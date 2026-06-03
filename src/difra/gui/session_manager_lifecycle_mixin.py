"""Session lifecycle operations for SessionManager."""

from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import h5py

from difra.gui.container_api import get_container_module
from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


class SessionManagerLifecycleMixin:
    """Session creation, opening, closing, and technical-container lookup."""

    def __init__(self, config: Optional[Dict] = None):
        """Initialize SessionManager.

        Args:
            config: Optional configuration dict from global.json
                   If provided, beam_energy_kev will be read from config
        """
        self.session_path: Optional[Path] = None
        self.session_id: Optional[str] = None
        self.sample_id: Optional[str] = None
        self.specimen_id: Optional[str] = None
        self.study_name: Optional[str] = None
        self.technical_container_path: Optional[Path] = None
        self.technical_container_id: Optional[str] = None
        self.session_state: str = self.SESSION_STATE_DRAFT

        # Track counters for linking
        self.i0_counter: Optional[int] = None  # Attenuation without sample
        self.i_counter: Optional[int] = None  # Attenuation with sample
        # Track in-progress point measurements for crash recovery metadata.
        self._pending_measurements: Dict[int, str] = {}

        # Store config for later use
        self.config = config or {}
        self.container_module = get_container_module(self.config)
        self.schema = self.container_module.schema
        self.writer = self.container_module.writer
        self.container_manager = self.container_module.container_manager
        self.producer_software: str = str(
            self.config.get("producer_software")
            or self.config.get("app_name")
            or "difra"
        )
        self.producer_version: str = str(
            self.config.get("producer_version")
            or getattr(self.container_module, "__version__", "unknown")
        )

        # Configuration - read from config or use defaults
        if config:
            self.operator_id: str = config.get("operator_id", "operator")
            self.site_id: str = config.get("site_id", "DIFRA_LAB")
            self.machine_name: str = self._resolve_machine_name(config)
            self.beam_energy_kev: float = self._resolve_beam_energy_kev(config)
        else:
            self.operator_id: str = "operator"
            self.site_id: str = "DIFRA_LAB"
            self.machine_name: str = "DIFRA-01"
            self.beam_energy_kev: float = self._resolve_beam_energy_kev({})

    def _find_locked_technical_container_for_distance(
        self,
        folder: Path,
        distance_cm: float,
        tolerance_cm: float = 0.5,
    ) -> Optional[Path]:
        """Find newest locked technical container matching distance."""
        folder = Path(folder)
        if not folder.exists():
            return None

        candidates = []
        seen = set()
        for pattern in ("technical_*.nxs.h5", "technical_*.h5"):
            for tech_path in folder.glob(pattern):
                if "archive" in tech_path.parts:
                    continue
                if not tech_path.is_file():
                    continue

                key = str(tech_path.resolve())
                if key in seen:
                    continue
                seen.add(key)

                try:
                    with h5py.File(tech_path, "r") as h5f:
                        file_distance = float(
                            h5f.attrs.get("distance_cm", float("nan"))
                        )
                except Exception:
                    continue

                if abs(file_distance - float(distance_cm)) > float(tolerance_cm):
                    continue
                if not self.container_manager.is_container_locked(tech_path):
                    continue

                candidates.append(tech_path)

        if not candidates:
            return None

        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    def create_session(
        self,
        folder: Path,
        distance_cm: float,
        technical_container_path: Optional[str] = None,
        **session_attrs,
    ) -> Tuple[str, Path]:
        """Create a new session container.

        All required session attributes should be provided as keyword arguments.
        These will be passed to the container writer and validated against self.schema.

        Required session attributes (from schema):
            sample_id: str - Unique sample identifier
            specimenId: str - Matador specimen identifier stored alongside sample_id
            study_name: str - Study name/identifier (optional, defaults to UNSPECIFIED)
            operator_id: str - Operator ID/name (optional, uses config default)
            site_id: str - Site identifier (optional, uses config default)
            machine_name: str - Machine name (optional, uses config default)
            beam_energy_keV: float - Beam energy (optional, uses config default)
            acquisition_date: str - Acquisition date (optional, auto-generated)

        Optional session attributes:
            patient_id: str - Patient identifier

        Args:
            folder: Directory for session container (measurements folder)
            distance_cm: Sample-detector distance (for technical container lookup)
            technical_container_path: Optional explicit technical container path
                (preferred when GUI has an active selected container)
            **session_attrs: All session attributes as keyword arguments

        Returns:
            Tuple of (session_id, session_path)

        Raises:
            RuntimeError: If no valid technical container found
            ValueError: If required session attributes are missing
        """
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)

        existing_sessions = sorted(
            [path for path in folder.glob("session_*.nxs.h5") if path.is_file()]
        )
        if existing_sessions:
            existing_names = "\n".join(
                f"• {path.name}" for path in existing_sessions[:3]
            )
            if len(existing_sessions) > 3:
                existing_names += f"\n• ... and {len(existing_sessions) - 3} more"
            raise RuntimeError(
                "A session container already exists in the measurements folder.\n\n"
                f"{existing_names}\n\n"
                "Close, send/archive, or explicitly clear the existing session container before creating a new one."
            )

        find_active_technical_container = (
            self.container_manager.find_active_technical_container
        )
        is_container_locked = self.container_manager.is_container_locked

        # Get technical folder from config
        technical_folder = self._get_technical_folder()

        # Prefer explicit active technical container from UI when provided.
        explicit_tech_path = str(technical_container_path or "").strip()
        if explicit_tech_path:
            tech_path = Path(explicit_tech_path)
            if not tech_path.exists():
                raise RuntimeError(
                    f"Selected technical container was not found: {tech_path}\n"
                    "Please load or create a technical container and try again."
                )
        else:
            # Find active technical container for this distance in technical folder
            tech_path = find_active_technical_container(
                folder=technical_folder,
                distance_cm=distance_cm,
            )

        if not tech_path:
            raise RuntimeError(
                f"No technical container found for distance {distance_cm} cm. "
                "Please create technical measurements first."
            )

        # If distance-based lookup returned an unlocked container while a locked one
        # exists for the same distance, automatically pick the locked candidate.
        if not explicit_tech_path and not is_container_locked(tech_path):
            locked_match = self._find_locked_technical_container_for_distance(
                folder=technical_folder,
                distance_cm=distance_cm,
            )
            if locked_match is not None and Path(locked_match) != Path(tech_path):
                logger.warning(
                    "Distance lookup returned unlocked technical container; using locked match instead",
                    requested_distance_cm=float(distance_cm),
                    unlocked_container=str(tech_path),
                    locked_container=str(locked_match),
                )
                tech_path = locked_match

        if not is_container_locked(tech_path):
            raise RuntimeError(
                f"Technical container is not locked: {tech_path}\n"
                "Please lock the technical container before creating sessions."
            )

        try:
            with h5py.File(tech_path, "r") as technical_file:
                technical_distance_cm = float(
                    technical_file.attrs[self.schema.ATTR_DISTANCE_CM]
                )
        except Exception as exc:
            raise RuntimeError(
                f"Technical container has no readable root distance_cm: {tech_path}"
            ) from exc

        try:
            requested_distance_cm = float(distance_cm)
            if abs(requested_distance_cm - technical_distance_cm) > 1e-6:
                logger.warning(
                    "Session distance overridden by selected technical container",
                    requested_distance_cm=requested_distance_cm,
                    technical_distance_cm=technical_distance_cm,
                    technical_container=str(tech_path),
                )
        except Exception:
            pass
        distance_cm = technical_distance_cm

        # Build session attributes from provided kwargs and config defaults
        # Required attributes from schema
        specimen_id = session_attrs.get("specimenId", session_attrs.get("specimen_id"))
        if specimen_id is None:
            specimen_id = session_attrs.get(
                self.schema.ATTR_SAMPLE_ID,
                session_attrs.get("sample_id"),
            )

        container_attrs = {
            self.schema.ATTR_SAMPLE_ID: session_attrs.get(
                self.schema.ATTR_SAMPLE_ID,
                session_attrs.get(
                    "sample_id", specimen_id
                ),  # Support both snake_case and schema names
            ),
            self.schema.ATTR_STUDY_NAME: session_attrs.get(
                self.schema.ATTR_STUDY_NAME,
                session_attrs.get("study_name", "UNSPECIFIED"),
            ),
            self.schema.ATTR_OPERATOR_ID: session_attrs.get(
                self.schema.ATTR_OPERATOR_ID,
                session_attrs.get("operator_id", self.operator_id),
            ),
            self.schema.ATTR_SITE_ID: session_attrs.get(
                self.schema.ATTR_SITE_ID,
                session_attrs.get("site_id", self.site_id),
            ),
            self.schema.ATTR_MACHINE_NAME: session_attrs.get(
                self.schema.ATTR_MACHINE_NAME,
                session_attrs.get("machine_name", self.machine_name),
            ),
            self.schema.ATTR_BEAM_ENERGY_KEV: session_attrs.get(
                self.schema.ATTR_BEAM_ENERGY_KEV,
                session_attrs.get("beam_energy_keV", self.beam_energy_kev),
            ),
            self.schema.ATTR_ACQUISITION_DATE: session_attrs.get(
                self.schema.ATTR_ACQUISITION_DATE,
                session_attrs.get(
                    "acquisition_date", datetime.now().strftime("%Y-%m-%d")
                ),
            ),
        }

        if hasattr(self.schema, "ATTR_PROJECT_ID"):
            project_attr = self.schema.ATTR_PROJECT_ID
            container_attrs[project_attr] = session_attrs.get(
                project_attr,
                session_attrs.get(
                    "project_id", container_attrs[self.schema.ATTR_STUDY_NAME]
                ),
            )

        # Add optional attributes if provided
        if (
            self.schema.ATTR_PATIENT_ID in session_attrs
            or "patient_id" in session_attrs
        ):
            container_attrs[self.schema.ATTR_PATIENT_ID] = session_attrs.get(
                self.schema.ATTR_PATIENT_ID,
                session_attrs.get("patient_id"),
            )

        # Validate required sample_id
        if not container_attrs[self.schema.ATTR_SAMPLE_ID]:
            raise ValueError("sample_id is required to create a session")

        sample_id = container_attrs[self.schema.ATTR_SAMPLE_ID]
        study_name = container_attrs[self.schema.ATTR_STUDY_NAME]

        logger.info(
            "Creating new session",
            sample_id=sample_id,
            distance_cm=distance_cm,
            technical_container=str(tech_path),
            study_name=study_name,
            operator_id=container_attrs.get(self.schema.ATTR_OPERATOR_ID),
            site_id=container_attrs.get(self.schema.ATTR_SITE_ID),
            machine_name=container_attrs.get(self.schema.ATTR_MACHINE_NAME),
        )

        # Create session container with schema-driven attributes
        self.session_id, session_path_str = self.writer.create_session_container(
            folder=folder,
            producer_software=self.producer_software,
            producer_version=self.producer_version,
            **container_attrs,
        )

        self.session_path = Path(session_path_str)
        self.sample_id = sample_id
        self.study_name = study_name
        self.technical_container_path = Path(tech_path)

        # Copy technical data to session
        self.writer.copy_technical_to_session(
            technical_file=tech_path,
            session_file=self.session_path,
        )
        specimen_text = self._as_text(specimen_id, sample_id)
        self.specimen_id = specimen_text
        technical_container_id = self._read_h5_text_attr(
            Path(tech_path),
            self.schema.ATTR_CONTAINER_ID,
            "",
        ).strip()
        self.technical_container_id = technical_container_id or None
        extra_attrs = {
            "specimenId": specimen_text,
            "distance_cm": float(distance_cm),
        }
        study_id = session_attrs.get(
            "matadorStudyId", session_attrs.get("matador_study_id")
        )
        machine_id = session_attrs.get(
            "matadorMachineId",
            session_attrs.get("matador_machine_id"),
        )
        project_id = session_attrs.get(
            "matadorProjectId",
            session_attrs.get("matador_project_id"),
        )
        project_name = session_attrs.get(
            "matadorProjectName",
            session_attrs.get("matador_project_name"),
        )
        if study_id not in (None, ""):
            extra_attrs["matadorStudyId"] = int(study_id)
        if machine_id not in (None, ""):
            extra_attrs["matadorMachineId"] = int(machine_id)
        if project_id not in (None, ""):
            try:
                extra_attrs["matadorProjectId"] = int(project_id)
            except Exception:
                logger.warning(
                    "Ignoring non-integer Matador project id during session creation",
                    project_id=project_id,
                )
        if project_name not in (None, ""):
            extra_attrs["matadorProjectName"] = self._as_text(project_name)
        try:
            with h5py.File(self.session_path, "a") as h5f:
                for key, value in extra_attrs.items():
                    h5f.attrs[key] = value
                sample_group = h5f.get(self.schema.GROUP_SAMPLE)
                if sample_group is not None:
                    sample_group.attrs["specimenId"] = specimen_text
        except Exception:
            logger.warning(
                "Failed to persist extra specimen/Matador attrs into session container",
                session_path=str(self.session_path),
                exc_info=True,
            )
        self.log_event(
            message="Technical snapshot copied into session",
            event_type="technical_snapshot_copied",
            details={"technical_container": str(tech_path)},
        )
        self._set_session_state(
            self.SESSION_STATE_DRAFT,
            reason="session_created",
        )

        logger.info(
            "Session created successfully",
            session_id=self.session_id,
            session_path=str(self.session_path),
        )

        # Reset counters
        self.i0_counter = None
        self.i_counter = None
        self._pending_measurements = {}

        return self.session_id, self.session_path

    def close_session(self):
        """Close the current session and clear state."""
        if self.session_path:
            logger.info(
                "Closing session",
                session_id=self.session_id,
                sample_id=self.sample_id,
            )

        self.session_path = None
        self.session_id = None
        self.sample_id = None
        self.specimen_id = None
        self.study_name = None
        self.technical_container_path = None
        self.technical_container_id = None
        self.i0_counter = None
        self.i_counter = None
        self._pending_measurements = {}
        self.session_state = self.SESSION_STATE_DRAFT

    def open_existing_session(self, session_file: Path) -> Dict:
        """Load metadata from an existing session container into manager state."""
        import h5py

        session_file = Path(session_file)
        if not session_file.exists():
            raise FileNotFoundError(f"Session container not found: {session_file}")

        with h5py.File(session_file, "r") as f:
            self.session_path = session_file
            sample_group = f.get(self.schema.GROUP_SAMPLE)
            user_group = f.get(self.schema.GROUP_USER)
            calibration_snapshot = f.get(self.schema.GROUP_CALIBRATION_SNAPSHOT)

            self.sample_id = self._read_specimen_id(
                {
                    "specimenId": f.attrs.get("specimenId"),
                    "sample_id": f.attrs.get(
                        self.schema.ATTR_SAMPLE_ID,
                        sample_group.attrs.get(self.schema.ATTR_SAMPLE_ID)
                        if sample_group
                        else None,
                    ),
                },
                fallback="unknown",
            )
            self.specimen_id = self.sample_id
            self.study_name = self._as_text(
                f.attrs.get(
                    self.schema.ATTR_STUDY_NAME,
                    sample_group.attrs.get(self.schema.ATTR_STUDY_NAME)
                    if sample_group
                    else None,
                ),
                "UNSPECIFIED",
            )
            self.session_id = self._as_text(
                f.attrs.get(self.schema.ATTR_SESSION_ID),
                "unknown",
            )
            self.operator_id = self._as_text(
                f.attrs.get(
                    self.schema.ATTR_OPERATOR_ID,
                    user_group.attrs.get(self.schema.ATTR_OPERATOR_ID)
                    if user_group
                    else None,
                ),
                self.operator_id,
            )
            self.machine_name = self._as_text(
                f.attrs.get(
                    self.schema.ATTR_MACHINE_NAME,
                    user_group.attrs.get(self.schema.ATTR_MACHINE_NAME)
                    if user_group
                    else None,
                ),
                self.machine_name,
            )

            if calibration_snapshot is not None:
                source = calibration_snapshot.attrs.get("source_file")
                source_container_id = calibration_snapshot.attrs.get(
                    "source_container_id"
                )
                self.technical_container_path = Path(source) if source else None
                self.technical_container_id = (
                    self._as_text(source_container_id, "").strip() or None
                )
            else:
                self.technical_container_path = None
                self.technical_container_id = None

            self._restore_attenuation_counters_from_h5(f)
            self.session_state = self._infer_session_state_from_h5(f)

        # Rebuild pending map from in-progress measurements for crash recovery.
        incomplete = self._load_incomplete_measurements_from_container(session_file)
        self._pending_measurements = {
            item["point_index"]: item["measurement_path"] for item in incomplete
        }

        return self.get_session_info()
