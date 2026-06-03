"""Technical H5 locking responsibilities: H5LockingDemoPoniMixin."""

from difra.gui.main_window_ext.technical.h5_locking_common import *


class H5LockingDemoPoniMixin:
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
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        alias_key = resolve_poni_rule_alias(alias, cfg.get("detectors", []))
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

        validation_cfg = cfg.get("poni_center_validation", {})
        if not isinstance(validation_cfg, dict):
            validation_cfg = {}

        defaults = validation_cfg.get("defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}

        detector_rules = validation_cfg.get("detectors", {})
        if not isinstance(detector_rules, dict):
            detector_rules = {}

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

