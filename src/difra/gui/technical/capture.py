from __future__ import annotations

import os
import json
import shutil
import logging
from pathlib import Path
from collections import Counter
from typing import Optional

import numpy as np
import seaborn as sns
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from difra.gui.container_api import get_container_version
from difra.gui.main_window_ext.technical.poni_center_preview import (
    rule_with_zone,
    resolve_overlay_zone,
    resolve_preview_limits,
)
from difra.gui.technical.analysis_compat import (
    create_mask,
    initialize_azimuthal_integrator_df,
    initialize_azimuthal_integrator_poni_text,
)
logger = logging.getLogger(__name__)
_PONI_RANGE_EDIT_PASSWORD = "Ulster2026!"


def _dsc_candidates(path: Path):
    path = Path(path)
    candidates = [Path(str(path) + ".dsc"), path.with_suffix(".dsc")]
    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        yield candidate


def _resolve_poni_validation_config_target(parent) -> Optional[Path]:
    for attr_name in ("_active_config_path", "_global_path", "_legacy_main_path"):
        candidate = getattr(parent, attr_name, None)
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _load_json_payload(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_poni_validation_rule_edits(
    *,
    parent,
    validation_cfg: dict,
    edited_rules_by_alias: dict,
) -> Path:
    target_path = _resolve_poni_validation_config_target(parent)
    if target_path is None:
        raise RuntimeError("Active setup config file is not available.")

    payload = _load_json_payload(target_path)
    block = payload.get("poni_center_validation")
    if not isinstance(block, dict):
        block = dict(validation_cfg or {})
    detectors = block.get("detectors")
    if not isinstance(detectors, dict):
        detectors = {}
    for alias_key, rule in (edited_rules_by_alias or {}).items():
        detectors[str(alias_key).upper()] = dict(rule or {})
    block["detectors"] = detectors
    if "enabled" not in block:
        block["enabled"] = True
    payload["poni_center_validation"] = block
    target_path.write_text(json.dumps(payload, indent=4), encoding="utf-8")

    if parent is not None and hasattr(parent, "load_config"):
        try:
            parent.config = parent.load_config()
        except Exception:
            logger.warning("Failed to reload config after PONI range edit", exc_info=True)
    elif parent is not None and hasattr(parent, "config"):
        parent.config = dict(getattr(parent, "config", {}) or {})
        parent.config["poni_center_validation"] = block

    return target_path


def _decode_h5_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _inspect_embedded_poni(
    measurement_filename: str,
    provided_poni_text: str | None = None,
) -> dict:
    info = {
        "measurement_ref": str(measurement_filename or "").strip(),
        "container_path": "",
        "measurement_dataset_path": "",
        "detector_group_path": "",
        "detector_alias": "",
        "detector_id": "",
        "measurement_source_file": "",
        "resolved_poni_path": "",
        "resolved_poni_filename": "",
        "resolved_poni_text": "",
        "provided_poni_text": str(provided_poni_text or ""),
        "resolution_error": "",
    }

    ref_value = info["measurement_ref"]
    if not ref_value.startswith("h5ref://"):
        return info

    try:
        import h5py
    except Exception as exc:
        info["resolution_error"] = f"h5py unavailable: {exc}"
        return info

    payload = ref_value[len("h5ref://") :]
    container_path, sep, dataset_path = payload.partition("#")
    info["container_path"] = str(container_path or "").strip()
    info["measurement_dataset_path"] = str(dataset_path or "").strip()
    if not sep or not info["container_path"] or not info["measurement_dataset_path"]:
        info["resolution_error"] = f"Invalid H5 reference: {measurement_filename}"
        return info

    try:
        with h5py.File(info["container_path"], "r") as h5f:
            if info["measurement_dataset_path"] not in h5f:
                info["resolution_error"] = (
                    "Measurement dataset not found in container: "
                    f"{info['measurement_dataset_path']}"
                )
                return info

            dataset = h5f[info["measurement_dataset_path"]]
            detector_group = dataset.parent
            info["detector_group_path"] = str(detector_group.name or "")
            info["detector_alias"] = _decode_h5_text(
                detector_group.attrs.get("detector_alias", "")
            ).strip()
            info["detector_id"] = _decode_h5_text(
                detector_group.attrs.get("detector_id", "")
            ).strip()
            info["measurement_source_file"] = _decode_h5_text(
                detector_group.attrs.get("source_file", "")
            ).strip()

            candidate_paths = []
            for attr_name in ("poni_ref", "poni_path"):
                ref_path = _decode_h5_text(detector_group.attrs.get(attr_name, "")).strip()
                if ref_path and ref_path not in candidate_paths:
                    candidate_paths.append(ref_path)

            role_name = str(detector_group.name.rsplit("/", 1)[-1] or "").strip()
            if role_name.startswith("det_"):
                for suffix in (role_name[4:], role_name):
                    canonical_path = f"/entry/technical/poni/poni_{suffix}"
                    if canonical_path not in candidate_paths:
                        candidate_paths.append(canonical_path)

            for ref_path in candidate_paths:
                if not ref_path or ref_path not in h5f:
                    continue
                try:
                    poni_dataset = h5f[ref_path]
                    info["resolved_poni_path"] = ref_path
                    info["resolved_poni_filename"] = _decode_h5_text(
                        poni_dataset.attrs.get("poni_filename", "")
                    ).strip()
                    info["resolved_poni_text"] = _decode_h5_text(poni_dataset[()]).strip()
                    if info["resolved_poni_text"]:
                        break
                except Exception as exc:
                    info["resolution_error"] = f"Failed reading PONI dataset '{ref_path}': {exc}"
    except Exception as exc:
        info["resolution_error"] = f"Failed reading H5 diagnostics: {exc}"

    return info


def _format_measurement_diagnostics(
    *,
    measurement_filename: str,
    poni_info: dict,
    integration_error: str = "",
) -> str:
    info = poni_info if isinstance(poni_info, dict) else {}
    provided_poni_text = str(info.get("provided_poni_text") or "").strip()
    embedded_poni_text = str(info.get("resolved_poni_text") or "").strip()

    lines = [f"Measurement source: {str(measurement_filename or '').strip() or '<empty>'}"]

    container_path = str(info.get("container_path") or "").strip()
    if container_path:
        lines.append(f"Container: {container_path}")

    dataset_path = str(info.get("measurement_dataset_path") or "").strip()
    if dataset_path:
        lines.append(f"Measurement dataset: {dataset_path}")

    detector_group_path = str(info.get("detector_group_path") or "").strip()
    if detector_group_path:
        lines.append(f"Detector group: {detector_group_path}")

    detector_alias = str(info.get("detector_alias") or "").strip()
    if detector_alias:
        lines.append(f"Detector alias: {detector_alias}")

    detector_id = str(info.get("detector_id") or "").strip()
    if detector_id:
        lines.append(f"Detector id: {detector_id}")

    measurement_source_file = str(info.get("measurement_source_file") or "").strip()
    if measurement_source_file:
        lines.append(f"Measurement source_file: {measurement_source_file}")

    resolved_poni_path = str(info.get("resolved_poni_path") or "").strip()
    if resolved_poni_path:
        lines.append(f"Embedded PONI dataset: {resolved_poni_path}")

    resolved_poni_filename = str(info.get("resolved_poni_filename") or "").strip()
    if resolved_poni_filename:
        lines.append(f"Embedded PONI filename: {resolved_poni_filename}")

    if embedded_poni_text and provided_poni_text:
        if embedded_poni_text == provided_poni_text:
            lines.append("PONI payload used for integration: embedded container PONI (matches caller text)")
        else:
            lines.append("PONI payload used for integration: embedded container PONI (caller text differs)")
    elif embedded_poni_text:
        lines.append("PONI payload used for integration: embedded container PONI")
    elif provided_poni_text:
        lines.append("PONI payload used for integration: caller-provided text")
    else:
        lines.append("PONI payload used for integration: unavailable")

    resolution_error = str(info.get("resolution_error") or "").strip()
    if resolution_error:
        lines.append(f"PONI resolution issue: {resolution_error}")

    if integration_error:
        lines.append(f"Integration error: {integration_error}")

    if embedded_poni_text:
        lines.extend(["", "--- Embedded PONI text ---", embedded_poni_text])
    elif provided_poni_text:
        lines.extend(["", "--- Caller-provided PONI text ---", provided_poni_text])

    return "\n".join(lines).strip()


def _build_measurement_dialog(
    measurement_filename: str,
    *,
    parent=None,
    note: str = "",
    diagnostics_text: str = "",
) -> tuple[QDialog, Figure]:
    dialog = QDialog(parent)
    dialog.setWindowTitle(
        f"Azimuthal Integration: {os.path.basename(measurement_filename)}"
    )
    layout = QVBoxLayout(dialog)
    if str(note or "").strip():
        note_label = QLabel(str(note).strip())
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(note_label)

    if str(diagnostics_text or "").strip():
        diagnostics_label = QLabel("Diagnostics / PONI")
        diagnostics_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(diagnostics_label)

        diagnostics_box = QPlainTextEdit()
        diagnostics_box.setObjectName("measurementDiagnosticsText")
        diagnostics_box.setReadOnly(True)
        diagnostics_box.setPlainText(str(diagnostics_text).strip())
        diagnostics_box.setMinimumHeight(220)
        layout.addWidget(diagnostics_box)

    fig = Figure(figsize=(6, 6))
    canvas = FigureCanvas(fig)
    layout.addWidget(canvas)
    dialog.resize(820, 920 if str(diagnostics_text or "").strip() else 700)
    dialog.show()
    return dialog, fig


def _load_measurement_array(measurement_filename: str) -> np.ndarray:
    value = str(measurement_filename or "").strip()
    if value.startswith("h5ref://"):
        # Format: h5ref://<absolute-container-path>#<dataset_path>
        import h5py

        payload = value[len("h5ref://") :]
        container_path, sep, dataset_path = payload.partition("#")
        if not sep or not container_path or not dataset_path:
            raise ValueError(f"Invalid H5 reference: {measurement_filename}")

        container = Path(container_path)
        if not container.exists():
            raise FileNotFoundError(f"H5 container does not exist: {container}")

        with h5py.File(container, "r") as h5f:
            if dataset_path not in h5f:
                raise KeyError(
                    f"Dataset not found in container: {container}#{dataset_path}"
                )
            data = h5f[dataset_path][()]
            arr = np.asarray(data, dtype=float)
            if arr.ndim != 2:
                raise ValueError(f"Expected 2D array, got shape {arr.shape}")
            return arr

    path = Path(value)
    if not path.exists():
        raise FileNotFoundError(f"Measurement file does not exist: {path}")

    suffix = path.suffix.lower()
    if suffix == ".txt":
        loaders = (np.loadtxt, np.load)
    elif suffix == ".npy":
        loaders = (np.load, np.loadtxt)
    else:
        loaders = (np.load, np.loadtxt)

    last_error = None
    for loader in loaders:
        try:
            data = loader(path)
            arr = np.asarray(data, dtype=float)
            if arr.ndim != 2:
                raise ValueError(f"Expected 2D array, got shape {arr.shape}")
            return arr
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Failed to load measurement file '{path}': {last_error}")


def _place_raw_capture_file(src_raw: str, target_txt: Path, allow_move: bool = True) -> None:
    """Place raw detector output at target path, preferring move over copy."""
    src_path = Path(src_raw)
    target_txt = Path(target_txt)
    target_txt.parent.mkdir(parents=True, exist_ok=True)
    dst_dsc = Path(str(target_txt) + ".dsc")
    src_dsc = next((path for path in _dsc_candidates(src_path) if path.exists()), None)

    if src_path.resolve() == target_txt.resolve():
        if src_dsc is not None and not dst_dsc.exists():
            shutil.copy2(src_dsc, dst_dsc)
        return

    moved = False
    if allow_move:
        try:
            shutil.move(str(src_path), str(target_txt))
            moved = True
        except Exception:
            moved = False

    if not moved:
        shutil.copy2(src_path, target_txt)

    if src_dsc is not None:
        if moved:
            try:
                shutil.move(str(src_dsc), str(dst_dsc))
            except Exception:
                shutil.copy2(src_dsc, dst_dsc)
        else:
            shutil.copy2(src_dsc, dst_dsc)


class CaptureWorker(QObject):
    finished = pyqtSignal(bool, dict)  # success, {alias: converted_file_path}

    def __init__(
        self,
        detector_controller,
        integration_time,
        txt_filename_base,
        parent=None,
        frames: int = 1,
        naming_mode: str = "normal",  # normal | attenuation_with | attenuation_without
        continuous_movement_controller=None,
        stage_controller=None,
        hardware_client=None,
        enable_continuous_movement: bool = False,
        movement_radius: float = 2.0,
        container_version: str = None,  # Container version for format conversion
    ):
        super().__init__(parent)
        self.detector_controller = detector_controller
        self.integration_time = integration_time
        self.txt_filename_base = txt_filename_base
        self.frames = frames
        self.naming_mode = naming_mode
        self.continuous_movement_controller = continuous_movement_controller
        self.stage_controller = stage_controller
        self.hardware_client = hardware_client
        self.enable_continuous_movement = enable_continuous_movement
        self.movement_radius = movement_radius
        self.container_version = container_version or get_container_version(None)
        self._stop_requested = False
        self.error_messages = []

    def _record_error(self, message: str, exc: Exception = None) -> None:
        self.error_messages.append(message)
        if exc is None:
            logger.error(message)
        else:
            logger.error("%s: %s", message, exc, exc_info=True)

    def run(self):
        results = {}
        movement_started = False

        # Determine if continuous movement should be used (checkbox-driven only)
        is_continuous_movement = (
            self.enable_continuous_movement
            and self.continuous_movement_controller
            and self.stage_controller
        )

        try:
            # Start continuous movement when enabled by the checkbox
            if is_continuous_movement:
                # Get current stage position as center
                try:
                    center_x, center_y = self.stage_controller.get_xy_position()
                except Exception:
                    if self.hardware_client is not None:
                        center_x, center_y = self.hardware_client.get_xy_position()
                    else:
                        raise

                # Configure movement for the full acquisition duration (frames × integration time)
                total_duration = float(self.integration_time) * max(int(self.frames), 1)
                self.continuous_movement_controller.configure(
                    self.movement_radius, total_duration
                )

                movement_started = self.continuous_movement_controller.start_movement(
                    center_x, center_y
                )

                if movement_started:
                    logger.info(
                        "Started continuous movement for technical measurement "
                        "(center: %.3f, %.3f, radius: %.3fmm)",
                        center_x,
                        center_y,
                        float(self.movement_radius),
                    )
                else:
                    message = (
                        "Failed to start continuous movement for technical measurement"
                    )
                    logger.warning(message)
                    self.error_messages.append(message)

            if self.hardware_client is None:
                raise RuntimeError(
                    "Hardware client is required for capture; direct detector calls are disabled in GUI."
                )

            raw_outputs = self.hardware_client.capture_exposure(
                exposure_s=float(self.integration_time),
                frames=max(int(self.frames), 1),
                timeout_s=max(30.0, float(self.integration_time) * max(int(self.frames), 1) + 30.0),
            )

            source_usage = Counter()
            fallback_single = next(iter(raw_outputs.values())) if len(raw_outputs) == 1 else None
            for alias in self.detector_controller.keys():
                src_raw = raw_outputs.get(alias) or fallback_single
                if not src_raw:
                    continue
                try:
                    source_usage[str(Path(src_raw).resolve())] += 1
                except Exception:
                    source_usage[str(src_raw)] += 1

            for alias, controller in self.detector_controller.items():
                if self._stop_requested:
                    results[alias] = None
                    continue
                try:
                    if self.naming_mode == "attenuation_with":
                        base = f"{self.txt_filename_base}__{alias}_ATTENUATION"
                    elif self.naming_mode == "attenuation_without":
                        base = f"{self.txt_filename_base}__{alias}_ATTENUATION0"
                    else:
                        base = f"{self.txt_filename_base}_{alias}"

                    src_raw = raw_outputs.get(alias)
                    if src_raw is None and len(raw_outputs) == 1:
                        src_raw = next(iter(raw_outputs.values()))
                    if not src_raw:
                        self._record_error(
                            f"No raw output for detector '{alias}'. "
                            f"Available output aliases: {sorted(raw_outputs.keys())}"
                        )
                        results[alias] = None
                        continue

                    src_path = Path(src_raw)
                    target_txt = Path(base + ".txt")
                    key = str(src_path.resolve())
                    allow_move = source_usage.get(key, 0) <= 1
                    _place_raw_capture_file(src_raw=src_raw, target_txt=target_txt, allow_move=allow_move)
                    if key in source_usage and source_usage[key] > 0:
                        source_usage[key] -= 1

                    converted_file = controller.convert_to_container_format(
                        str(target_txt), self.container_version
                    )
                    results[alias] = converted_file
                    logger.info(
                        "Converted technical capture for %s: %s -> %s",
                        alias,
                        target_txt.name,
                        Path(converted_file).name,
                    )
                except Exception as e:
                    self._record_error(
                        f"Error while processing detector '{alias}' output",
                        e,
                    )
                    results[alias] = None

        except Exception as e:
            self._record_error("Error during capture operation", e)
            results = {alias: None for alias in self.detector_controller.keys()}

        finally:
            # Stop continuous movement if it was started
            if movement_started and self.continuous_movement_controller:
                try:
                    self.continuous_movement_controller.stop_movement(
                        return_to_origin=True
                    )
                    logger.info(
                        "Stopped continuous movement and returned to original position"
                    )
                except Exception as e:
                    self._record_error("Error stopping continuous movement", e)

        overall_success = (
            all(r is not None for r in results.values()) and not self._stop_requested
        )
        if not overall_success and not self.error_messages:
            self.error_messages.append("Capture failed without explicit error details.")
        self.finished.emit(overall_success, results)

    def stop(self):
        """Request the capture operation to stop."""
        self._stop_requested = True

        # Stop continuous movement immediately if active
        if (
            self.continuous_movement_controller
            and self.continuous_movement_controller.is_moving()
        ):
            try:
                self.continuous_movement_controller.stop_movement(return_to_origin=True)
                logger.info("Stopped continuous movement due to capture stop request")
            except Exception as e:
                self._record_error(
                    "Error stopping continuous movement during stop request", e
                )

def validate_folder(path: str):
    if not path:
        path = os.getcwd()
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        path = os.getcwd()
    if not os.access(path, os.W_OK):
        path = os.getcwd()
    return Path(path)


def show_measurement_window(
    measurement_filename: str,
    mask: np.ndarray,
    poni_text: str = None,
    parent=None,
    columns_to_remove: int = 30,
    goodness: float = 0.0,
    center=None,  # <-- NEW
    integration_radius=None,  # <-- NEW
):
    """
    Opens a dialog window displaying the raw 2D image and its azimuthal integration.
    Optionally overlays the beam center and integration region.
    """
    import matplotlib.pyplot as plt

    # Load data
    data = _load_measurement_array(measurement_filename)
    poni_info = _inspect_embedded_poni(measurement_filename, poni_text)
    active_poni_text = str(poni_info.get("resolved_poni_text") or poni_text or "").strip()

    radial = intensity = std = sigma = cake = None
    integration_error = ""
    integration_note = ""

    def _run_integration(active_mask):
        ai = initialize_azimuthal_integrator_poni_text(active_poni_text)
        result = ai.integrate1d(
            data, 200, unit="q_nm^-1", error_model="azimuthal", mask=active_mask
        )
        local_radial = np.asarray(result.radial, dtype=float).reshape(-1)
        local_intensity = np.asarray(result.intensity, dtype=float).reshape(-1)
        local_std = np.asarray(result.std, dtype=float).reshape(-1)
        local_sigma = np.asarray(result.sigma, dtype=float).reshape(-1)

        min_len = min(
            local_radial.size,
            local_intensity.size,
            local_std.size,
            local_sigma.size,
        )
        if min_len <= 0:
            raise ValueError("Integration produced empty radial/intensity arrays")
        local_radial = local_radial[:min_len]
        local_intensity = local_intensity[:min_len]
        local_std = local_std[:min_len]
        local_sigma = local_sigma[:min_len]

        finite = np.isfinite(local_radial) & np.isfinite(local_intensity)
        if not np.any(finite):
            raise ValueError("Integration produced only NaN/Inf values")
        local_radial = local_radial[finite]
        local_intensity = local_intensity[finite]
        local_std = local_std[finite]
        local_sigma = local_sigma[finite]
        local_cake, _, _ = ai.integrate2d(data, 200, npt_azim=180, mask=active_mask)
        return local_radial, local_intensity, local_std, local_sigma, local_cake

    if active_poni_text:
        try:
            radial, intensity, std, sigma, cake = _run_integration(mask)
        except Exception as exc:
            first_error = str(exc)
            if mask is not None:
                try:
                    radial, intensity, std, sigma, cake = _run_integration(None)
                    integration_note = (
                        "Masked integration failed; retried without mask.\n"
                        f"Original reason: {first_error}"
                    )
                except Exception as second_exc:
                    integration_error = str(second_exc)
                    logger.warning(
                        "Falling back to raw-only technical view; integration failed for %s "
                        "with mask and without mask: %s | %s",
                        measurement_filename,
                        first_error,
                        second_exc,
                    )
            else:
                integration_error = first_error
                logger.warning(
                    "Falling back to raw-only technical view; integration failed for %s: %s",
                    measurement_filename,
                    exc,
                )

    note = ""
    if not active_poni_text:
        note = "No PONI is embedded for this measurement. Showing raw image only."
    elif integration_note:
        note = integration_note
    elif integration_error:
        note = (
            "Could not integrate this measurement with the current PONI. "
            f"Showing raw image only.\nReason: {integration_error}"
        )

    diagnostics_text = _format_measurement_diagnostics(
        measurement_filename=measurement_filename,
        poni_info=poni_info,
        integration_error=integration_error,
    )

    dialog, fig = _build_measurement_dialog(
        measurement_filename,
        parent=parent,
        note=note,
        diagnostics_text=diagnostics_text,
    )

    # Top-left: raw 2D heatmap
    integrated_view = radial is not None and intensity is not None and cake is not None
    if integrated_view:
        ax1 = fig.add_subplot(2, 2, 1)
    else:
        ax1 = fig.add_subplot(1, 1, 1)
    sns.heatmap(data, robust=True, square=True, ax=ax1, cbar=False)
    ax1.set_title("2D Image")

    # === Overlay beam center and integration region ===
    if center is not None:
        cy, cx = center
        ax1.plot(
            [cx],
            [cy],
            marker="x",
            color="red",
            markersize=10,
            label="Beam center",
        )
        if integration_radius is not None and integration_radius > 0:
            from matplotlib.patches import Circle

            circ = Circle(
                (cx, cy),
                integration_radius,
                edgecolor="red",
                facecolor="none",
                lw=3,
                ls="--",
                label="Integration area",
            )
            ax1.add_patch(circ)

    if not integrated_view:
        return dialog

    # Top-right: 1D integration
    from mpl_toolkits.axes_grid1.inset_locator import inset_axes

    ax2 = fig.add_subplot(2, 2, 2)
    ax2.errorbar(
        radial,
        intensity,
        yerr=np.where(np.isfinite(sigma) & (sigma >= 0), sigma, np.nan),
        fmt="-o",
        markersize=3,
        linewidth=1,
        ecolor="black",
        capsize=3,
        capthick=1,
        label="Intensity ± σ",
    )
    xmin = float(np.nanmin(radial))
    xmax = float(np.nanmax(radial))
    xright = xmax * 1.3 if xmax > 0 else xmax + 1.0
    if (not np.isfinite(xmin)) or (not np.isfinite(xright)) or (xright <= xmin):
        xleft = 0.0
        xright = max(1.0, abs(xmax)) * 1.3
    else:
        xleft = xmin
    ax2.set_xlim(xleft, xright)
    if np.any(np.isfinite(intensity) & (intensity > 0)):
        ax2.set_yscale("log")
    else:
        ax2.set_yscale("linear")
    ax2.set_title("Azimuthal Integration")
    ax2.set_xlabel("q (nm⁻¹)")
    ax2.set_ylabel("Intensity")
    ax2.legend(loc="upper right", fontsize="small")

    # 3) inset for std (top-left)
    ax_std = inset_axes(
        ax2,
        width="30%",
        height="30%",
        bbox_to_anchor=(0.05, -0.2, 1, 1),
        bbox_transform=ax2.transAxes,
    )
    ax_std.plot(radial, std, "-", linewidth=1)
    ax_std.set_title("std", fontsize="x-small")
    ax_std.tick_params(labelsize="x-small", axis="both", which="both")

    # 4) inset for SNR = I / σ (below the std inset)
    safe_sigma = np.where(np.isfinite(sigma) & (sigma > 0), sigma, np.nan)
    snr = np.divide(
        intensity,
        safe_sigma,
        out=np.full_like(intensity, np.nan, dtype=float),
        where=np.isfinite(safe_sigma),
    )
    ax_snr = inset_axes(
        ax2,
        width="30%",
        height="30%",
        bbox_to_anchor=(0.05, -0.5, 1, 1),
        bbox_transform=ax2.transAxes,
    )
    ax_snr.plot(radial, snr, "-", linewidth=1)
    ax_snr.set_title("SNR", fontsize="x-small")
    ax_snr.tick_params(labelsize="x-small", axis="both", which="both")

    # Bottom-left: cake representation
    ax3 = fig.add_subplot(2, 2, 3)
    sns.heatmap(cake[:, 30:], robust=True, square=True, ax=ax3)
    ax3.set_title("Cake Representation")

    # Bottom-right: deviation map
    cake2 = cake[:, columns_to_remove:]
    mask_zero = cake2 == 0
    col_sums = cake2.sum(axis=0)
    valid_counts = (~mask_zero).sum(axis=0)
    col_means = np.divide(col_sums, valid_counts, where=valid_counts > 0)
    pct_dev = (cake2 - col_means[np.newaxis, :]) / col_means[np.newaxis, :] * 100

    ax4 = fig.add_subplot(2, 2, 4)
    sns.heatmap(pct_dev, robust=True, square=True, ax=ax4)
    ax4.set_title(f"Deviation (%), goodness: {goodness}")

    return dialog


def show_poni_centers_preview_window(
    *,
    aliases,
    poni_by_alias: dict,
    detector_sizes_by_alias: dict,
    validation_cfg: dict,
    agbh_images_by_alias: Optional[dict] = None,
    decision_mode: bool = False,
    parent=None,
):
    """Show detector previews with PONI centers and allowed center zones."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.widgets import RectangleSelector

    from difra.gui.main_window_ext.technical.poni_center_validation import (
        evaluate_poni_centers,
        parse_poni_center_px,
    )

    aliases = [str(a) for a in aliases if str(a or "").strip()]
    if not aliases:
        return None

    data_by_alias = agbh_images_by_alias if isinstance(agbh_images_by_alias, dict) else {}
    detector_rules = {}
    if isinstance(validation_cfg, dict):
        rules = validation_cfg.get("detectors", {})
        if isinstance(rules, dict):
            detector_rules = {str(k).upper(): v for k, v in rules.items()}
        defaults = validation_cfg.get("defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}
    else:
        defaults = {}

    cols = len(aliases)
    fig = Figure(figsize=(4.5 * cols, 4.2))
    canvas = FigureCanvas(fig)
    axes = fig.subplots(1, cols)
    if cols == 1:
        axes = [axes]

    zone_patches = {}
    rules_by_alias = {}
    zones_by_alias = {}
    axes_by_alias = {}
    status_by_alias = {
        str(item.get("alias") or "").upper(): item
        for item in evaluate_poni_centers(
            poni_text_by_alias=poni_by_alias,
            detector_sizes_by_alias=detector_sizes_by_alias,
            validation_config=validation_cfg,
        )
    }

    for ax, alias in zip(axes, aliases):
        alias_key = str(alias).upper()
        axes_by_alias[alias_key] = ax
        size = detector_sizes_by_alias.get(alias) or detector_sizes_by_alias.get(alias_key) or (256, 256)
        try:
            width_px = int(size[0])
            height_px = int(size[1])
        except Exception:
            width_px, height_px = 256, 256

        raw_data = data_by_alias.get(alias)
        if raw_data is None:
            raw_data = data_by_alias.get(alias_key)
        if raw_data is None:
            img = np.zeros((height_px, width_px), dtype=float)
            source_label = "fake detector square"
        else:
            img = np.asarray(raw_data, dtype=float)
            if img.ndim != 2:
                img = np.zeros((height_px, width_px), dtype=float)
                source_label = "fake detector square"
            else:
                source_label = "AGBH"

        h, w = img.shape
        ax.imshow(
            img,
            origin="lower",
            cmap="gray",
            aspect="equal",
            extent=(0.0, float(w), 0.0, float(h)),
        )
        ax.set_title(f"{alias} ({source_label})")
        ax.set_xlabel("col (px)")
        ax.set_ylabel("row (px)")

        rule = {}
        if alias_key in detector_rules and isinstance(detector_rules[alias_key], dict):
            rule = dict(defaults)
            rule.update(detector_rules[alias_key])
        elif isinstance(defaults, dict):
            rule = dict(defaults)
        rules_by_alias[alias_key] = dict(rule)

        zone = resolve_overlay_zone(rule, w, h)
        zones_by_alias[alias_key] = zone
        if zone is not None:
            rect = Rectangle(
                (zone[0], zone[1]),
                zone[2],
                zone[3],
                facecolor=(0.58, 0.28, 0.78, 0.25),
                edgecolor=(0.58, 0.28, 0.78, 0.8),
                linewidth=1.5,
            )
            ax.add_patch(rect)
            zone_patches[alias_key] = rect

        poni_text = str(poni_by_alias.get(alias) or poni_by_alias.get(alias_key) or "")
        center = parse_poni_center_px(poni_text, fallback_detector_size=(w, h))
        if center is not None:
            ax.plot(
                [float(center["col_px"])],
                [float(center["row_px"])],
                marker="o",
                markersize=6,
                markerfacecolor="red",
                markeredgecolor="white",
                markeredgewidth=0.8,
            )

        status_info = status_by_alias.get(alias_key, {})
        if isinstance(status_info, dict):
            status_label = "IN ZONE" if bool(status_info.get("in_zone")) else "OUT OF ZONE"
            color = "#1b7f3b" if bool(status_info.get("in_zone")) else "#b42318"
            geometry = status_info.get("geometry") or {}
            row_text = geometry.get("row_px")
            col_text = geometry.get("col_px")
            status_lines = [status_label]
            if row_text is not None and col_text is not None:
                status_lines.append(f"row={float(row_text):.2f}, col={float(col_text):.2f}")
            summary = status_info.get("rule_summary") or []
            if summary:
                status_lines.append("; ".join(summary[:2]))
            ax.text(
                0.02,
                0.98,
                "\n".join(status_lines),
                transform=ax.transAxes,
                va="top",
                ha="left",
                fontsize=8.5,
                color=color,
                bbox=dict(facecolor=(1, 1, 1, 0.72), edgecolor=color, linewidth=0.8),
            )

        detector_frame = Rectangle(
            (0.0, 0.0),
            float(w),
            float(h),
            facecolor="none",
            edgecolor=(1.0, 1.0, 1.0, 0.55),
            linewidth=1.0,
            linestyle="--",
        )
        ax.add_patch(detector_frame)

        x_min, x_max, y_min, y_max = resolve_preview_limits(
            width_px=w,
            height_px=h,
            zone=zone,
            center=center,
        )
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(y_min, y_max)

    fig.tight_layout()

    dialog = QDialog(parent)
    dialog.setWindowTitle("PONI Centers: PRIMARY/SECONDARY")
    if decision_mode:
        dialog.setModal(True)

    layout = QVBoxLayout(dialog)
    layout.addWidget(canvas)

    help_label = QLabel(dialog)
    help_label.setWordWrap(True)
    help_label.setStyleSheet("color: #555; font-size: 11px;")
    help_label.setText(
        "Purple rectangles show allowed PONI beam-center ranges. "
        "Use 'Unlock Editing…' to drag/resize them with the mouse; OK/Accept will save updates to the active setup config."
    )
    layout.addWidget(help_label)

    selectors = {}
    editing_enabled = {"value": False}

    def _apply_selector_style(selector):
        artist = getattr(selector, "_selection_artist", None)
        if artist is not None:
            artist.set_facecolor((0.58, 0.28, 0.78, 0.25))
            artist.set_edgecolor((0.58, 0.28, 0.78, 0.9))
            artist.set_linewidth(1.6)
        handles = getattr(selector, "_corner_handles", None)
        if handles is not None:
            try:
                handles.artist.set_markerfacecolor((0.58, 0.28, 0.78, 0.95))
                handles.artist.set_markeredgecolor("white")
            except Exception:
                pass

    def _selector_for_alias(alias_key: str):
        selector = selectors.get(alias_key)
        if selector is not None:
            return selector
        ax = axes_by_alias.get(alias_key)
        zone = zones_by_alias.get(alias_key)
        if ax is None or zone is None:
            return None

        x0, y0, zone_w, zone_h = zone
        selector_kwargs = dict(
            useblit=False,
            button=[1],
            interactive=True,
            minspanx=1.0,
            minspany=1.0,
            spancoords="data",
        )
        try:
            selector = RectangleSelector(
                ax,
                lambda *_args, **_kwargs: None,
                drag_from_anywhere=True,
                props=dict(
                    facecolor=(0.58, 0.28, 0.78, 0.25),
                    edgecolor=(0.58, 0.28, 0.78, 0.9),
                    linewidth=1.6,
                ),
                **selector_kwargs,
            )
        except TypeError:
            try:
                selector = RectangleSelector(
                    ax,
                    lambda *_args, **_kwargs: None,
                    rectprops=dict(
                        facecolor=(0.58, 0.28, 0.78, 0.25),
                        edgecolor=(0.58, 0.28, 0.78, 0.9),
                        linewidth=1.6,
                    ),
                    **selector_kwargs,
                )
            except TypeError:
                selector = RectangleSelector(
                    ax,
                    lambda *_args, **_kwargs: None,
                    **selector_kwargs,
                )
                try:
                    selector.drag_from_anywhere = True
                except Exception:
                    pass

        selector.extents = (x0, x0 + zone_w, y0, y0 + zone_h)
        _apply_selector_style(selector)
        selectors[alias_key] = selector
        return selector

    def _unlock_editing():
        if editing_enabled["value"]:
            return
        password, ok = QInputDialog.getText(
            dialog,
            "Unlock PONI Range Editing",
            "Enter password to edit allowed PONI ranges:",
            QLineEdit.Password,
        )
        if not ok:
            return
        if str(password) != _PONI_RANGE_EDIT_PASSWORD:
            QMessageBox.warning(dialog, "Wrong Password", "Password is incorrect.")
            return
        for alias_key, patch in list(zone_patches.items()):
            if patch is not None:
                patch.set_visible(False)
            _selector_for_alias(alias_key)
        editing_enabled["value"] = True
        help_label.setText(
            "Editing unlocked. Drag inside a rectangle to move it, or drag its edges/corners to resize it. "
            "Click OK/Accept to save the updated ranges to the active setup config."
        )
        canvas.draw_idle()

    def _save_current_edits() -> bool:
        if not editing_enabled["value"]:
            return True
        edited_rules_by_alias = {}
        for alias_key, selector in selectors.items():
            try:
                x1, x2, y1, y2 = selector.extents
            except Exception:
                continue
            zone = (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
            edited_rules_by_alias[alias_key] = rule_with_zone(
                rules_by_alias.get(alias_key, {}),
                zone,
            )
        if not edited_rules_by_alias:
            return True
        try:
            target_path = _save_poni_validation_rule_edits(
                parent=parent,
                validation_cfg=validation_cfg,
                edited_rules_by_alias=edited_rules_by_alias,
            )
        except Exception as exc:
            QMessageBox.warning(
                dialog,
                "Save Failed",
                f"Could not update PONI range config:\n{exc}",
            )
            return False
        QMessageBox.information(
            dialog,
            "PONI Ranges Saved",
            f"Updated PONI range rules in:\n{target_path}",
        )
        return True

    if decision_mode:
        decision_buttons = QDialogButtonBox(dialog)
        unlock_btn = decision_buttons.addButton("Unlock Editing…", QDialogButtonBox.ActionRole)
        accept_btn = decision_buttons.addButton("Accept", QDialogButtonBox.AcceptRole)
        reject_btn = decision_buttons.addButton("Reject", QDialogButtonBox.RejectRole)
        unlock_btn.clicked.connect(_unlock_editing)
        def _accept_and_maybe_save():
            if _save_current_edits():
                dialog.accept()
        accept_btn.clicked.connect(_accept_and_maybe_save)
        reject_btn.clicked.connect(dialog.reject)
        layout.addWidget(decision_buttons)
    else:
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        unlock_btn = buttons.addButton("Unlock Editing…", QDialogButtonBox.ActionRole)
        unlock_btn.clicked.connect(_unlock_editing)
        def _ok_and_maybe_save():
            if _save_current_edits():
                dialog.accept()
        buttons.accepted.connect(_ok_and_maybe_save)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

    dialog.resize(max(640, 460 * cols), 420)
    dialog._poni_zone_selectors = selectors
    if decision_mode:
        result = dialog.exec_()
        return {"dialog": dialog, "accepted": bool(result == QDialog.Accepted)}

    dialog.show()
    return dialog


def show_auto_poni_review_window(
    *,
    aliases,
    review_by_alias: dict,
    images_by_alias: dict,
    detector_config_by_alias: dict,
    first_visible_ring_by_alias: dict,
    rings_to_show: int = 8,
    parent=None,
):
    """Show AgBh heatmaps, cake plots, and 1D integration with ring markers."""
    from matplotlib.patches import Circle

    from difra.gui.technical.pyfai_calibration import (
        AGBH_D_SPACING_A,
        DEFAULT_WAVELENGTH_M,
        build_agbh_ring_overlays,
        build_pyfai_calib2_command,
        parse_poni_parameters,
        refine_poni_from_clicked_ring_points,
        ring_two_theta_rad,
        write_agbh_clicked_points_npt,
    )

    aliases = [str(alias) for alias in aliases if str(alias or "").strip()]
    if not aliases:
        return {"decision": "cancel", "dialog": None}

    cols = len(aliases)

    def _rings_to_show_for_alias(alias: str) -> int:
        alias_key = str(alias or "").strip().upper()
        if isinstance(rings_to_show, dict):
            for key in (alias, alias_key):
                try:
                    count = int(rings_to_show.get(key))
                except (TypeError, ValueError):
                    continue
                if count > 0:
                    return count
        try:
            return max(1, int(rings_to_show))
        except (TypeError, ValueError):
            return 3

    def _ring_positions_deg(poni_text: str, first_ring: int, count: int):
        params = parse_poni_parameters(poni_text)
        wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
        positions = []
        start = max(1, int(first_ring or 1))
        stop = min(len(AGBH_D_SPACING_A), start + max(1, int(count or 1)) - 1)
        for ring_index in range(start, stop + 1):
            two_theta = ring_two_theta_rad(
                wavelength_m=wavelength_m,
                d_spacing_a=AGBH_D_SPACING_A[ring_index - 1],
            )
            if two_theta is None:
                continue
            positions.append((ring_index, float(np.degrees(two_theta))))
        return positions

    def _integrate_with_poni(review, data):
        try:
            import pyFAI

            poni_path = Path(getattr(review, "poni_path", "") or "")
            if not poni_path.exists():
                return None, None
            integrator = pyFAI.load(str(poni_path))
            cake = integrator.integrate2d(
                data,
                500,
                180,
                unit="2th_deg",
                method=("bbox", "csr", "cython"),
            )
            curve = integrator.integrate1d(
                data,
                800,
                unit="2th_deg",
                method=("bbox", "csr", "cython"),
            )
            return cake, curve
        except Exception:
            logger.warning("Failed to compute Auto PONI integrations", exc_info=True)
            return None, None

    def _command_with_npt(command, npt_path: Path):
        clean = []
        skip = False
        for part in list(command or []):
            if skip:
                skip = False
                continue
            if part == "-n":
                skip = True
                continue
            clean.append(part)
        if not clean:
            return []
        return [*clean[:-1], "-n", str(npt_path), clean[-1]]

    def _snap_to_peak(data, col: float, row: float, radius: int = 6):
        arr = np.asarray(data, dtype=float)
        height, width = arr.shape
        col_i = int(round(float(col)))
        row_i = int(round(float(row)))
        col_i = min(max(col_i, 0), width - 1)
        row_i = min(max(row_i, 0), height - 1)
        x0 = max(0, col_i - radius)
        x1 = min(width, col_i + radius + 1)
        y0 = max(0, row_i - radius)
        y1 = min(height, row_i + radius + 1)
        window = arr[y0:y1, x0:x1]
        if window.size == 0 or not np.isfinite(window).any():
            return float(col_i), float(row_i)
        safe = np.nan_to_num(window, nan=-np.inf)
        local_row, local_col = np.unravel_index(int(np.argmax(safe)), safe.shape)
        return float(x0 + local_col), float(y0 + local_row)

    fig = Figure(figsize=(5.2 * cols, 10.8))
    canvas = FigureCanvas(fig)
    axes = fig.subplots(3, cols, squeeze=False)
    if cols == 1:
        axes = np.asarray(axes).reshape(3, 1)
    axis_to_alias = {}
    image_data_by_alias = {}
    detector_state_by_alias = {}
    first_ring_by_alias = {}
    manual_points_by_alias = {}
    manual_artists_by_alias = {}
    review_state_by_alias = {}
    base_review_by_alias = {}
    top_axes_by_alias = {}
    cake_axes_by_alias = {}
    curve_axes_by_alias = {}
    overlay_artists_by_alias = {}
    full_view_by_alias = {}
    status = {"label": None, "last_alias": None}
    drag_state = {"alias": None, "index": None, "artist": None}

    def _draw_ring_overlays(alias: str):
        ax = top_axes_by_alias.get(alias)
        review = review_state_by_alias.get(alias)
        if ax is None or review is None:
            return
        for artist in overlay_artists_by_alias.get(alias, []):
            try:
                artist.remove()
            except Exception:
                pass
        overlay_artists_by_alias[alias] = []
        first_ring = int(first_ring_by_alias.get(alias, 1) or 1)
        overlays = build_agbh_ring_overlays(
            poni_text=str(getattr(review, "poni_text", "") or ""),
            detector_config=detector_state_by_alias.get(alias, {}),
            first_visible_ring=first_ring,
            rings_to_show=_rings_to_show_for_alias(alias),
        )
        for overlay in overlays:
            ring_index = int(overlay["ring_index"])
            circle = Circle(
                (
                    float(overlay["center_col_px"]),
                    float(overlay["center_row_px"]),
                ),
                float(overlay["radius_px"]),
                fill=False,
                linewidth=1.15 if ring_index == first_ring else 0.85,
                edgecolor="#35d0ff" if ring_index == first_ring else "#f9f871",
                alpha=0.95 if ring_index == first_ring else 0.78,
            )
            ax.add_patch(circle)
            overlay_artists_by_alias[alias].append(circle)
            if ring_index == first_ring:
                label = ax.text(
                    0.02,
                    0.98,
                    f"first visible ring: {ring_index}",
                    transform=ax.transAxes,
                    va="top",
                    ha="left",
                    fontsize=8.5,
                    color="#35d0ff",
                    bbox=dict(facecolor=(0, 0, 0, 0.55), edgecolor="#35d0ff", linewidth=0.7),
                )
                overlay_artists_by_alias[alias].append(label)

    def _draw_integrations(alias: str):
        cake_ax = cake_axes_by_alias.get(alias)
        curve_ax = curve_axes_by_alias.get(alias)
        review = review_state_by_alias.get(alias)
        data = image_data_by_alias.get(alias)
        if cake_ax is None or curve_ax is None or review is None or data is None:
            return
        first_ring = int(first_ring_by_alias.get(alias, 1) or 1)
        ring_positions = _ring_positions_deg(
            poni_text=str(getattr(review, "poni_text", "") or ""),
            first_ring=first_ring,
            count=_rings_to_show_for_alias(alias),
        )
        cake_ax.clear()
        curve_ax.clear()
        cake, curve = _integrate_with_poni(review, data)
        if cake is not None:
            cake_data = np.asarray(cake.intensity, dtype=float)
            cake_display = np.log1p(np.clip(cake_data, a_min=0.0, a_max=None))
            radial = np.asarray(cake.radial, dtype=float)
            azimuthal = np.asarray(cake.azimuthal, dtype=float)
            cake_ax.imshow(
                cake_display,
                origin="lower",
                aspect="auto",
                cmap="magma",
                extent=(
                    float(np.nanmin(radial)),
                    float(np.nanmax(radial)),
                    float(np.nanmin(azimuthal)),
                    float(np.nanmax(azimuthal)),
                ),
            )
            cake_ax.set_title(f"{alias} cake")
            cake_ax.set_xlabel("2theta (deg)")
            cake_ax.set_ylabel("azimuth (deg)")
            for ring_index, two_theta_deg in ring_positions:
                cake_ax.axvline(
                    two_theta_deg,
                    color="#35d0ff" if ring_index == first_ring else "#f9f871",
                    linewidth=1.0 if ring_index == first_ring else 0.75,
                    alpha=0.9,
                )
        else:
            cake_ax.set_title(f"{alias} cake unavailable")
            cake_ax.axis("off")
        if curve is not None:
            radial = np.asarray(curve.radial, dtype=float)
            intensity = np.asarray(curve.intensity, dtype=float)
            curve_ax.plot(radial, intensity, color="#35d0ff", linewidth=1.0)
            curve_ax.set_yscale("log")
            curve_ax.set_title(f"{alias} radial integration")
            curve_ax.set_xlabel("2theta (deg)")
            curve_ax.set_ylabel("I")
            for ring_index, two_theta_deg in ring_positions:
                curve_ax.axvline(
                    two_theta_deg,
                    color="#35d0ff" if ring_index == first_ring else "#f9f871",
                    linewidth=1.0 if ring_index == first_ring else 0.75,
                    alpha=0.9,
                )
                curve_ax.text(
                    two_theta_deg,
                    0.96,
                    str(ring_index),
                    transform=curve_ax.get_xaxis_transform(),
                    va="top",
                    ha="center",
                    fontsize=8,
                    color="#35d0ff" if ring_index == first_ring else "#f9f871",
                )
        else:
            curve_ax.set_title(f"{alias} radial integration unavailable")
            curve_ax.axis("off")

    for col_index, alias in enumerate(aliases):
        ax = axes[0, col_index]
        cake_ax = axes[1, col_index]
        curve_ax = axes[2, col_index]
        alias_key = str(alias).upper()
        review = review_by_alias.get(alias) or review_by_alias.get(alias_key)
        if review is None:
            continue
        detector_config = (
            detector_config_by_alias.get(alias)
            or detector_config_by_alias.get(alias_key)
            or {}
        )
        image = images_by_alias.get(alias) if isinstance(images_by_alias, dict) else None
        if image is None and isinstance(images_by_alias, dict):
            image = images_by_alias.get(alias_key)
        try:
            data = np.asarray(image, dtype=float)
            if data.ndim != 2:
                raise ValueError("non-2d")
        except Exception:
            data = np.zeros((256, 256), dtype=float)

        display = np.log1p(np.clip(data, a_min=0.0, a_max=None))
        height, width = display.shape
        ax.imshow(
            display,
            origin="lower",
            cmap="magma",
            aspect="equal",
            extent=(0.0, float(width), 0.0, float(height)),
        )
        ax.set_title(f"{alias} AgBh")
        ax.set_xlabel("col (px)")
        ax.set_ylabel("row (px)")

        first_ring = int(first_visible_ring_by_alias.get(alias_key, 1) or 1)
        axis_to_alias[ax] = alias
        top_axes_by_alias[alias] = ax
        cake_axes_by_alias[alias] = cake_ax
        curve_axes_by_alias[alias] = curve_ax
        image_data_by_alias[alias] = data
        detector_state_by_alias[alias] = detector_config
        first_ring_by_alias[alias] = first_ring
        manual_points_by_alias[alias] = []
        manual_artists_by_alias[alias] = []
        overlay_artists_by_alias[alias] = []
        review_state_by_alias[alias] = review
        base_review_by_alias[alias] = review
        poni_text = str(getattr(review, "poni_text", "") or "")
        overlays = build_agbh_ring_overlays(
            poni_text=poni_text,
            detector_config=detector_config,
            first_visible_ring=first_ring,
            rings_to_show=_rings_to_show_for_alias(alias),
        )
        for overlay in overlays:
            ring_index = int(overlay["ring_index"])
            circle = Circle(
                (
                    float(overlay["center_col_px"]),
                    float(overlay["center_row_px"]),
                ),
                float(overlay["radius_px"]),
                fill=False,
                linewidth=1.15 if ring_index == first_ring else 0.85,
                edgecolor="#35d0ff" if ring_index == first_ring else "#f9f871",
                alpha=0.95 if ring_index == first_ring else 0.78,
            )
            ax.add_patch(circle)
            overlay_artists_by_alias[alias].append(circle)
            if ring_index == first_ring:
                label = ax.text(
                    0.02,
                    0.98,
                    f"first visible ring: {ring_index}",
                    transform=ax.transAxes,
                    va="top",
                    ha="left",
                    fontsize=8.5,
                    color="#35d0ff",
                    bbox=dict(facecolor=(0, 0, 0, 0.55), edgecolor="#35d0ff", linewidth=0.7),
                )
                overlay_artists_by_alias[alias].append(label)

        ax.set_xlim(0.0, float(width))
        ax.set_ylim(0.0, float(height))
        full_view_by_alias[alias] = (0.0, float(width), 0.0, float(height))

        cake_ax.set_title(f"{alias} cake pending")
        cake_ax.axis("off")
        curve_ax.set_title(f"{alias} radial integration pending")
        curve_ax.axis("off")

    fig.tight_layout()

    def _draw_all_integrations():
        _set_status("Computing Auto PONI integrations...")
        for alias in aliases:
            _draw_integrations(alias)
            canvas.draw_idle()
        _set_status("Clicked ring points: none")

    def _save_clicked_points(alias: str):
        points = manual_points_by_alias.get(alias) or []
        review = review_state_by_alias.get(alias)
        if review is None:
            return None, False
        ring_index = int(first_ring_by_alias.get(alias, 1) or 1)
        output_dir = Path(getattr(review, "poni_path", "") or ".").parent
        refit = False
        if len(points) < 3:
            base_review = base_review_by_alias.get(alias)
            if base_review is not None:
                review = base_review
                review_state_by_alias[alias] = review
                review_by_alias[alias] = review
                review_by_alias[str(alias).upper()] = review
                _draw_ring_overlays(alias)
                _draw_integrations(alias)
            if not points:
                return None, False
        if len(points) >= 3:
            poni_text = refine_poni_from_clicked_ring_points(
                poni_text=str(getattr(review, "poni_text", "") or ""),
                detector_config=detector_state_by_alias.get(alias, {}),
                ring_index=ring_index,
                points_col_row=points,
                alias=alias,
            )
            poni_path = output_dir / f"{Path(str(review.image_path)).stem}_{alias}_clicked_ring_{ring_index}.poni"
            poni_path.write_text(poni_text, encoding="utf-8")
            review = type(review)(
                image_path=review.image_path,
                poni_path=poni_path,
                command=review.command,
                poni_text=poni_text,
                source_path=getattr(review, "source_path", None),
            )
            refit = True
        npt_path = output_dir / f"{Path(str(review.image_path)).stem}_{alias}_clicked_ring_{ring_index}.npt"
        write_agbh_clicked_points_npt(
            poni_text=str(getattr(review, "poni_text", "") or ""),
            output_path=npt_path,
            ring_index=ring_index,
            points_col_row=points,
            calibrant="AgBh",
        )
        command = build_pyfai_calib2_command(
            image_path=review.image_path,
            poni_text=review.poni_text,
            detector_config=detector_state_by_alias.get(alias, {}),
            calibrant="AgBh",
        )
        command = _command_with_npt(command, npt_path)
        updated = type(review)(
            image_path=review.image_path,
            poni_path=review.poni_path,
            command=command,
            poni_text=review.poni_text,
            source_path=getattr(review, "source_path", None),
        )
        review_state_by_alias[alias] = updated
        review_by_alias[alias] = updated
        review_by_alias[str(alias).upper()] = updated
        if refit:
            _draw_ring_overlays(alias)
            _draw_integrations(alias)
        return npt_path, refit

    def _set_status(text: str):
        label = status.get("label")
        if label is not None:
            label.setText(text)

    def _nearest_clicked_point(alias: str, event, max_screen_distance: float = 12.0):
        ax = top_axes_by_alias.get(alias)
        points = manual_points_by_alias.get(alias) or []
        if ax is None or not points:
            return None
        event_xy = np.asarray([float(event.x), float(event.y)])
        best = None
        best_distance = None
        for index, (col, row) in enumerate(points):
            point_xy = np.asarray(ax.transData.transform((col, row)), dtype=float)
            distance = float(np.linalg.norm(point_xy - event_xy))
            if best_distance is None or distance < best_distance:
                best = index
                best_distance = distance
        if best_distance is None or best_distance > float(max_screen_distance):
            return None
        return best

    def _delete_last_point(alias: str | None = None):
        target_alias = alias or status.get("last_alias")
        if not target_alias:
            _set_status("No clicked point to delete")
            return
        points = manual_points_by_alias.setdefault(target_alias, [])
        artists = manual_artists_by_alias.setdefault(target_alias, [])
        if not points:
            _set_status(f"{target_alias}: no clicked point to delete")
            return
        points.pop()
        if artists:
            artist = artists.pop()
            try:
                artist.remove()
            except Exception:
                pass
        try:
            npt_path, refit = _save_clicked_points(target_alias)
        except Exception as exc:
            _set_status(f"{target_alias}: clicked point refit failed: {exc}")
            canvas.draw_idle()
            return
        _set_status(
            f"{target_alias}: deleted last point; {len(points)} clicked points on ring "
            f"{first_ring_by_alias.get(target_alias, 1)}"
            + ("; refit" if refit else "; original geometry" if len(points) < 3 else "")
            + (f"; saved {npt_path}" if npt_path else "")
        )
        canvas.draw_idle()

    def _on_click(event):
        alias = axis_to_alias.get(event.inaxes)
        if not alias or event.xdata is None or event.ydata is None:
            return
        status["last_alias"] = alias
        if getattr(event, "dblclick", False):
            view = full_view_by_alias.get(alias)
            if view:
                event.inaxes.set_xlim(view[0], view[1])
                event.inaxes.set_ylim(view[2], view[3])
                _set_status(f"{alias}: zoom reset")
                canvas.draw_idle()
            return
        artists = manual_artists_by_alias.setdefault(alias, [])
        points = manual_points_by_alias.setdefault(alias, [])
        if event.button == 3:
            _delete_last_point(alias)
            return
        if event.button != 1:
            return

        point_index = _nearest_clicked_point(alias, event)
        if point_index is not None:
            drag_state["alias"] = alias
            drag_state["index"] = point_index
            drag_state["artist"] = (
                artists[point_index] if point_index < len(artists) else None
            )
            _set_status(f"{alias}: dragging point {point_index + 1}")
            return

        col, row = float(event.xdata), float(event.ydata)
        points.append((col, row))
        artist = event.inaxes.plot(
            [col],
            [row],
            marker="o",
            markersize=6,
            markeredgewidth=1.0,
            markerfacecolor="none",
            color="#ffffff",
            linestyle="None",
        )[0]
        artists.append(artist)
        try:
            npt_path, refit = _save_clicked_points(alias)
        except Exception as exc:
            _set_status(f"{alias}: clicked point refit failed: {exc}")
            canvas.draw_idle()
            return
        _set_status(
            f"{alias}: added point ({col:.1f}, {row:.1f}) on ring "
            f"{first_ring_by_alias.get(alias, 1)}; total {len(points)}"
            + ("; refit" if refit else "; need 3 points to refit")
            + (f"; saved {npt_path}" if npt_path else "")
        )
        canvas.draw_idle()

    def _on_motion(event):
        alias = drag_state.get("alias")
        index = drag_state.get("index")
        artist = drag_state.get("artist")
        if alias is None or index is None or artist is None:
            return
        if event.inaxes is not top_axes_by_alias.get(alias):
            return
        if event.xdata is None or event.ydata is None:
            return
        points = manual_points_by_alias.get(alias) or []
        if int(index) >= len(points):
            return
        col, row = float(event.xdata), float(event.ydata)
        points[int(index)] = (col, row)
        artist.set_data([col], [row])
        _set_status(f"{alias}: moving point {int(index) + 1} to ({col:.1f}, {row:.1f})")
        canvas.draw_idle()

    def _on_release(event):
        alias = drag_state.get("alias")
        index = drag_state.get("index")
        if alias is None or index is None:
            return
        drag_state["alias"] = None
        drag_state["index"] = None
        drag_state["artist"] = None
        try:
            npt_path, refit = _save_clicked_points(str(alias))
        except Exception as exc:
            _set_status(f"{alias}: clicked point refit failed: {exc}")
            canvas.draw_idle()
            return
        points = manual_points_by_alias.get(str(alias)) or []
        _set_status(
            f"{alias}: moved point {int(index) + 1}; {len(points)} clicked points"
            + ("; refit" if refit else "; need 3 points to refit")
            + (f"; saved {npt_path}" if npt_path else "")
        )
        canvas.draw_idle()

    def _on_scroll(event):
        alias = axis_to_alias.get(event.inaxes)
        if not alias or event.xdata is None or event.ydata is None:
            return
        status["last_alias"] = alias
        ax = event.inaxes
        scale = 0.8 if event.button == "up" else 1.25
        x_left, x_right = ax.get_xlim()
        y_bottom, y_top = ax.get_ylim()
        new_width = abs(x_right - x_left) * scale
        new_height = abs(y_top - y_bottom) * scale
        rel_x = (event.xdata - x_left) / (x_right - x_left)
        rel_y = (event.ydata - y_bottom) / (y_top - y_bottom)
        new_left = event.xdata - new_width * rel_x
        new_right = event.xdata + new_width * (1.0 - rel_x)
        new_bottom = event.ydata - new_height * rel_y
        new_top = event.ydata + new_height * (1.0 - rel_y)
        view = full_view_by_alias.get(alias)
        if view:
            min_x, max_x, min_y, max_y = view
            full_width = max_x - min_x
            full_height = max_y - min_y
            if new_width >= full_width:
                new_left, new_right = min_x, max_x
            else:
                if new_left < min_x:
                    new_right += min_x - new_left
                    new_left = min_x
                if new_right > max_x:
                    new_left -= new_right - max_x
                    new_right = max_x
            if new_height >= full_height:
                new_bottom, new_top = min_y, max_y
            else:
                if new_bottom < min_y:
                    new_top += min_y - new_bottom
                    new_bottom = min_y
                if new_top > max_y:
                    new_bottom -= new_top - max_y
                    new_top = max_y
        ax.set_xlim(new_left, new_right)
        ax.set_ylim(new_bottom, new_top)
        _set_status(f"{alias}: zoom {abs(new_right - new_left):.0f} x {abs(new_top - new_bottom):.0f} px")
        canvas.draw_idle()

    canvas.mpl_connect("button_press_event", _on_click)
    canvas.mpl_connect("motion_notify_event", _on_motion)
    canvas.mpl_connect("button_release_event", _on_release)
    canvas.mpl_connect("scroll_event", _on_scroll)

    dialog = QDialog(parent)
    dialog.setWindowTitle("Auto PONI Review")
    dialog.setModal(True)
    layout = QVBoxLayout(dialog)
    layout.addWidget(canvas)
    note = QLabel(dialog)
    note.setWordWrap(True)
    note.setText(
        "Validate saves generated PONI files and updates the active technical container. "
        "Correct opens pyFAI-calib2 for manual refinement. "
        "Left-click an AgBh image to add a point on the selected first ring; drag points to move them; right-click removes the last point. "
        "Use mouse wheel to zoom around cursor; double-click to reset zoom."
    )
    layout.addWidget(note)
    clicked_status = QLabel(dialog)
    clicked_status.setWordWrap(True)
    clicked_status.setText("Clicked ring points: none")
    status["label"] = clicked_status
    layout.addWidget(clicked_status)

    buttons = QDialogButtonBox(dialog)
    delete_btn = buttons.addButton("Delete last point", QDialogButtonBox.ActionRole)
    validate_btn = buttons.addButton("Validate", QDialogButtonBox.AcceptRole)
    correct_btn = buttons.addButton("Correct", QDialogButtonBox.ActionRole)
    cancel_btn = buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
    decision = {"value": "cancel"}

    def _validate():
        decision["value"] = "validate"
        dialog.accept()

    def _correct():
        decision["value"] = "correct"
        dialog.accept()

    validate_btn.clicked.connect(_validate)
    delete_btn.clicked.connect(lambda: _delete_last_point())
    correct_btn.clicked.connect(_correct)
    cancel_btn.clicked.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.resize(max(900, 560 * cols), 980)
    QTimer.singleShot(250, _draw_all_integrations)
    result = dialog.exec_()
    if result != QDialog.Accepted:
        decision["value"] = "cancel"
    return {"decision": decision["value"], "dialog": dialog}


def compute_hf_score_from_cake(
    measurement_filename: np.ndarray,
    poni_text: str = None,
    mask=None,
    hf_cutoff_fraction: float = 0.2,
    skip_bins: int = 30,
):
    """
    Compute the percentage of power in 'high' spatial frequencies
    from a 2D 'cake' integration array.
    """
    try:
        data = _load_measurement_array(measurement_filename)
    except Exception as e:
        print(f"Failed to load measurement '{measurement_filename}': {e}")
        return -1

    # Choose integrator
    if poni_text:
        ai = initialize_azimuthal_integrator_poni_text(poni_text)
    else:
        # Fallback: manual integration parameters
        max_idx = np.unravel_index(np.argmax(data), data.shape)
        center_row, center_column = max_idx
        pixel_size = 55e-6
        wavelength = 1.54
        sample_distance_mm = 100.0
        ai = initialize_azimuthal_integrator_df(
            pixel_size,
            center_column,
            center_row,
            wavelength,
            sample_distance_mm,
        )

    # Perform integration
    npt = 200
    try:
        result = ai.integrate1d(
            data, npt, unit="q_nm^-1", error_model="azimuthal", mask=mask
        )
        radial = result.radial
        intensity = result.intensity
        cake, _, _ = ai.integrate2d(data, 200, npt_azim=180, mask=mask)
    except Exception as e:
        print(f"Error integrating data: {e}")
        return None

    # 1) Skip low-q bins
    Z = cake[:, skip_bins:]
    n_az, n_q = Z.shape

    # 2) Percent deviation per bin
    Z_norm = np.full_like(Z, np.nan, dtype=float)
    for j in range(n_q):
        col = Z[:, j]
        valid = col != 0
        if np.any(valid):
            mean_val = col[valid].mean()
            if mean_val != 0:
                Z_norm[valid, j] = (col[valid] - mean_val) / mean_val * 100

    # 3) Prepare for FFT
    Z_fft = np.nan_to_num(Z_norm, nan=0.0)
    Z_fft -= Z_fft.mean()

    # 4) FFT → power spectrum → shift
    F = np.fft.fft2(Z_fft)
    P = np.abs(F) ** 2
    P_shift = np.fft.fftshift(P)

    # 5) Build normalized frequency grid
    fy = np.fft.fftshift(np.fft.fftfreq(n_az))
    fx = np.fft.fftshift(np.fft.fftfreq(n_q))
    FX, FY = np.meshgrid(fx, fy)
    FreqMag = np.sqrt(FX**2 + FY**2)

    # 6) High-freq mask + fraction
    mask_hf = FreqMag > hf_cutoff_fraction
    P_high = P_shift[mask_hf].sum()
    P_total = P_shift.sum()
    return float((P_high / P_total) * 100) if P_total > 0 else 0.0
