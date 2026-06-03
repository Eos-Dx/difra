"""Common SessionManager helpers."""

from pathlib import Path
from typing import Dict, Optional

from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


DEFAULT_BEAM_ENERGY_KEV = 8.04


class SessionManagerCommonMixin:
    """Shared utility helpers for session containers."""

    @staticmethod
    def _resolve_machine_name(config: Dict) -> str:
        """Resolve machine name from explicit field or selected setup identity."""
        return (
            config.get("machine_name")
            or config.get("setup_name")
            or config.get("name")
            or config.get("default_setup")
            or "DIFRA-01"
        )

    @staticmethod
    def _as_text(value, default: str = "") -> str:
        if value is None:
            return default
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _safe_int(value):
        try:
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _resolve_beam_energy_kev(config: Optional[Dict]) -> float:
        config = config or {}
        for key in (
            "beam_energy_keV",
            "beam_energy_kev",
            "xray_energy_kev",
            "energy_kev",
        ):
            if key not in config:
                continue
            try:
                value = float(config[key])
            except Exception as exc:
                raise ValueError(f"Invalid {key}: {config[key]!r}") from exc
            if value <= 0:
                raise ValueError(f"Invalid {key}: {config[key]!r}")
            return value

        logger.warning(
            "Beam energy missing from config; using default %.2f keV",
            DEFAULT_BEAM_ENERGY_KEV,
        )
        return DEFAULT_BEAM_ENERGY_KEV

    @staticmethod
    def _read_specimen_id(attrs, *, fallback: str = "") -> str:
        specimen = attrs.get("specimenId")
        if specimen is not None:
            return SessionManagerCommonMixin._as_text(specimen, fallback)
        sample = attrs.get("sample_id")
        if sample is not None:
            return SessionManagerCommonMixin._as_text(sample, fallback)
        return fallback

    @staticmethod
    def _counter_from_measurement_name(name: str):
        try:
            return int(str(name).split("_")[-1])
        except Exception:
            return None

    def _restore_attenuation_counters_from_h5(self, h5f) -> None:
        """Restore attenuation counters from analytical measurements in an existing session."""
        self.i0_counter = None
        self.i_counter = None

        ana_group_path = getattr(
            self.schema, "GROUP_ANALYTICAL_MEASUREMENTS", "/analytical_measurements"
        )
        ana_group = h5f.get(ana_group_path)
        if ana_group is None:
            return

        role_attr_name = getattr(self.schema, "ATTR_ANALYSIS_ROLE", "analysis_role")
        type_attr_name = getattr(self.schema, "ATTR_ANALYSIS_TYPE", "analysis_type")
        counter_attr_name = getattr(
            self.schema, "ATTR_MEASUREMENT_COUNTER", "measurement_counter"
        )
        attenuation_type = (
            str(getattr(self.schema, "ANALYSIS_TYPE_ATTENUATION", "attenuation"))
            .strip()
            .lower()
        )
        role_i0 = str(getattr(self.schema, "ANALYSIS_ROLE_I0", "i0")).strip().lower()
        role_i = str(getattr(self.schema, "ANALYSIS_ROLE_I", "i")).strip().lower()

        i0_candidates = []
        i_candidates = []

        for ana_id in sorted(ana_group.keys()):
            ana_group_item = ana_group[ana_id]
            counter = self._safe_int(ana_group_item.attrs.get(counter_attr_name))
            if counter is None:
                counter = self._counter_from_measurement_name(str(ana_id))
            if counter is None:
                continue

            analysis_type = (
                self._as_text(ana_group_item.attrs.get(type_attr_name), "")
                .strip()
                .lower()
            )
            analysis_role = (
                self._as_text(ana_group_item.attrs.get(role_attr_name), "")
                .strip()
                .lower()
            )

            is_attenuation = (
                analysis_type == attenuation_type
                or analysis_type.startswith("attenuation")
                or analysis_role in {role_i0, role_i}
            )
            if not is_attenuation:
                continue

            is_i0 = analysis_role in {
                role_i0,
                "without",
                "without_sample",
            } or analysis_type in {
                "attenuation_i0",
                "attenuation_without",
                "attenuation_without_sample",
            }
            is_i = analysis_role in {
                role_i,
                "with",
                "with_sample",
            } or analysis_type in {
                "attenuation_i",
                "attenuation_with",
                "attenuation_with_sample",
            }

            if is_i0:
                i0_candidates.append(counter)
                continue
            if is_i:
                i_candidates.append(counter)
                continue

            # Legacy fallback (no explicit role): first attenuation is I0, later ones are I.
            if not i0_candidates:
                i0_candidates.append(counter)
            else:
                i_candidates.append(counter)

        if i0_candidates:
            self.i0_counter = max(i0_candidates)
        if i_candidates:
            self.i_counter = max(i_candidates)

        if self.i0_counter is not None or self.i_counter is not None:
            logger.info(
                "Restored attenuation counters from existing session",
                session_path=str(self.session_path),
                i0_counter=self.i0_counter,
                i_counter=self.i_counter,
            )

    def _get_technical_folder(self) -> Path:
        """Get technical container folder from config.

        Returns:
            Path to technical folder from config
        """
        # Try technical_folder from config first
        folder = self.config.get("technical_folder")
        if folder:
            return Path(folder)

        # Fall back to difra_base_folder/technical
        base = self.config.get("difra_base_folder")
        if base:
            return Path(base) / "technical"

        # Last resort: home directory
        logger.warning("No technical folder in config, using default")
        return Path.home() / "difra_technical"

    def is_session_active(self) -> bool:
        """Check if a session is currently active."""
        return self.session_path is not None and self.session_path.exists()

    def _check_active(self):
        """Check if session is active, raise if not."""
        if not self.is_session_active():
            raise RuntimeError(
                "No active session. Please create a session first using create_session()."
            )

    def is_locked(self) -> bool:
        """Check if the current session container is locked.

        Returns:
            True if locked, False if unlocked or no active session
        """
        if not self.is_session_active():
            return False

        return self.container_manager.is_container_locked(self.session_path)
