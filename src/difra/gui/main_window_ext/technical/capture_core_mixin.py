import logging
from pathlib import Path

from difra.gui.container_api import get_schema

logger = logging.getLogger(__name__)


class TechnicalCaptureCoreMixin:
    @staticmethod
    def _format_distance_token_cm(distance_cm) -> str:
        try:
            value = float(distance_cm)
        except (TypeError, ValueError):
            return "unknowncm"
        if abs(value - round(value)) < 1e-6:
            return f"{int(round(value))}cm"
        token = f"{value:.6f}".rstrip("0").rstrip(".")
        token = token.replace("-", "m").replace(".", "p")
        return f"{token}cm"
    def _technical_capture_distance_token(self) -> str:
        active_path = None
        active_getter = getattr(self, "_active_technical_container_path_obj", None)
        if callable(active_getter):
            try:
                active_path = active_getter()
            except Exception:
                active_path = None
        if active_path is None:
            raw_active_path = str(
                getattr(self, "_active_technical_container_path", "") or ""
            ).strip()
            active_path = Path(raw_active_path) if raw_active_path else None
        if active_path is not None:
            try:
                import h5py

                with h5py.File(active_path, "r") as h5f:
                    token = self._format_distance_token_cm(h5f.attrs.get("distance_cm"))
                    if token != "unknowncm":
                        return token
            except Exception:
                logger.debug(
                    "Failed to read active technical container distance from %s",
                    active_path,
                    exc_info=True,
                )

        distances = getattr(self, "_detector_distances", {}) or {}
        for value in distances.values():
            token = self._format_distance_token_cm(value)
            if token != "unknowncm":
                return token
        standard_distances = getattr(self, "config", {}).get("standard_distances", {}) or {}
        if isinstance(standard_distances, dict):
            for value in standard_distances.values():
                token = self._format_distance_token_cm(value)
                if token != "unknowncm":
                    return token
        return "unknowncm"
    def _technical_capture_order_token(self, typ: str, count: int) -> str:
        key = str(typ or "").strip().upper()
        return self.TECHNICAL_TYPE_ORDER.get(key, f"{int(count):03d}")
    def _technical_capture_base_stem(
        self,
        *,
        typ: str,
        count: int,
        timestamp_token: str,
        integration_time_s: float,
        frames: int,
    ) -> str:
        base = self._file_base(typ)
        distance_token = self._technical_capture_distance_token()
        order_token = self._technical_capture_order_token(typ, count)
        time_token = f"{float(integration_time_s):.6f}s"
        return (
            f"{base}_{distance_token}_{order_token}_{timestamp_token}_"
            f"{time_token}_{int(frames)}frames"
        )
    @staticmethod
    def _normalize_technical_alias_candidates(alias: str | None):
        token = str(alias or "").strip().upper()
        if not token:
            return set()
        if token.startswith("PONI_"):
            token = token[5:]
        if not token:
            return set()
        candidates = {token}
        bare = token[4:] if token.startswith("DET_") else token
        if bare:
            candidates.add(bare)
            candidates.add(f"DET_{bare}")
        mapping = {
            "PRIMARY": "SAXS",
            "SAXS": "PRIMARY",
            "SECONDARY": "WAXS",
            "WAXS": "SECONDARY",
        }
        detector_groups = (
            {"PRIMARY", "SAXS", "DET_PRIMARY", "DET_SAXS"},
            {"SECONDARY", "WAXS", "DET_SECONDARY", "DET_WAXS"},
        )
        if bare in mapping:
            candidates.add(mapping[bare])
        for group in detector_groups:
            bare_group = {
                value[4:] if value.startswith("DET_") else value for value in group
            }
            if token in group or bare in bare_group:
                candidates.update(group)
                candidates.update(bare_group)
        return {value for value in candidates if value}
    def _resolve_technical_measurement_poni(
        self,
        *,
        alias: str | None,
        source_ref: str = "",
        source_info: dict | None = None,
    ) -> str | None:
        detector_context = self._read_technical_measurement_container_context(
            source_ref=source_ref,
            source_info=source_info,
        )
        direct_poni_text = str(detector_context.get("poni_text") or "").strip()
        if direct_poni_text:
            return direct_poni_text

        container_path = ""
        raw_source_ref = str(source_ref or "").strip()
        source_payload = source_info if isinstance(source_info, dict) else {}
        if raw_source_ref.startswith("h5ref://"):
            payload = raw_source_ref[len("h5ref://") :]
            container_path = payload.partition("#")[0]
        if not container_path:
            container_path = str(source_payload.get("container_path") or "").strip()
        if not container_path:
            active_getter = getattr(self, "_active_technical_container_path_obj", None)
            if callable(active_getter):
                active_path = active_getter()
                if active_path is not None:
                    container_path = str(active_path)
        if not container_path:
            container_path = str(getattr(self, "_active_technical_container_path", "") or "").strip()
        if not container_path:
            return None

        collect = getattr(self, "_collect_container_poni_text_by_alias", None)
        if not callable(collect):
            return None
        try:
            poni_by_alias = collect(Path(container_path)) or {}
        except Exception:
            logger.debug("Failed to collect PONI from technical container %s", container_path, exc_info=True)
            return None
        alias_candidates = self._normalize_technical_alias_candidates(alias)
        alias_candidates.update(
            self._normalize_technical_alias_candidates(detector_context.get("detector_alias"))
        )
        alias_candidates.update(
            self._normalize_technical_alias_candidates(detector_context.get("detector_id"))
        )
        for key, text in (poni_by_alias or {}).items():
            key_candidates = self._normalize_technical_alias_candidates(key)
            if alias_candidates & key_candidates and str(text or "").strip():
                return str(text).strip()
        return None
    def _read_technical_measurement_container_context(
        self,
        *,
        source_ref: str = "",
        source_info: dict | None = None,
    ) -> dict:
        raw_source_ref = str(source_ref or "").strip()
        source_payload = source_info if isinstance(source_info, dict) else {}
        container_path = ""
        dataset_path = ""

        if raw_source_ref.startswith("h5ref://"):
            payload = raw_source_ref[len("h5ref://") :]
            container_path, _sep, dataset_path = payload.partition("#")

        if not container_path:
            container_path = str(source_payload.get("container_path") or "").strip()
        if not dataset_path:
            dataset_path = str(source_payload.get("dataset_path") or "").strip()

        if not container_path or not dataset_path:
            return {}

        try:
            import h5py
        except Exception:
            logger.debug("h5py unavailable while reading technical measurement context", exc_info=True)
            return {}

        try:
            with h5py.File(container_path, "r") as h5f:
                if dataset_path not in h5f:
                    return {}

                dataset = h5f[dataset_path]
                detector_group = dataset.parent
                schema = get_schema(self.config if hasattr(self, "config") else None)
                context = {
                    "detector_alias": self._decode_technical_h5_attr(
                        detector_group.attrs.get(
                            getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias"),
                            "",
                        )
                    ),
                    "detector_id": self._decode_technical_h5_attr(
                        detector_group.attrs.get(
                            getattr(schema, "ATTR_DETECTOR_ID", "detector_id"),
                            "",
                        )
                    ),
                    "poni_text": "",
                }

                candidate_paths = []

                attr_poni_ref = getattr(schema, "ATTR_PONI_REF", "poni_ref")
                for attr_name in (attr_poni_ref, "poni_path"):
                    ref_path = self._decode_technical_h5_attr(
                        detector_group.attrs.get(attr_name, "")
                    ).strip()
                    if ref_path and ref_path not in candidate_paths:
                        candidate_paths.append(ref_path)

                role_name = str(detector_group.name.rsplit("/", 1)[-1] or "").strip()
                if role_name.startswith("det_"):
                    technical_poni_group = getattr(
                        schema,
                        "GROUP_TECHNICAL_PONI",
                        "/entry/technical/poni",
                    )
                    for suffix in (role_name[4:], role_name):
                        canonical_path = f"{technical_poni_group}/poni_{suffix}"
                        if canonical_path not in candidate_paths:
                            candidate_paths.append(canonical_path)

                if not role_name.startswith("det_"):
                    format_detector_role = getattr(schema, "format_detector_role", None)
                    if callable(format_detector_role):
                        for candidate in (
                            context["detector_alias"],
                            context["detector_id"],
                        ):
                            try:
                                role = str(format_detector_role(candidate) or "").strip()
                            except Exception:
                                role = ""
                            if role.startswith("det_"):
                                technical_poni_group = getattr(
                                    schema,
                                    "GROUP_TECHNICAL_PONI",
                                    "/entry/technical/poni",
                                )
                                for suffix in (role[4:], role):
                                    canonical_path = f"{technical_poni_group}/poni_{suffix}"
                                    if canonical_path not in candidate_paths:
                                        candidate_paths.append(canonical_path)

                for ref_path in candidate_paths:
                    if ref_path and ref_path in h5f:
                        try:
                            value = h5f[ref_path][()]
                            context["poni_text"] = self._decode_technical_h5_attr(value).strip()
                            if context["poni_text"]:
                                context["poni_path"] = ref_path
                                break
                        except Exception:
                            logger.debug(
                                "Failed reading detector-linked technical PONI %s from %s",
                                ref_path,
                                container_path,
                                exc_info=True,
                            )
                return context
        except Exception:
            logger.debug(
                "Failed to read technical measurement context from %s#%s",
                container_path,
                dataset_path,
                exc_info=True,
            )
            return {}
    @staticmethod
    def _decode_technical_h5_attr(value) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value or "")
    def _resolve_technical_measurement_mask(
        self,
        *,
        alias: str | None,
        source_ref: str = "",
        source_info: dict | None = None,
    ):
        masks = getattr(self, "masks", None)
        if not isinstance(masks, dict) or not masks:
            return None

        detector_context = self._read_technical_measurement_container_context(
            source_ref=source_ref,
            source_info=source_info,
        )
        alias_candidates = []
        for candidate in (
            alias,
            detector_context.get("detector_alias"),
            detector_context.get("detector_id"),
        ):
            for normalized in sorted(self._normalize_technical_alias_candidates(candidate)):
                if normalized not in alias_candidates:
                    alias_candidates.append(normalized)

        for key in alias_candidates:
            if key in masks:
                return masks.get(key)
        return masks.get(alias)
