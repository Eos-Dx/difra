"""Technical H5 locking responsibilities: H5LockingPoniValidationMixin."""

from difra.gui.main_window_ext.technical.h5_locking_common import *


class H5LockingPoniValidationMixin:
    def _container_has_poni_datasets(self, container_path: Path) -> bool:
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        try:
            with h5py.File(container_path, "r") as h5f:
                poni_group = h5f.get(schema.GROUP_TECHNICAL_PONI)
                if poni_group is None:
                    return False
                return any(str(name).startswith("poni_") for name in poni_group.keys())
        except Exception:
            return False

    def _collect_container_poni_text_by_alias(self, container_path: Path):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        poni_by_alias = {}

        with h5py.File(container_path, "r") as h5f:
            poni_group = h5f.get(schema.GROUP_TECHNICAL_PONI)
            if poni_group is None:
                return {}

            for ds_name in sorted(poni_group.keys()):
                try:
                    ds = poni_group[ds_name]
                    alias = ds.attrs.get(schema.ATTR_DETECTOR_ALIAS, "")
                    detector_id = ds.attrs.get(
                        getattr(schema, "ATTR_DETECTOR_ID", "detector_id"),
                        "",
                    )
                    alias_key, _detector_cfg_id, alias_candidates = (
                        self._resolve_configured_technical_alias(
                            alias,
                            detector_id,
                            str(ds_name),
                        )
                    )

                    value = ds[()]
                    if isinstance(value, bytes):
                        text = value.decode("utf-8", errors="replace")
                    else:
                        text = str(value)
                    for candidate in [alias_key, *sorted(alias_candidates)]:
                        key = str(candidate or "").strip().upper()
                        if key:
                            poni_by_alias[key] = text
                except Exception:
                    logger.warning(
                        "Failed to parse PONI dataset while validating centers: %s",
                        ds_name,
                        exc_info=True,
                    )

        return poni_by_alias

    @staticmethod
    def _parse_poni_distance_cm(poni_text: str):
        return parse_poni_distance_cm(poni_text)

    def _poni_distance_validation_config(self):
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_distance_validation", {})
        if not isinstance(validation_cfg, dict):
            validation_cfg = {}
        if bool(cfg.get("DEV", False)) and not bool(validation_cfg.get("apply_in_dev_mode", False)):
            return {}
        return validation_cfg

    def _agbh_peak_qc_config(self):
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("agbh_peak_qc", {})
        if not isinstance(validation_cfg, dict) or not bool(validation_cfg.get("enabled", False)):
            return {}
        if bool(cfg.get("DEV", False)) and not bool(validation_cfg.get("apply_in_dev_mode", False)):
            return {}
        return validation_cfg

    def _embedded_agbh_peak_qc_warnings(self, container_path: Path):
        validation_cfg = self._agbh_peak_qc_config()
        if not validation_cfg:
            return []
        try:
            return evaluate_agbh_peak_qc_for_h5(
                Path(container_path),
                schema=get_schema(self.config if hasattr(self, "config") else None),
                validation_config=validation_cfg,
            )
        except Exception as exc:
            logger.debug("AgBH peak QC failed for %s: %s", container_path, exc, exc_info=True)
            return []

    def _embedded_poni_distance_validation_errors(
        self,
        container_path: Path,
        *,
        tolerance_percent: float = 5.0,
    ):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        errors = []

        try:
            with h5py.File(container_path, "r") as h5f:
                root_distance = self._to_float_or_none(
                    h5f.attrs.get(schema.ATTR_DISTANCE_CM)
                )
                distances_by_alias = {}

                technical_group = h5f.get(schema.GROUP_TECHNICAL)
                if technical_group is not None:
                    for event_name in sorted(technical_group.keys()):
                        event_group = technical_group[event_name]
                        if not hasattr(event_group, "keys"):
                            continue
                        for detector_name in sorted(event_group.keys()):
                            detector_group = event_group[detector_name]
                            if not hasattr(detector_group, "attrs"):
                                continue
                            distance_cm = self._to_float_or_none(
                                detector_group.attrs.get(schema.ATTR_DISTANCE_CM)
                            )
                            if distance_cm is None:
                                continue
                            alias, detector_id, alias_candidates = (
                                self._resolve_configured_technical_alias(
                                    detector_group.attrs.get(schema.ATTR_DETECTOR_ALIAS, ""),
                                    detector_group.attrs.get(
                                        getattr(schema, "ATTR_DETECTOR_ID", "detector_id"),
                                        "",
                                    ),
                                    detector_name,
                                )
                            )
                            for candidate in [alias, detector_id, *sorted(alias_candidates)]:
                                key = str(candidate or "").strip().upper()
                                if key:
                                    distances_by_alias[key] = distance_cm

                poni_group = h5f.get(schema.GROUP_TECHNICAL_PONI)
                if poni_group is None:
                    return errors

                poni_text_by_alias = {}
                poni_name_by_alias = {}
                for ds_name in sorted(poni_group.keys()):
                    ds = poni_group[ds_name]
                    value = ds[()]
                    if isinstance(value, bytes):
                        poni_text = value.decode("utf-8", errors="replace")
                    else:
                        poni_text = str(value)

                    alias, detector_id, alias_candidates = (
                        self._resolve_configured_technical_alias(
                            ds.attrs.get(schema.ATTR_DETECTOR_ALIAS, ""),
                            ds.attrs.get(getattr(schema, "ATTR_DETECTOR_ID", "detector_id"), ""),
                            ds_name,
                        )
                    )
                    expected_cm = None
                    for candidate in [alias, detector_id, *sorted(alias_candidates)]:
                        key = str(candidate or "").strip().upper()
                        if key in distances_by_alias:
                            expected_cm = distances_by_alias[key]
                            break
                    if expected_cm is None:
                        expected_cm = root_distance

                    label = str(alias or ds_name).strip() or str(ds_name)
                    if expected_cm is None:
                        continue
                    poni_text_by_alias[label] = poni_text
                    poni_name_by_alias[label] = "embedded PONI"
                    distances_by_alias[label] = expected_cm

                cfg = dict(self._poni_distance_validation_config() or {})
                if "tolerance_percent" not in cfg and "default_tolerance_percent" not in cfg:
                    cfg["tolerance_percent"] = float(tolerance_percent)
                errors.extend(
                    validate_poni_distances(
                        poni_text_by_alias=poni_text_by_alias,
                        distances_by_alias=distances_by_alias,
                        poni_name_by_alias=poni_name_by_alias,
                        validation_config=cfg,
                    )
                )
        except Exception as exc:
            errors.append(f"Failed to validate embedded PONI distances: {exc}")

        return errors

    def _poni_metadata_validation_config(self):
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_metadata_validation", {})
        if not isinstance(validation_cfg, dict) or not bool(validation_cfg.get("enabled", False)):
            return {}
        if bool(cfg.get("DEV", False)) and not bool(validation_cfg.get("apply_in_dev_mode", False)):
            return {}
        return validation_cfg

    def _embedded_poni_metadata_validation_errors(self, container_path: Path):
        validation_cfg = self._poni_metadata_validation_config()
        if not validation_cfg:
            return []

        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        poni_text_by_alias = {}
        errors = []

        try:
            with h5py.File(container_path, "r") as h5f:
                poni_group = h5f.get(schema.GROUP_TECHNICAL_PONI)
                if poni_group is None:
                    return errors
                for ds_name in sorted(poni_group.keys()):
                    ds = poni_group[ds_name]
                    value = ds[()]
                    if isinstance(value, bytes):
                        poni_text = value.decode("utf-8", errors="replace")
                    else:
                        poni_text = str(value)
                    alias, detector_id, alias_candidates = (
                        self._resolve_configured_technical_alias(
                            ds.attrs.get(schema.ATTR_DETECTOR_ALIAS, ""),
                            ds.attrs.get(getattr(schema, "ATTR_DETECTOR_ID", "detector_id"), ""),
                            ds_name,
                        )
                    )
                    label = str(alias or detector_id or ds_name).strip() or str(ds_name)
                    poni_text_by_alias[label] = poni_text
        except Exception as exc:
            return [f"Failed to validate embedded PONI metadata: {exc}"]

        return validate_poni_metadata(
            poni_text_by_alias=poni_text_by_alias,
            validation_config=validation_cfg,
        )

    def _detector_sizes_by_alias(self):
        sizes = {}
        for detector_cfg in self.config.get("detectors", []) if hasattr(self, "config") else []:
            alias = str(detector_cfg.get("alias") or "").strip()
            if not alias:
                continue
            size_cfg = detector_cfg.get("size", {})
            if isinstance(size_cfg, dict):
                width = size_cfg.get("width", 256)
                height = size_cfg.get("height", 256)
            else:
                width = 256
                height = 256
            try:
                sizes[alias] = (int(width), int(height))
            except Exception:
                sizes[alias] = (256, 256)
        detector_cfgs = self.config.get("detectors", []) if hasattr(self, "config") else []
        return normalize_alias_mapping_to_rule_aliases(sizes, detector_cfgs)

    def _validate_poni_centers_for_container(self, container_path: Path):
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_center_validation", {})
        if not isinstance(validation_cfg, dict) or not validation_cfg.get("enabled", False):
            return [], []

        if bool(cfg.get("DEV", False)) and not bool(
            validation_cfg.get("apply_in_dev_mode", False)
        ):
            return [], []

        try:
            poni_text_by_alias = self._collect_container_poni_text_by_alias(container_path)
        except Exception as exc:
            return [f"PONI center validation failed while reading container: {exc}"], []

        detector_cfgs = cfg.get("detectors", [])
        poni_text_by_alias = normalize_alias_mapping_to_rule_aliases(
            poni_text_by_alias,
            detector_cfgs,
        )
        detector_sizes = self._detector_sizes_by_alias()
        return validate_poni_centers(
            poni_text_by_alias=poni_text_by_alias,
            detector_sizes_by_alias=detector_sizes,
            validation_config=validation_cfg,
        )

