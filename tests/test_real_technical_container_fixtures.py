from __future__ import annotations

from pathlib import Path
import os
import shutil

import h5py
import pytest

from difra.gui.main_window_ext.technical.h5_management_mixin import H5ManagementMixin


class _ValidationHarness(H5ManagementMixin):
    def __init__(self):
        self.config = {
            "detectors": [
                {"id": "MiniPIX G08-W0299", "alias": "PRIMARY"},
                {"id": "MiniPIX G05-W0339", "alias": "SECONDARY"},
            ],
            "poni_distance_validation": {
                "nominal_ranges_cm": [
                    {
                        "nominal_cm": 2.0,
                        "min_cm": 1.8,
                        "max_cm": 3.0,
                        "match_tolerance_cm": 0.35,
                    },
                    {
                        "nominal_cm": 17.0,
                        "min_cm": 16.5,
                        "max_cm": 18.0,
                        "match_tolerance_cm": 0.85,
                    },
                ]
            },
            "poni_metadata_validation": {
                "enabled": True,
                "expected_energy_keV": 8.04,
                "energy_tolerance_keV": 0.1,
                "expected_pixel_size_um": [55, 55],
                "pixel_tolerance_um": 0.25,
                "expected_shape": [256, 256],
            },
            "agbh_peak_qc": {
                "enabled": True,
                "show_dialog": False,
                "calibrant": "AgBh",
                "npt": 300,
                "peak_window_nm_inv": 0.2,
                "peak_shift_warning_nm_inv": 0.25,
                "min_checked_peaks": 4,
                "q_ranges_by_alias": {
                    "PRIMARY": [1.0, 18.0],
                    "SECONDARY": [4.5, 23.0],
                },
            },
        }


def _archive_root() -> Path:
    return Path(
        os.environ.get(
            "DIFRA_TEST_ARCHIVE_TECHNICAL",
            "/Users/sad/dev/Data/difra/archive/technical",
        )
    )


def _fixture_path(relative: str) -> Path:
    path = _archive_root() / relative
    if not path.exists():
        pytest.skip(f"real technical container fixture is not available: {path}")
    return path


@pytest.mark.parametrize(
    ("relative_path", "expected_distance_token"),
    [
        (
            "technical_fe2c66f4975b42d1_2cm_20260514_nxs_jennifer_nicell_20260514_144718/"
            "technical_fe2c66f4975b42d1_2cm_20260514.nxs.h5",
            "2cm",
        ),
        (
            "technical_d4ac6be5aa0b4e23_17cm_20260528_nxs_jennifer_nicell_20260528_104130/"
            "technical_d4ac6be5aa0b4e23_17cm_20260528.nxs.h5",
            "17cm",
        ),
    ],
)
def test_validated_real_technical_container_fixtures_pass_guards(
    tmp_path: Path,
    relative_path: str,
    expected_distance_token: str,
):
    source_path = _fixture_path(relative_path)
    copied_path = tmp_path / source_path.name
    shutil.copy2(source_path, copied_path)

    harness = _ValidationHarness()

    assert expected_distance_token in source_path.name
    assert harness._embedded_poni_distance_validation_errors(copied_path) == []
    assert harness._embedded_poni_metadata_validation_errors(copied_path) == []
    peak_warnings = harness._embedded_agbh_peak_qc_warnings(copied_path)
    assert all("peak_shift" not in warning for warning in peak_warnings)
    assert all("outside tolerance" not in warning for warning in peak_warnings)
    if expected_distance_token == "2cm":
        assert peak_warnings == []
    else:
        assert all("not_enough_agbh_peaks_checked" in warning for warning in peak_warnings)

    with h5py.File(copied_path, "r") as h5f:
        poni_names = set(h5f["/entry/technical/poni"].keys())

    assert "poni_primary" in poni_names
    assert "poni_secondary" in poni_names
    assert "poni_saxs" not in poni_names
    assert "poni_waxs" not in poni_names
