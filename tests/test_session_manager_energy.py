from __future__ import annotations

import pytest

from difra.gui.session_manager import DEFAULT_BEAM_ENERGY_KEV, SessionManager


def test_session_manager_reads_xray_energy_kev_from_config(tmp_path):
    manager = SessionManager(
        config={
            "technical_folder": str(tmp_path),
            "xray_energy_kev": 8.04,
        }
    )

    assert manager.beam_energy_kev == pytest.approx(8.04)


def test_session_manager_default_beam_energy_is_not_legacy_17_5(tmp_path):
    manager = SessionManager(config={"technical_folder": str(tmp_path)})

    assert manager.beam_energy_kev == pytest.approx(DEFAULT_BEAM_ENERGY_KEV)
    assert manager.beam_energy_kev != pytest.approx(17.5)


def test_session_manager_rejects_invalid_beam_energy(tmp_path):
    with pytest.raises(ValueError, match="Invalid xray_energy_kev"):
        SessionManager(
            config={
                "technical_folder": str(tmp_path),
                "xray_energy_kev": "bad",
            }
        )
