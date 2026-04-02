from __future__ import annotations

import pytest
from PyQt5.QtWidgets import QApplication, QPushButton, QTableWidget, QWidget

from difra.gui.main_window_ext.technical.panel_mixin import TechnicalPanelMixin


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _ToggleWidget(QWidget):
    pass


class _PanelHarness:
    def __init__(self):
        self.integrationTimeSpin = _ToggleWidget()
        self.captureFramesSpin = _ToggleWidget()
        self.moveContinuousCheck = _ToggleWidget()
        self.movementRadiusSpin = _ToggleWidget()
        self.folderLE = _ToggleWidget()
        self.auxNameLE = _ToggleWidget()
        self.auxTable = QTableWidget()
        self.framesSpin = _ToggleWidget()
        self.rtBtn = QPushButton("Real-time")
        self.load_h5_btn = QPushButton("Load H5")
        self.load_files_btn = QPushButton("Load Files")
        self.new_h5_btn = QPushButton("New H5")
        self.lock_h5_btn = QPushButton("Lock")
        self.archive_h5_btn = QPushButton("Archive")
        self.update_poni_btn = QPushButton("Update PONI")
        self.auxBtn = QPushButton("Measure AUX")
        self.pyfai_btn = QPushButton("PyFAI")
        self._detector_distances = {}
        self.logged = []

    def _log_technical_event(self, message: str):
        self.logged.append(message)

    _update_distance_dependent_controls = TechnicalPanelMixin._update_distance_dependent_controls


def test_enable_measurement_controls_keeps_aux_table_available_without_hardware(qapp):
    owner = _PanelHarness()
    owner._detector_distances = {"det_primary": 17.0}

    TechnicalPanelMixin.enable_measurement_controls(owner, False)

    assert owner.integrationTimeSpin.isEnabled() is False
    assert owner.auxTable.isEnabled() is True
    assert owner.pyfai_btn.isEnabled() is True


def test_enable_measurement_controls_still_respects_missing_distances_for_pyfai(qapp):
    owner = _PanelHarness()

    TechnicalPanelMixin.enable_measurement_controls(owner, False)

    assert owner.auxTable.isEnabled() is True
    assert owner.pyfai_btn.isEnabled() is False
