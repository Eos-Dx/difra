"""Measurement plotting and quality-score helpers."""

import logging
import os
import sys

import numpy as np
import seaborn as sns
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from difra.gui.qt_compat import QDialog, QLabel, QPlainTextEdit, QVBoxLayout
from difra.gui.technical.analysis_compat import (
    initialize_azimuthal_integrator_df,
    initialize_azimuthal_integrator_poni_text,
)
from difra.gui.technical.capture_io import (
    _inspect_embedded_poni,
    _load_measurement_array,
)

logger = logging.getLogger(__name__)


def _capture_dependency(name: str, fallback):
    facade = sys.modules.get("difra.gui.technical.capture")
    if facade is not None and hasattr(facade, name):
        return getattr(facade, name)
    return fallback


def _format_measurement_diagnostics(
    *,
    measurement_filename: str,
    poni_info: dict,
    integration_error: str = "",
) -> str:
    info = poni_info if isinstance(poni_info, dict) else {}
    provided_poni_text = str(info.get("provided_poni_text") or "").strip()
    embedded_poni_text = str(info.get("resolved_poni_text") or "").strip()

    lines = [
        f"Measurement source: {str(measurement_filename or '').strip() or '<empty>'}"
    ]

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
            lines.append(
                "PONI payload used for integration: embedded container PONI (matches caller text)"
            )
        else:
            lines.append(
                "PONI payload used for integration: embedded container PONI (caller text differs)"
            )
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

    # Load data
    data = _load_measurement_array(measurement_filename)
    poni_info = _inspect_embedded_poni(measurement_filename, poni_text)
    active_poni_text = str(
        poni_info.get("resolved_poni_text") or poni_text or ""
    ).strip()

    radial = intensity = std = sigma = cake = None
    integration_error = ""
    integration_note = ""

    def _run_integration(active_mask):
        ai = _capture_dependency(
            "initialize_azimuthal_integrator_poni_text",
            initialize_azimuthal_integrator_poni_text,
        )(active_poni_text)
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
        ai = _capture_dependency(
            "initialize_azimuthal_integrator_poni_text",
            initialize_azimuthal_integrator_poni_text,
        )(poni_text)
    else:
        # Fallback: manual integration parameters
        max_idx = np.unravel_index(np.argmax(data), data.shape)
        center_row, center_column = max_idx
        pixel_size = 55e-6
        wavelength = 1.54
        sample_distance_mm = 100.0
        ai = _capture_dependency(
            "initialize_azimuthal_integrator_df", initialize_azimuthal_integrator_df
        )(
            pixel_size,
            center_column,
            center_row,
            wavelength,
            sample_distance_mm,
        )

    # Perform integration
    npt = 200
    try:
        ai.integrate1d(data, npt, unit="q_nm^-1", error_model="azimuthal", mask=mask)
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
