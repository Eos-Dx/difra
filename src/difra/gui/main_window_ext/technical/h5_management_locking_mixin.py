"""Technical H5 validation/locking responsibilities."""

from . import h5_management_mixin as _module
from .poni_center_validation import parse_poni_center_px, validate_poni_centers

os = _module.os
logger = _module.logger
QInputDialog = _module.QInputDialog
QMessageBox = _module.QMessageBox
QFileDialog = _module.QFileDialog
Path = _module.Path
shutil = _module.shutil
time = _module.time
get_container_manager = _module.get_container_manager
get_technical_validator = _module.get_technical_validator
get_schema = _module.get_schema

from difra.gui.main_window_ext.technical import h5_management_lock_actions


class H5ManagementLockingMixin:
    PONI_REVIEW_STATUS_ATTR = "poni_center_review_status"
    PONI_REVIEW_USER_ATTR = "poni_center_review_user"
    PONI_REVIEW_TS_ATTR = "poni_center_review_timestamp"
    PONI_REVIEW_IN_ZONE_ATTR = "poni_center_in_allowed_zone"
    PONI_REVIEW_NOTES_ATTR = "poni_center_review_notes"

    @staticmethod
    def _to_float_or_none(value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _decode_attr_text(value, default: str = "") -> str:
        if value is None:
            return default
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _sync_lock_action_overrides():
        """Mirror monkeypatchable module globals into extracted helper actions."""
        h5_management_lock_actions.QMessageBox = QMessageBox
        h5_management_lock_actions.QInputDialog = QInputDialog
        h5_management_lock_actions.get_container_manager = get_container_manager

    @staticmethod
    def _build_fake_poni_content(
        alias: str,
        distance_cm: float = 17.0,
        detector_size=(256, 256),
        pixel_size_um=(55.0, 55.0),
        center_px=None,
    ) -> str:
        """Create deterministic fake PONI content for demo mode."""
        try:
            width = int(detector_size[0])
            height = int(detector_size[1])
        except Exception:
            width, height = 256, 256

        try:
            pixel1_um = float(pixel_size_um[0])
        except Exception:
            pixel1_um = 55.0
        try:
            pixel2_um = float(pixel_size_um[1])
        except Exception:
            pixel2_um = pixel1_um

        distance_m = max(float(distance_cm), 0.0) / 100.0
        pixel1 = pixel1_um * 1e-6
        pixel2 = pixel2_um * 1e-6

        if isinstance(center_px, (list, tuple)) and len(center_px) >= 2:
            try:
                row_px = float(center_px[0])
                col_px = float(center_px[1])
            except Exception:
                row_px = float(height) / 2.0
                col_px = float(width) / 2.0
            poni1 = row_px * pixel1
            poni2 = col_px * pixel2
        else:
            # Stable pseudo-variation per detector alias.
            alias_seed = abs(hash(str(alias))) % 1000
            poni1 = 0.006 + (alias_seed % 150) / 100000.0
            poni2 = 0.002 + (alias_seed % 120) / 100000.0

        return (
            "# Auto-generated fake PONI (DEMO mode)\n"
            "poni_version: 2.1\n"
            "Detector: Detector\n"
            f'Detector_config: {{"pixel1": {pixel1}, "pixel2": {pixel2}, '
            f'"max_shape": [{height}, {width}], "orientation": 3}}\n'
            f"Distance: {distance_m:.6f}\n"
            f"Poni1: {poni1:.6f}\n"
            f"Poni2: {poni2:.6f}\n"
            "Rot1: 0\n"
            "Rot2: 0\n"
            "Rot3: 0\n"
            "Wavelength: 1.5406e-10\n"
            f"# Detector alias: {alias}\n"
        )

    def _resolve_demo_poni_center_px(self, alias: str, detector_size=(256, 256)):
        """Resolve demo center in pixels from main.json center rules when available."""
        alias_key = str(alias or "").strip().upper()
        # Fixed demo targets requested by workflow for fake PRIMARY/SECONDARY.
        if alias_key == "PRIMARY":
            return 128.0, 8.0
        if alias_key == "SECONDARY":
            return 128.0, 280.0

        try:
            width = float(detector_size[0])
            height = float(detector_size[1])
        except Exception:
            width, height = 256.0, 256.0

        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_center_validation", {})
        if not isinstance(validation_cfg, dict):
            validation_cfg = {}

        defaults = validation_cfg.get("defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}

        detector_rules = validation_cfg.get("detectors", {})
        if not isinstance(detector_rules, dict):
            detector_rules = {}

        alias_key = str(alias or "").strip().upper()
        rule = {}
        if defaults:
            rule.update(defaults)
        for key, value in detector_rules.items():
            if str(key or "").strip().upper() == alias_key and isinstance(value, dict):
                rule.update(value)
                break

        row_target = self._to_float_or_none(rule.get("row_target_px"))
        if row_target is None:
            row_target = height / 2.0

        col_target = self._to_float_or_none(rule.get("col_target_px"))
        col_min = self._to_float_or_none(rule.get("col_min_px"))
        col_max = self._to_float_or_none(rule.get("col_max_px"))
        col_gt = self._to_float_or_none(rule.get("col_gt_px"))
        col_lt = self._to_float_or_none(rule.get("col_lt_px"))

        if col_target is not None:
            col = float(col_target)
        elif col_gt is not None and col_lt is not None and float(col_lt) > float(col_gt):
            col = (float(col_gt) + float(col_lt)) / 2.0
        elif col_gt is not None:
            col = float(col_gt) + 1.0
        elif col_min is not None and col_max is not None and float(col_max) >= float(col_min):
            col = (float(col_min) + float(col_max)) / 2.0
        elif col_min is not None:
            col = float(col_min)
        elif col_lt is not None:
            col = float(col_lt) - 1.0
        elif col_max is not None:
            col = float(col_max)
        else:
            col = width / 2.0

        # Ensure strict inequalities and bounds where specified.
        if col_gt is not None and not (col > float(col_gt)):
            col = float(col_gt) + 1.0
        if col_min is not None and col < float(col_min):
            col = float(col_min)
        if col_lt is not None and not (col < float(col_lt)):
            col = float(col_lt) - 1.0
        if col_max is not None and col > float(col_max):
            col = float(col_max)

        return float(row_target), float(col)

    def _demo_poni_is_compliant(
        self,
        *,
        alias: str,
        poni_text: str,
        detector_size,
        center_tolerance_px: float = 0.5,
        size_tolerance_px: float = 0.5,
    ) -> bool:
        """Check that a demo PONI matches expected detector size and configured center."""
        geometry = parse_poni_center_px(
            str(poni_text or ""),
            fallback_detector_size=detector_size,
        )
        if not isinstance(geometry, dict):
            return False

        expected_row, expected_col = self._resolve_demo_poni_center_px(alias, detector_size)
        actual_row = float(geometry.get("row_px", 0.0))
        actual_col = float(geometry.get("col_px", 0.0))
        actual_w = float(geometry.get("width_px", 0.0))
        actual_h = float(geometry.get("height_px", 0.0))
        try:
            expected_w = float(detector_size[0])
            expected_h = float(detector_size[1])
        except Exception:
            expected_w, expected_h = 256.0, 256.0

        if abs(actual_w - expected_w) > float(size_tolerance_px):
            return False
        if abs(actual_h - expected_h) > float(size_tolerance_px):
            return False
        if abs(actual_row - expected_row) > float(center_tolerance_px):
            return False
        if abs(actual_col - expected_col) > float(center_tolerance_px):
            return False
        return True

    def _auto_provision_demo_poni_files(self, aliases) -> bool:
        """Create/load fake PONI files for aliases in DEV mode."""
        if not bool(self.config.get("DEV", False)):
            return False

        if not isinstance(getattr(self, "ponis", None), dict):
            self.ponis = {}
        if not isinstance(getattr(self, "poni_files", None), dict):
            self.poni_files = {}

        demo_dir = Path(__file__).resolve().parents[3] / "resources" / "demo_poni_files"
        demo_dir.mkdir(parents=True, exist_ok=True)

        detector_cfg_by_alias = {}
        for detector_cfg in self.config.get("detectors", []):
            alias = str(detector_cfg.get("alias") or "").strip()
            if alias:
                detector_cfg_by_alias[alias] = detector_cfg

        distances_by_alias = {}
        if hasattr(self, "_distance_map_by_alias"):
            try:
                distances_by_alias = dict(self._distance_map_by_alias() or {})
            except Exception:
                distances_by_alias = {}

        added = 0
        for alias in sorted(set(aliases)):
            alias = str(alias).strip()
            if not alias:
                continue

            existing_content = str((self.ponis or {}).get(alias) or "").strip()
            existing_meta = (self.poni_files or {}).get(alias, {})
            existing_path = ""
            if isinstance(existing_meta, dict):
                existing_path = str(existing_meta.get("path") or "").strip()

            demo_path = demo_dir / f"{alias.lower()}_demo.poni"

            detector_cfg = detector_cfg_by_alias.get(alias, {})
            size_cfg = detector_cfg.get("size", {}) if isinstance(detector_cfg, dict) else {}
            if isinstance(size_cfg, dict):
                detector_size = (
                    int(size_cfg.get("width", 256)),
                    int(size_cfg.get("height", 256)),
                )
            else:
                detector_size = (256, 256)

            pixel_cfg = detector_cfg.get("pixel_size_um", [55.0, 55.0]) if isinstance(detector_cfg, dict) else [55.0, 55.0]
            if isinstance(pixel_cfg, (int, float)):
                pixel_size_um = (float(pixel_cfg), float(pixel_cfg))
            elif isinstance(pixel_cfg, (list, tuple)):
                if len(pixel_cfg) >= 2:
                    pixel_size_um = (float(pixel_cfg[0]), float(pixel_cfg[1]))
                elif len(pixel_cfg) == 1:
                    pixel_size_um = (float(pixel_cfg[0]), float(pixel_cfg[0]))
                else:
                    pixel_size_um = (55.0, 55.0)
            else:
                pixel_size_um = (55.0, 55.0)

            distance_cm = float(distances_by_alias.get(alias, 17.0))
            center_px = self._resolve_demo_poni_center_px(alias, detector_size)

            # If operator selected a non-demo PONI file and it's present in memory, keep it.
            if existing_content and existing_path and os.path.exists(existing_path):
                try:
                    is_demo_path = Path(existing_path).resolve().parent == demo_dir.resolve()
                except Exception:
                    is_demo_path = str(existing_path).endswith(f"{alias.lower()}_demo.poni")
                if not is_demo_path:
                    continue
                if self._demo_poni_is_compliant(
                    alias=alias,
                    poni_text=existing_content,
                    detector_size=detector_size,
                ):
                    continue

            # Reuse existing demo file only when it already matches expected size/center.
            if demo_path.exists():
                try:
                    content = demo_path.read_text(encoding="utf-8")
                    if self._demo_poni_is_compliant(
                        alias=alias,
                        poni_text=content,
                        detector_size=detector_size,
                    ):
                        self.ponis[alias] = content
                        self.poni_files[alias] = {
                            "path": str(demo_path),
                            "name": demo_path.name,
                        }
                        added += 1
                        continue
                except Exception:
                    logger.debug(
                        "Suppressed exception while reading existing demo PONI",
                        exc_info=True,
                    )

            content = self._build_fake_poni_content(
                alias=alias,
                distance_cm=distance_cm,
                detector_size=detector_size,
                pixel_size_um=pixel_size_um,
                center_px=center_px,
            )
            try:
                demo_path.write_text(content, encoding="utf-8")
                self.ponis[alias] = content
                self.poni_files[alias] = {
                    "path": str(demo_path),
                    "name": demo_path.name,
                }
                added += 1
            except Exception as exc:
                logger.warning(
                    "Failed to auto-generate demo PONI file for alias=%s error=%s",
                    alias,
                    str(exc),
                )

        if added > 0:
            self._log_technical_event(
                f"Auto-provisioned {added} fake PONI file(s) for demo mode"
            )
        return added > 0

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
                    if isinstance(alias, bytes):
                        alias = alias.decode("utf-8", errors="replace")
                    alias = str(alias or "").strip()
                    if not alias and str(ds_name).startswith("poni_"):
                        alias = str(ds_name)[5:].upper()
                    if not alias:
                        continue

                    value = ds[()]
                    if isinstance(value, bytes):
                        text = value.decode("utf-8", errors="replace")
                    else:
                        text = str(value)
                    poni_by_alias[alias] = text
                except Exception:
                    logger.warning(
                        "Failed to parse PONI dataset while validating centers: %s",
                        ds_name,
                        exc_info=True,
                    )

        return poni_by_alias

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
        return sizes

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

        detector_sizes = self._detector_sizes_by_alias()
        return validate_poni_centers(
            poni_text_by_alias=poni_text_by_alias,
            detector_sizes_by_alias=detector_sizes,
            validation_config=validation_cfg,
        )

    def _current_operator_id_for_review(self) -> str:
        operator_id = ""
        operator_manager = getattr(self, "operator_manager", None)
        if operator_manager is not None:
            getter = getattr(operator_manager, "get_current_operator_id", None)
            if callable(getter):
                try:
                    operator_id = str(getter() or "").strip()
                except Exception:
                    operator_id = ""

        if not operator_id and hasattr(self, "config") and isinstance(self.config, dict):
            operator_id = str(self.config.get("operator_id") or "").strip()

        return operator_id or "unknown"

    def _read_poni_review_state(self, container_path: Path):
        import h5py

        try:
            with h5py.File(container_path, "r") as h5f:
                status = self._decode_attr_text(
                    h5f.attrs.get(self.PONI_REVIEW_STATUS_ATTR, "pending")
                ).strip().lower()
                user = self._decode_attr_text(
                    h5f.attrs.get(self.PONI_REVIEW_USER_ATTR, "")
                ).strip()
                timestamp = self._decode_attr_text(
                    h5f.attrs.get(self.PONI_REVIEW_TS_ATTR, "")
                ).strip()
                notes = self._decode_attr_text(
                    h5f.attrs.get(self.PONI_REVIEW_NOTES_ATTR, "")
                ).strip()
                raw_in_zone = h5f.attrs.get(self.PONI_REVIEW_IN_ZONE_ATTR, False)
                in_zone = bool(raw_in_zone)
        except Exception:
            return {
                "status": "pending",
                "user": "",
                "timestamp": "",
                "in_zone": False,
                "notes": "",
            }

        return {
            "status": status or "pending",
            "user": user,
            "timestamp": timestamp,
            "in_zone": in_zone,
            "notes": notes,
        }

    def _write_poni_review_state(
        self,
        container_path: Path,
        *,
        status: str,
        in_zone: bool,
        notes: str = "",
    ) -> bool:
        import h5py

        review_status = str(status or "pending").strip().lower() or "pending"
        review_user = self._current_operator_id_for_review()
        review_timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        review_notes = str(notes or "").strip()

        original_mode = None
        try:
            original_mode = Path(container_path).stat().st_mode
            if not os.access(container_path, os.W_OK):
                os.chmod(container_path, original_mode | 0o200)
        except Exception:
            original_mode = None

        try:
            with h5py.File(container_path, "a") as h5f:
                h5f.attrs[self.PONI_REVIEW_STATUS_ATTR] = review_status
                h5f.attrs[self.PONI_REVIEW_USER_ATTR] = review_user
                h5f.attrs[self.PONI_REVIEW_TS_ATTR] = review_timestamp
                h5f.attrs[self.PONI_REVIEW_IN_ZONE_ATTR] = bool(in_zone)
                h5f.attrs[self.PONI_REVIEW_NOTES_ATTR] = review_notes
            return True
        except Exception as exc:
            logger.warning(
                "Failed to write PONI review state for %s: %s",
                container_path,
                exc,
                exc_info=True,
            )
            return False
        finally:
            if original_mode is not None:
                try:
                    os.chmod(container_path, original_mode)
                except Exception:
                    logger.debug(
                        "Suppressed exception while restoring container mode after PONI review write",
                        exc_info=True,
                    )

    def _run_poni_center_review_workflow(
        self,
        container_path: Path,
        *,
        container_id: str,
        prompt_reload_on_reject: bool = True,
    ) -> bool:
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_center_validation", {})
        if not isinstance(validation_cfg, dict) or not validation_cfg.get("enabled", False):
            return True

        show_preview = getattr(self, "_show_poni_center_preview_for_container", None)
        if not callable(show_preview):
            QMessageBox.warning(
                self,
                "PONI Review Unavailable",
                "PONI center review UI is unavailable in this build.",
            )
            return False

        decision = show_preview(str(container_path), decision_mode=True)
        if decision is None:
            QMessageBox.warning(
                self,
                "PONI Review Required",
                "PONI center preview could not be shown.\n\n"
                "Cannot proceed without user review.",
            )
            self._log_technical_event(
                f"PONI center review blocked: preview unavailable for {container_id}"
            )
            return False

        if bool(decision):
            center_errors, _center_warnings = self._validate_poni_centers_for_container(
                Path(container_path)
            )
            in_zone = len(center_errors) == 0
            if in_zone:
                self._write_poni_review_state(
                    Path(container_path),
                    status="accepted",
                    in_zone=True,
                    notes="accepted_in_valid_zone",
                )
                self._log_technical_event(
                    f"PONI center review accepted for {container_id}"
                )
                return True

            # Hard fail: user cannot proceed with an out-of-zone center acceptance.
            self._write_poni_review_state(
                Path(container_path),
                status="rejected",
                in_zone=False,
                notes="accept_attempt_rejected_out_of_zone",
            )
            QMessageBox.critical(
                self,
                "PONI Validation Failed",
                "Center point is outside the valid zone.\n\n"
                "Lock cannot continue.\n"
                "Adjust PONI/distance settings and load valid PONI files.",
            )
            if prompt_reload_on_reject:
                retry = QMessageBox.question(
                    self,
                    "Reload PONI",
                    "Load new PONI files now?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if retry == QMessageBox.Yes:
                    return bool(self._launch_poni_update_for_container(Path(container_path)))

            QMessageBox.warning(
                self,
                "Lock Blocked",
                "Technical container remains unlocked.\n\n"
                "Without lock, downstream measurements will not be available.",
            )
            return False

        self._write_poni_review_state(
            Path(container_path),
            status="rejected",
            in_zone=False,
            notes="rejected_by_user",
        )
        self._log_technical_event(
            f"PONI center review rejected for {container_id}"
        )

        if prompt_reload_on_reject:
            retry = QMessageBox.question(
                self,
                "PONI Rejected",
                "PONI centers were rejected.\n\nLoad new PONI files now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if retry == QMessageBox.Yes:
                return bool(self._launch_poni_update_for_container(Path(container_path)))

        QMessageBox.warning(
            self,
            "Lock Blocked",
            "PONI review was rejected and no replacement was loaded.\n\n"
            "Technical container remains unlocked.\n"
            "Without lock, downstream measurements will not be available.",
        )
        return False

    def _collect_lock_detector_aliases(self, container_path: Path):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        aliases = []

        if hasattr(self, "_get_active_detector_aliases"):
            try:
                aliases.extend([a for a in self._get_active_detector_aliases() if a])
            except Exception:
                import logging
                logging.getLogger(__name__).debug(
                    "Suppressed exception in h5_management_locking_mixin.py",
                    exc_info=True,
                )

        if aliases:
            return sorted({str(a) for a in aliases if str(a).strip()})

        try:
            with h5py.File(container_path, "r") as h5f:
                tech_group = h5f.get(schema.GROUP_TECHNICAL)
                if tech_group is None:
                    return []
                for event_name in tech_group.keys():
                    if not str(event_name).startswith("tech_evt_"):
                        continue
                    event_group = tech_group[event_name]
                    for detector_name in event_group.keys():
                        detector_group = event_group[detector_name]
                        alias = detector_group.attrs.get(schema.ATTR_DETECTOR_ALIAS, "")
                        if isinstance(alias, bytes):
                            alias = alias.decode("utf-8", errors="replace")
                        alias = str(alias or "").strip()
                        if alias:
                            aliases.append(alias)
        except Exception:
            return []

        return sorted({str(a) for a in aliases if str(a).strip()})

    def _prompt_poni_selection_for_lock(self, aliases) -> bool:
        from difra.gui.main_window_ext.technical_measurements import (
            PoniFileSelectionDialog,
        )

        if not isinstance(getattr(self, "ponis", None), dict):
            self.ponis = {}
        if not isinstance(getattr(self, "poni_files", None), dict):
            self.poni_files = {}

        dialog = PoniFileSelectionDialog(
            aliases=sorted(set(aliases)),
            current_poni_files=getattr(self, "poni_files", {}) or {},
            parent=self,
        )
        accepted_value = getattr(type(dialog), "Accepted", 1)
        if dialog.exec_() != accepted_value:
            self._log_technical_event("Lock cancelled: PONI selection dialog cancelled")
            return False

        selected_poni_files = dialog.get_poni_files() or {}
        missing = []
        read_errors = []

        for alias in sorted(set(aliases)):
            candidate_path = str(selected_poni_files.get(alias) or "").strip()
            if not candidate_path:
                current_info = self.poni_files.get(alias)
                if isinstance(current_info, dict):
                    candidate_path = str(current_info.get("path") or "").strip()

            if not candidate_path or not os.path.exists(candidate_path):
                missing.append(alias)
                continue

            try:
                with open(candidate_path, "r", encoding="utf-8") as file_handle:
                    self.ponis[alias] = file_handle.read()
                self.poni_files[alias] = {
                    "path": candidate_path,
                    "name": Path(candidate_path).name,
                }
            except Exception as exc:
                read_errors.append(f"{alias}: {exc}")

        if missing:
            QMessageBox.warning(
                self,
                "Missing PONI Files",
                "PONI files are required before locking.\n\nMissing for: "
                + ", ".join(missing),
            )
            self._log_technical_event(
                f"Lock blocked: missing PONI for aliases {missing}"
            )
            return False

        if read_errors:
            QMessageBox.warning(
                self,
                "PONI Read Error",
                "Failed to read one or more PONI files:\n\n"
                + "\n".join(read_errors),
            )
            self._log_technical_event(
                f"Lock blocked: failed to read PONI files ({len(read_errors)})"
            )
            return False

        return True

    def _launch_poni_update_for_container(self, container_path: Path) -> bool:
        """Run Update PONI flow for a specific container path."""
        update_fn = getattr(self, "update_active_technical_container_poni", None)
        if not callable(update_fn):
            return False

        previous_path = str(getattr(self, "_active_technical_container_path", "") or "")
        previous_locked = bool(getattr(self, "_active_technical_container_locked", False))
        switched = False
        try:
            same_path = False
            if previous_path:
                try:
                    same_path = Path(previous_path).resolve() == Path(container_path).resolve()
                except Exception:
                    same_path = str(previous_path) == str(container_path)
            if not same_path:
                self._active_technical_container_path = str(container_path)
                self._active_technical_container_locked = False
                switched = True
            return bool(update_fn())
        finally:
            if switched:
                self._active_technical_container_path = previous_path
                self._active_technical_container_locked = previous_locked

    def _ensure_poni_before_lock(self, container_path: Path, container_id: str) -> bool:
        if self._container_has_poni_datasets(container_path):
            return True

        aliases = self._collect_lock_detector_aliases(container_path)
        if not aliases:
            QMessageBox.warning(
                self,
                "Missing PONI",
                "Container has no PONI datasets and detector aliases could not be determined.\n\n"
                "Load/select a valid technical container with detector aliases and try again.",
            )
            self._log_technical_event(
                f"Lock blocked: missing PONI and detector aliases for {container_id}"
            )
            return False

        if bool(self.config.get("DEV", False)):
            if self._auto_provision_demo_poni_files(aliases):
                if hasattr(self, "_sync_active_technical_container_from_table"):
                    synced = self._sync_active_technical_container_from_table(show_errors=False)
                    if not synced:
                        QMessageBox.warning(
                            self,
                            "PONI Sync Failed",
                            "Demo PONI files were generated but container sync failed.\n\n"
                            "Fix technical rows and try locking again.",
                        )
                        self._log_technical_event(
                            f"Lock blocked: failed to sync demo PONI into {container_id}"
                        )
                        return False
                if self._container_has_poni_datasets(container_path):
                    QMessageBox.information(
                        self,
                        "Demo PONI Added",
                        "DEMO mode detected. Fake PONI files were added automatically.\n\n"
                        "Validation will now continue before lock.",
                    )
                    return True

        reply = QMessageBox.question(
            self,
            "PONI Required",
            "Active technical container has no embedded PONI datasets.\n\n"
            "Select PONI files now? They will be added, then validation will run before lock.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            self._log_technical_event("Lock cancelled: user declined PONI selection")
            return False

        if not self._prompt_poni_selection_for_lock(aliases):
            return False

        if hasattr(self, "_sync_active_technical_container_from_table"):
            synced = self._sync_active_technical_container_from_table(show_errors=False)
            if not synced:
                QMessageBox.warning(
                    self,
                    "PONI Sync Failed",
                    "PONI files were selected but container sync failed.\n\n"
                    "Fix technical rows and try locking again.",
                )
                self._log_technical_event(
                    f"Lock blocked: failed to sync selected PONI into {container_id}"
                )
                return False

        if not self._container_has_poni_datasets(container_path):
            QMessageBox.warning(
                self,
                "PONI Missing",
                "PONI datasets are still missing after selection.\n\n"
                "Please verify detector aliases and selected PONI files.",
            )
            self._log_technical_event(
                f"Lock blocked: PONI still missing after selection for {container_id}"
            )
            return False

        self._log_technical_event(
            f"PONI datasets added before lock for {container_id}"
        )
        return True

    def _validate_container_before_lock(self, container_path: Path, container_id: str) -> bool:
        """Validate technical container before allowing lock."""
        import h5py

        technical_validator = get_technical_validator(
            self.config if hasattr(self, "config") else None
        )
        validate_technical_container = technical_validator.validate_technical_container

        try:
            is_valid, errors, warnings = validate_technical_container(
                str(container_path), strict=False
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Validation Error",
                f"Failed to validate technical container before lock:\n{exc}",
            )
            self._log_technical_event(f"Lock validation error: {exc}")
            return False

        expected_version = self.config.get(
            "expected_technical_schema_version",
            self.config.get("container_version", "0.2"),
        )
        actual_version = "unknown"
        try:
            with h5py.File(container_path, "r") as h5f:
                raw_version = h5f.attrs.get("schema_version", "unknown")
                if isinstance(raw_version, bytes):
                    raw_version = raw_version.decode("utf-8", errors="replace")
                actual_version = str(raw_version)
        except Exception as exc:
            errors.append(f"Failed to read schema version: {exc}")
            is_valid = False

        if actual_version != str(expected_version):
            errors.append(
                f"Schema version mismatch: container has {actual_version}, expected {expected_version}"
            )
            is_valid = False

        center_errors, center_warnings = self._validate_poni_centers_for_container(
            Path(container_path)
        )
        center_error_count = len(center_errors)
        if center_errors:
            errors.extend(center_errors)
            is_valid = False
        if center_warnings:
            warnings.extend(center_warnings)

        if not is_valid:
            details = []
            for i, err in enumerate(errors[:8], 1):
                details.append(f"{i}. {err}")
            if len(errors) > 8:
                details.append(f"... and {len(errors) - 8} more")

            remediation_lines = []
            has_distance_error = any(
                ("distance" in str(err).lower()) and ("poni" in str(err).lower())
                for err in errors
            )
            if has_distance_error:
                remediation_lines.append(
                    "Distance check failed: update PONI file distance values or detector distance settings."
                )
            if center_error_count > 0:
                remediation_lines.append(
                    "Center position check failed: update PONI file center values or update limits in main.json (poni_center_validation)."
                )

            message = (
                "Technical container cannot be locked because validation failed.\n\n"
                f"Container ID: {container_id}\n"
                f"File: {container_path.name}\n\n"
                + "\n".join(details)
            )
            if remediation_lines:
                message += "\n\nRecommended action:\n" + "\n".join(
                    f"- {line}" for line in remediation_lines
                )

            QMessageBox.critical(
                self,
                "Validation Failed",
                message,
            )
            self._log_technical_event(
                f"Lock blocked by validation errors for {container_id}: {len(errors)} error(s)"
            )
            return False

        if warnings:
            self._log_technical_event(
                f"Validation warnings before lock for {container_id}: {len(warnings)} warning(s)"
            )

        return True

    def _confirm_poni_center_preview_before_lock(self, container_path: Path, container_id: str) -> bool:
        """Require accepted user review for PONI center preview before lock."""
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_center_validation", {})
        if not isinstance(validation_cfg, dict) or not validation_cfg.get("enabled", False):
            return True

        review_state = self._read_poni_review_state(Path(container_path))
        if (
            review_state.get("status") == "accepted"
            and bool(review_state.get("in_zone", False))
        ):
            return True

        self._log_technical_event(
            f"PONI center review must be re-confirmed before lock for {container_id}"
        )
        return bool(
            self._run_poni_center_review_workflow(
                Path(container_path),
                container_id=container_id,
                prompt_reload_on_reject=True,
            )
        )

    def _validate_and_prompt_lock(self, container_path: str, container_id: str):
        """Validate container and prompt user to lock it.
        
        Args:
            container_path: Path to generated container
            container_id: Container ID
        """
        import h5py
        technical_validator = get_technical_validator(
            self.config if hasattr(self, "config") else None
        )
        validate_technical_container = technical_validator.validate_technical_container
        
        # Validate container
        try:
            is_valid, errors, warnings = validate_technical_container(container_path, strict=False)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Validation Error",
                f"Failed to validate container:\n{e}"
            )
            self._log_technical_event(f"Validation error: {e}")
            return
        
        # Check schema version
        expected_version = self.config.get(
            "expected_technical_schema_version",
            self.config.get("container_version", "0.2"),
        )
        try:
            with h5py.File(container_path, 'r') as f:
                actual_version = f.attrs.get("schema_version", "unknown")
                if isinstance(actual_version, bytes):
                    actual_version = actual_version.decode('utf-8')
                
                if actual_version != expected_version:
                    errors.append(
                        f"Schema version mismatch: container has {actual_version}, expected {expected_version}"
                    )
                    is_valid = False
        except Exception as e:
            errors.append(f"Failed to check schema version: {e}")
            is_valid = False

        center_errors, center_warnings = self._validate_poni_centers_for_container(
            Path(container_path)
        )
        if center_errors:
            errors.extend(center_errors)
            is_valid = False
        if center_warnings:
            warnings.extend(center_warnings)
        
        # Build validation summary
        status_icon = "✅" if is_valid else ("⚠️" if errors else "✅")
        summary_lines = [
            f"{status_icon} Container Validation Results",
            "",
            f"Container ID: {container_id}",
            f"Location: {os.path.basename(container_path)}",
            f"Schema Version: {actual_version}",
            "",
        ]
        
        if errors:
            summary_lines.append(f"❌ {len(errors)} Error(s):")
            for i, error in enumerate(errors[:5], 1):
                summary_lines.append(f"  {i}. {error}")
            if len(errors) > 5:
                summary_lines.append(f"  ... and {len(errors) - 5} more")
            summary_lines.append("")
        
        if warnings:
            summary_lines.append(f"⚠️  {len(warnings)} Warning(s):")
            for i, warning in enumerate(warnings[:3], 1):
                summary_lines.append(f"  {i}. {warning}")
            if len(warnings) > 3:
                summary_lines.append(f"  ... and {len(warnings) - 3} more")
            summary_lines.append("")
        
        if not errors and not warnings:
            summary_lines.append("✅ No issues found")
            summary_lines.append("")
        
        # Show validation results
        if is_valid:
            summary_lines.append("Container is valid and ready to lock.")
            summary_lines.append("\nLock this container for session measurements?")
            
            reply = QMessageBox.question(
                self,
                "Validation Passed",
                "\n".join(summary_lines),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            
            if reply == QMessageBox.Yes:
                if self._confirm_poni_center_preview_before_lock(
                    Path(container_path), container_id
                ):
                    # Lock the container only after operator preview confirmation.
                    self._lock_container(container_path, container_id)
            else:
                QMessageBox.information(
                    self,
                    "Container Saved",
                    f"Container saved without locking.\n\nLocation: {container_path}",
                )
        else:
            summary_lines.append("Container has validation errors.")
            summary_lines.append("\nYou can still use this container, but it may not work correctly.")
            summary_lines.append("\nSave anyway?")
            
            reply = QMessageBox.warning(
                self,
                "Validation Failed",
                "\n".join(summary_lines),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            
            if reply == QMessageBox.Yes:
                self._log_technical_event(f"User saved container {container_id} despite validation errors")
                QMessageBox.information(
                    self,
                    "Container Saved",
                    f"Container saved with errors.\n\nLocation: {container_path}",
                )
    
    def _archive_existing_containers(self, storage_folder: str) -> int:
        """Archive any existing .h5 containers in storage folder before creating new one."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.archive_existing_containers(
            self, storage_folder
        )

    def _update_aux_table_paths_after_archive(self, archive_folder: Path) -> int:
        """Remap aux table file paths to archived locations for visualization."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.update_aux_table_paths_after_archive(
            self, archive_folder
        )

    def create_new_technical_container(self):
        """Legacy API kept for compatibility; uses container-first creation flow."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.create_new_technical_container(self)

    def lock_active_technical_container(self):
        """Lock currently active technical container."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.lock_active_technical_container(self)

    def archive_active_technical_container(self):
        """Archive active technical container (irreversible)."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.archive_active_technical_container(self)

    def update_active_technical_container_poni(self):
        """Update PONI files for active technical container and resync datasets."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.update_active_technical_container_poni(self)
    
    def _lock_container(self, container_path: str, container_id: str):
        """Lock the technical container and archive raw data."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.lock_container(
            self, container_path, container_id
        )
    
