from difra.gui.main_window_ext.technical.capture_mixin import TechnicalCaptureMixin


class _Harness(TechnicalCaptureMixin):
    def __init__(self, config):
        self.config = config


def test_resolve_pyfai_env_prefers_explicit_pyfai_conda():
    harness = _Harness({"pyfai_conda": "eosdx13", "conda": "ulster38"})
    assert harness._resolve_pyfai_conda_env() == "eosdx13"


def test_resolve_pyfai_env_uses_global_config_when_explicit_missing(monkeypatch):
    harness = _Harness({"conda": "eosdx13"})
    monkeypatch.setattr(harness, "_read_pyfai_conda_from_global_config", lambda: "eosdx13")
    assert harness._resolve_pyfai_conda_env() == "eosdx13"


def test_resolve_pyfai_env_uses_conda_for_eosdx_when_global_missing(monkeypatch):
    harness = _Harness({"conda": "eosdx13"})
    monkeypatch.setattr(harness, "_read_pyfai_conda_from_global_config", lambda: "")
    monkeypatch.setattr(harness, "_list_conda_env_names", lambda: ["base", "ulster38", "eosdx13"])
    assert harness._resolve_pyfai_conda_env() == "eosdx13"


def test_resolve_pyfai_env_does_not_rewrite_eosdx_typo(monkeypatch):
    harness = _Harness({"conda": "IOSDX13"})
    monkeypatch.setattr(harness, "_read_pyfai_conda_from_global_config", lambda: "")
    monkeypatch.setattr(harness, "_list_conda_env_names", lambda: ["base", "eosdx13"])
    assert harness._resolve_pyfai_conda_env() == "IOSDX13"


def test_resolve_pyfai_env_uses_conda_for_non_eosdx(monkeypatch):
    harness = _Harness({"conda": "research-env"})
    monkeypatch.setattr(harness, "_read_pyfai_conda_from_global_config", lambda: "")
    assert harness._resolve_pyfai_conda_env() == "research-env"


def test_build_windows_pyfai_script_uses_conda_run_and_no_capture_output():
    script = _Harness({})._build_windows_pyfai_script(
        folder=r"D:\Data\measurements",
        env="eosdx13",
    )

    assert "conda activate" not in script
    assert "conda run --no-capture-output -n eosdx13 pyfai-calib2" in script
    assert "Set-Location 'D:\\Data\\measurements'" in script
