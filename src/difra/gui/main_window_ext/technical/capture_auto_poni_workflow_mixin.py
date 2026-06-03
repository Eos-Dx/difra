import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from difra.gui.main_window_ext.technical.capture_auto_poni_sources_mixin import (
    TechnicalCaptureAutoPoniSourcesMixin,
)

logger = logging.getLogger(__name__)


def _tm():
    from difra.gui.main_window_ext.technical import capture_mixin

    return capture_mixin._tm()


class TechnicalCaptureAutoPoniWorkflowMixin(TechnicalCaptureAutoPoniSourcesMixin):
    def _prepare_auto_poni_reviews(
        self,
        auto_cfg: dict,
        *,
        sources: dict | None = None,
        distance_cm_by_alias: dict | None = None,
        first_visible_ring_by_alias: dict | None = None,
        rings_to_search_by_alias: dict | None = None,
        detector_config_by_alias: dict | None = None,
        center_px_by_alias: dict | None = None,
    ):
        tm = _tm()
        sources = (
            sources
            if isinstance(sources, dict)
            else self._collect_auto_poni_agbh_sources()
        )
        if not sources:
            tm.QMessageBox.warning(
                self,
                "Auto PONI",
                "No AGBH rows found. Measure or load AGBH NumPy images first.",
            )
            return False

        try:
            from difra.gui.technical.pyfai_calibration import (
                build_pyfai_calib2_command,
                energy_kev_to_wavelength_m,
                is_headless_agbh_fit_plausible,
                load_calibration_array,
                prepare_agbh_calib2_review,
                run_headless_agbh_fit,
            )
        except Exception as exc:
            tm.QMessageBox.warning(
                self,
                "Auto PONI",
                f"Auto PONI helpers are unavailable:\n{exc}",
            )
            return False

        reviews = {}
        images = {}
        detector_configs = {}
        missing = []
        output_dir = self._reset_autopony_output_dir()

        for alias, source_ref in sorted(sources.items()):
            alias_key = str(alias or "").strip().upper()
            detector_config = (
                dict(detector_config_by_alias.get(alias_key, {}))
                if isinstance(detector_config_by_alias, dict)
                and isinstance(detector_config_by_alias.get(alias_key), dict)
                else self._auto_poni_detector_config_for_alias(alias)
            )
            detector_configs[alias] = detector_config
            first_visible_ring = None
            if isinstance(first_visible_ring_by_alias, dict):
                try:
                    first_visible_ring = int(first_visible_ring_by_alias.get(alias_key))
                except (TypeError, ValueError):
                    first_visible_ring = None
            try:
                rings_to_search = int(
                    (rings_to_search_by_alias or {}).get(
                        alias_key,
                        auto_cfg.get("rings_to_show", 3),
                    )
                )
            except (TypeError, ValueError):
                rings_to_search = int(auto_cfg.get("rings_to_show", 3) or 3)
            rings_to_search = max(1, rings_to_search)
            distance_cm = (
                (distance_cm_by_alias or {}).get(alias_key)
                if isinstance(distance_cm_by_alias, dict)
                else None
            )
            if distance_cm is None:
                distance_m = self._distance_m_for_detector_alias(alias, detector_config)
            else:
                try:
                    distance_m = float(distance_cm) / 100.0
                except (TypeError, ValueError):
                    distance_m = None
            if distance_m is None:
                missing.append(f"{alias}: distance")
                continue
            center_px = None
            if isinstance(center_px_by_alias, dict):
                center_px = center_px_by_alias.get(alias_key)
                if isinstance(center_px, (list, tuple)) and len(center_px) >= 2:
                    center_px = (float(center_px[0]), float(center_px[1]))
                else:
                    center_px = None
            if center_px is None:
                center_px = self._auto_poni_seed_center_px_from_config(alias, auto_cfg)
            if center_px is None:
                center_px = self._auto_poni_center_px_for_alias(alias, detector_config)

            existing_poni = str((getattr(self, "ponis", {}) or {}).get(alias) or "")
            if not existing_poni:
                existing_poni = str(detector_config.get("default_poni") or "")

            try:
                _, source_path = self._auto_poni_output_dir_for_source(source_ref)
                wavelength_m = energy_kev_to_wavelength_m(
                    float(auto_cfg.get("energy_kev", 8.04) or 8.04)
                )
                review = prepare_agbh_calib2_review(
                    source_image=source_ref,
                    detector_config=detector_config,
                    distance_m=distance_m,
                    alias=alias,
                    output_dir=output_dir,
                    existing_poni_text=existing_poni,
                    wavelength_m=wavelength_m,
                    calibrant=str(auto_cfg.get("calibrant") or "AgBh"),
                    center_px=center_px,
                    first_visible_ring=first_visible_ring,
                    rings_to_show=rings_to_search,
                    output_prefix=alias_key,
                )
                if source_path is not None:
                    review = type(review)(
                        image_path=review.image_path,
                        poni_path=review.poni_path,
                        command=review.command,
                        poni_text=review.poni_text,
                        source_path=source_path,
                    )
                if first_visible_ring is not None:
                    try:
                        fit_result = run_headless_agbh_fit(
                            source_image=source_ref,
                            detector_config=detector_config,
                            distance_m=distance_m,
                            output_dir=output_dir,
                            alias=alias,
                            center_px=center_px,
                            wavelength_m=wavelength_m,
                            calibrant=str(auto_cfg.get("calibrant") or "AgBh"),
                            first_visible_ring=first_visible_ring,
                            rings_to_show=rings_to_search,
                            output_prefix=alias_key,
                        )
                    except Exception as fit_exc:
                        self._log_technical_event(
                            f"Auto PONI headless fit failed for {alias}: {fit_exc}"
                        )
                    else:
                        if is_headless_agbh_fit_plausible(
                            fit_result,
                            seed_poni_text=review.poni_text,
                            detector_config=detector_config,
                        ):
                            command = build_pyfai_calib2_command(
                                image_path=review.image_path,
                                poni_text=fit_result.poni_text,
                                detector_config=detector_config,
                                calibrant=str(auto_cfg.get("calibrant") or "AgBh"),
                            )
                            command = [
                                *command[:-1],
                                "-n",
                                str(fit_result.npt_path),
                                command[-1],
                            ]
                            review = type(review)(
                                image_path=review.image_path,
                                poni_path=fit_result.poni_path,
                                command=command,
                                poni_text=fit_result.poni_text,
                                source_path=source_path or getattr(review, "source_path", None),
                            )
                            self._log_technical_event(
                                "Auto PONI headless fit "
                                f"{alias}: points={fit_result.extracted_points}, "
                                f"chi2={fit_result.chi2}"
                            )
                        else:
                            from difra.gui.technical.pyfai_calibration import (
                                parse_poni_parameters,
                            )

                            seed_params = parse_poni_parameters(review.poni_text)
                            fit_params = parse_poni_parameters(fit_result.poni_text)
                            self._log_technical_event(
                                "Auto PONI headless fit rejected "
                                f"{alias}: points={fit_result.extracted_points}, "
                                f"chi2={fit_result.chi2}, "
                                f"seed_dist={seed_params.get('Distance')}, "
                                f"fit_dist={fit_params.get('Distance')}"
                            )
                reviews[alias] = review
                images[alias] = load_calibration_array(source_ref)
            except Exception as exc:
                missing.append(f"{alias}: {exc}")

        if missing:
            tm.QMessageBox.warning(
                self,
                "Auto PONI",
                "Could not prepare Auto PONI for:\n\n" + "\n".join(missing),
            )

        if not reviews:
            return False
        return {
            "reviews": reviews,
            "images": images,
            "detector_configs": detector_configs,
        }
    def _first_visible_rings_for_auto_poni(self, aliases, auto_cfg: dict) -> dict:
        configured = auto_cfg.get("first_visible_ring_by_alias", {})
        result = {}
        for alias in aliases:
            rule_alias = self._auto_poni_rule_alias(alias)
            alias_key = str(alias or "").strip().upper()
            rule_key = str(rule_alias or "").strip().upper()
            try:
                value = configured.get(alias_key, configured.get(rule_key, 1))
                ring = int(value)
            except (TypeError, ValueError):
                ring = 1
            result[alias_key] = max(1, ring)
        return result
    def _launch_pyfai_reviews(self, reviews: dict) -> bool:
        env = self._resolve_auto_poni_pyfai_calib2_env()
        if not env:
            _tm().QMessageBox.warning(self, "Auto PONI", "No conda env configured for pyFAI.")
            return False
        try:
            from difra.gui.technical.pyfai_calibration import (
                write_pyfai_calib2_launcher,
            )
        except Exception:
            write_pyfai_calib2_launcher = None

        commands = []
        for alias, review in reviews.items():
            command = list(review.command)
            if (
                "DIFRA-256-55UM" in command
                and callable(write_pyfai_calib2_launcher)
            ):
                launcher = write_pyfai_calib2_launcher(
                    output_dir=Path(review.image_path).parent,
                    command=command,
                    launcher_stem=f"run_pyfai_calib2_{alias}",
                )
                command = ["python", str(launcher)]
            commands.append(command)
        if not commands:
            return False
        folder = Path(next(iter(reviews.values())).image_path).parent

        try:
            if os.name == "nt":
                command_lines = [
                    self._build_windows_conda_pyfai_command(env=env, command=cmd)
                    for cmd in commands
                ]
                script = "\n".join(
                    [
                        "$ErrorActionPreference = 'Stop'",
                        f"Set-Location {self._ps_quote(str(folder))}",
                        *command_lines,
                        "",
                    ]
                )
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".ps1", delete=False, encoding="utf-8"
                ) as handle:
                    handle.write(script)
                    script_path = handle.name
                start_cmd = (
                    f'Start-Process powershell '
                    f'-ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "{script_path}"'
                )
                subprocess.Popen(["powershell", "-NoProfile", "-Command", start_cmd])
            else:
                command_lines = [
                    self._build_posix_conda_pyfai_command(env=env, command=cmd)
                    for cmd in commands
                ]
                script = "\n".join(
                    [
                        "#!/bin/bash",
                        f"cd {shlex.quote(str(folder))}",
                        *command_lines,
                        "",
                    ]
                )
                with tempfile.NamedTemporaryFile(mode="w", suffix=".command", delete=False) as handle:
                    handle.write(script)
                    script_path = handle.name
                os.chmod(script_path, 0o755)
                if sys.platform == "darwin":
                    subprocess.Popen(["open", "-a", "Terminal", script_path])
                else:
                    subprocess.Popen(["bash", script_path])
            self._log_technical_event(f"Auto PONI correction launched for {len(commands)} detector(s)")
            return True
        except Exception as exc:
            logger.warning("Failed to launch Auto PONI correction", exc_info=True)
            _tm().QMessageBox.warning(self, "Auto PONI", f"Could not launch pyFAI:\n{exc}")
            return False
    def _validate_auto_poni_reviews(self, reviews: dict) -> bool:
        tm = _tm()

        def _warn(message: str):
            widget_cls = getattr(tm, "QWidget", None)
            parent = self if widget_cls is not None and isinstance(self, widget_cls) else None
            tm.QMessageBox.warning(parent, "Auto PONI", message)

        if not isinstance(getattr(self, "ponis", None), dict):
            self.ponis = {}
        if not isinstance(getattr(self, "poni_files", None), dict):
            self.poni_files = {}

        active_path = None
        active_getter = getattr(self, "_active_technical_container_path_obj", None)
        if callable(active_getter):
            try:
                active_path = active_getter()
            except Exception:
                active_path = None
        if active_path is None or not Path(active_path).exists():
            self._log_technical_event("Auto PONI validate ignored: no active technical container")
            return False

        try:
            from difra.gui.container_api import get_container_manager

            manager = get_container_manager(self.config if hasattr(self, "config") else None)
            if manager.is_container_locked(Path(active_path)):
                self._log_technical_event(
                    f"Auto PONI validate ignored: active container is locked ({Path(active_path).name})"
                )
                _warn(
                    "Active technical container is locked. "
                    "PONI files cannot be updated in this container."
                )
                return False
        except Exception:
            logger.warning("Failed to check active technical container lock state", exc_info=True)
            return False

        metadata_errors = self._auto_poni_metadata_validation_errors(reviews)
        if metadata_errors:
            details = "\n".join(f"- {msg}" for msg in metadata_errors[:8])
            if len(metadata_errors) > 8:
                details += f"\n- ... and {len(metadata_errors) - 8} more"
            self._log_technical_event("Auto PONI validate blocked: metadata mismatch")
            _warn(
                "Generated PONI files do not match required detector metadata.\n\n"
                + details
                + "\n\nPONI was not accepted or saved."
            )
            return False

        for alias, review in reviews.items():
            poni_text = str(review.poni_text or "")
            autopony_path = self._auto_poni_output_path_for_source(
                getattr(review, "source_path", None),
                getattr(review, "poni_path", ""),
            )
            autopony_path.parent.mkdir(parents=True, exist_ok=True)
            autopony_path.write_text(poni_text, encoding="utf-8")
            target_path = autopony_path.parent.parent / autopony_path.name
            if target_path.exists():
                target_path.unlink()
            shutil.move(str(autopony_path), str(target_path))
            self.ponis[alias] = poni_text
            self.poni_files[alias] = {
                "path": str(target_path),
                "name": target_path.name,
            }

        sync_fn = getattr(self, "_sync_active_technical_container_from_table", None)
        if callable(sync_fn):
            synced = bool(sync_fn(show_errors=True))
            if not synced:
                self._log_technical_event("Auto PONI validated, but container sync failed")
                _warn(
                    "Generated PONI files were saved, but could not be synced into an unlocked technical container.",
                )
                return False

        set_state = getattr(self, "_set_container_state", None)
        if callable(set_state):
            set_state(
                Path(active_path),
                state=getattr(self, "STATE_PENDING_PONI_REVIEW", "pending_poni_review"),
                reason="auto_poni_synced_review_required",
            )

        run_review = getattr(self, "_run_poni_center_review_workflow", None)
        if callable(run_review):
            reviewed = bool(
                run_review(
                    Path(active_path),
                    container_id=Path(active_path).stem,
                    prompt_reload_on_reject=False,
                )
            )
            if not reviewed:
                self._log_technical_event("Auto PONI validated, but PONI center review was not accepted")
                return False
        else:
            show_preview = getattr(self, "_show_poni_center_preview_for_container", None)
            if callable(show_preview):
                try:
                    show_preview(str(active_path))
                except Exception:
                    logger.debug("Suppressed PONI center preview error after Auto PONI validate", exc_info=True)

        sync_state = getattr(self, "_sync_container_state", None)
        if callable(sync_state):
            sync_state(Path(active_path), reason="auto_poni_review_completed")

        self._log_technical_event(f"Auto PONI validated for {len(reviews)} detector(s)")
        app = tm.QApplication.instance() if hasattr(tm, "QApplication") else None
        if app is not None:
            widget_cls = getattr(tm, "QWidget", None)
            parent = self if widget_cls is not None and isinstance(self, widget_cls) else None
            tm.QMessageBox.information(
                parent,
                "Auto PONI",
                "Generated PONI files moved next to the technical container and synced to it.",
            )
        return True
    def run_auto_poni(self):
        auto_cfg = self._auto_poni_config()
        sources = self._collect_auto_poni_agbh_sources()
        if not sources:
            _tm().QMessageBox.warning(
                self,
                "Auto PONI",
                "No AGBH rows found. Measure or load AGBH NumPy images first.",
            )
            return False
        aliases = sorted(sources.keys())
        self._pending_auto_poni_sources = sources
        settings = self._prompt_auto_poni_settings(auto_cfg, aliases)
        self._pending_auto_poni_sources = {}
        if not settings:
            return False
        auto_cfg["energy_kev"] = float(settings.get("energy_kev", 8.04) or 8.04)

        prepared = self._prepare_auto_poni_reviews(
            auto_cfg,
            sources=settings.get("sources_by_alias") or sources,
            distance_cm_by_alias=settings.get("distance_cm_by_alias", {}),
            first_visible_ring_by_alias=settings.get("first_visible_ring_by_alias", {}),
            rings_to_search_by_alias=settings.get("rings_to_search_by_alias", {}),
            detector_config_by_alias=settings.get("detector_config_by_alias", {}),
            center_px_by_alias=settings.get("center_px_by_alias", {}),
        )
        if not prepared:
            return False

        reviews = prepared["reviews"]
        aliases = list(reviews.keys())
        first_visible = settings.get("first_visible_ring_by_alias") or (
            self._first_visible_rings_for_auto_poni(aliases, auto_cfg)
        )
        show_review = self._get_technical_module("show_auto_poni_review_window")
        if not callable(show_review):
            _tm().QMessageBox.warning(self, "Auto PONI", "Auto PONI review UI unavailable.")
            return False

        decision_payload = show_review(
            aliases=aliases,
            review_by_alias=reviews,
            images_by_alias=prepared["images"],
            detector_config_by_alias=prepared["detector_configs"],
            first_visible_ring_by_alias=first_visible,
            rings_to_show=settings.get("rings_to_search_by_alias")
            or int(auto_cfg.get("rings_to_show", 8)),
            parent=self,
        )
        decision = ""
        if isinstance(decision_payload, dict):
            decision = str(decision_payload.get("decision") or "").strip().lower()

        if decision == "validate":
            return self._validate_auto_poni_reviews(reviews)
        if decision == "correct":
            return self._launch_pyfai_reviews(reviews)
        self._log_technical_event("Auto PONI cancelled")
        return False
