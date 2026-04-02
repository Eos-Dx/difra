from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt5.QtWidgets import QApplication

if "seaborn" not in sys.modules:
    sys.modules["seaborn"] = SimpleNamespace(
        heatmap=lambda data, robust=True, square=True, ax=None, cbar=False: ax.imshow(data)
    )

from difra.gui.technical import capture as capture_module


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_show_measurement_window_without_poni_opens_raw_only_dialog(qapp, tmp_path: Path):
    measurement_path = tmp_path / "dark.npy"
    np.save(measurement_path, np.zeros((8, 8), dtype=np.float32))

    dialog = capture_module.show_measurement_window(str(measurement_path), None, None, None)
    try:
        assert dialog is not None
        labels = [label.text() for label in dialog.findChildren(capture_module.QLabel)]
        assert any("Showing raw image only" in text for text in labels)
    finally:
        dialog.close()


def test_show_measurement_window_falls_back_to_raw_when_integration_fails(
    qapp, tmp_path: Path, monkeypatch
):
    measurement_path = tmp_path / "agbh.npy"
    np.save(measurement_path, np.ones((8, 8), dtype=np.float32))

    class _BadIntegrator:
        def integrate1d(self, *args, **kwargs):
            raise ValueError("synthetic integration failure")

    monkeypatch.setattr(
        capture_module,
        "initialize_azimuthal_integrator_poni_text",
        lambda _poni: _BadIntegrator(),
    )

    dialog = capture_module.show_measurement_window(
        str(measurement_path),
        None,
        "fake poni text",
        None,
    )
    try:
        assert dialog is not None
        labels = [label.text() for label in dialog.findChildren(capture_module.QLabel)]
        assert any("Could not integrate this measurement" in text for text in labels)
    finally:
        dialog.close()


def test_show_measurement_window_retries_without_mask_when_masked_integration_returns_nan(
    qapp, tmp_path: Path, monkeypatch
):
    measurement_path = tmp_path / "agbh_retry.npy"
    np.save(measurement_path, np.ones((16, 16), dtype=np.float32))

    class _Result:
        def __init__(self):
            self.radial = np.linspace(0.1, 1.0, 8)
            self.intensity = np.linspace(10.0, 20.0, 8)
            self.std = np.ones(8, dtype=float)
            self.sigma = np.ones(8, dtype=float)

    class _RetryIntegrator:
        def integrate1d(self, _data, *_args, mask=None, **_kwargs):
            if mask is not None:
                return SimpleNamespace(
                    radial=np.full(8, np.nan),
                    intensity=np.full(8, np.nan),
                    std=np.full(8, np.nan),
                    sigma=np.full(8, np.nan),
                )
            return _Result()

        def integrate2d(self, _data, *_args, mask=None, **_kwargs):
            if mask is not None:
                return np.full((8, 8), np.nan), None, None
            return np.ones((8, 8), dtype=float), None, None

    monkeypatch.setattr(
        capture_module,
        "initialize_azimuthal_integrator_poni_text",
        lambda _poni: _RetryIntegrator(),
    )

    dialog = capture_module.show_measurement_window(
        str(measurement_path),
        np.ones((16, 16), dtype=bool),
        "fake poni text",
        None,
    )
    try:
        assert dialog is not None
        labels = [label.text() for label in dialog.findChildren(capture_module.QLabel)]
        assert any("retried without mask" in text for text in labels)
        assert not any("Could not integrate this measurement" in text for text in labels)
    finally:
        dialog.close()
