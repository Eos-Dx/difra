import importlib.util
import os
import sys
from pathlib import Path


SRC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

_MIXIN_PATH = (
    Path(__file__).resolve().parents[1]
    / "gui"
    / "main_window_ext"
    / "zone_measurements"
    / "logic"
    / "process_results_mixin.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "process_results_mixin_for_tests",
    _MIXIN_PATH,
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
ZoneMeasurementsProcessResultsMixin = _MODULE.ZoneMeasurementsProcessResultsMixin


class _Harness(ZoneMeasurementsProcessResultsMixin):
    pass


class _Schema:
    DATASET_PROCESSED_SIGNAL = "processed_signal"

    @staticmethod
    def format_detector_role(alias: str) -> str:
        alias_map = {
            "PRIMARY": "det_primary",
            "SAXS": "det_saxs",
        }
        return alias_map.get(str(alias), f"det_{str(alias).lower()}")


class _SessionManager:
    def __init__(self):
        self.session_path = Path("/tmp/test_session.nxs.h5")
        self.schema = _Schema()


def test_build_session_measurement_result_refs_uses_container_dataset_paths():
    harness = _Harness()

    refs = harness._build_session_measurement_result_refs(
        session_manager=_SessionManager(),
        measurement_path="/entry/measurements/pt_004/meas_000000007",
        result_files={
            "PRIMARY": "/tmp/point4_primary.npy",
            "SAXS": "/tmp/point4_saxs.npy",
        },
        detector_lookup={
            "PRIMARY": {"id": "det_primary"},
            "SAXS": {"id": "det_saxs"},
        },
    )

    assert refs == {
        "PRIMARY": (
            "h5ref:///tmp/test_session.nxs.h5"
            "#/entry/measurements/pt_004/meas_000000007/det_primary/processed_signal"
        ),
        "SAXS": (
            "h5ref:///tmp/test_session.nxs.h5"
            "#/entry/measurements/pt_004/meas_000000007/det_saxs/processed_signal"
        ),
    }


def test_build_session_measurement_result_refs_falls_back_without_session_path():
    harness = _Harness()

    refs = harness._build_session_measurement_result_refs(
        session_manager=None,
        measurement_path=None,
        result_files={"PRIMARY": "/tmp/point4_primary.npy"},
        detector_lookup={"PRIMARY": {"id": "det_primary"}},
    )

    assert refs == {"PRIMARY": "/tmp/point4_primary.npy"}
