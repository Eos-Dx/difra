from difra.gui.main_window_ext.technical.capture_mixin import TechnicalCaptureMixin


class _Harness(TechnicalCaptureMixin):
    def __init__(self, config):
        self.config = config


def test_resolve_pyfai_env_prefers_explicit_pyfai_conda():
    harness = _Harness({"pyfai_conda": "ulster38", "conda": "eosdx13"})
    assert harness._resolve_pyfai_conda_env() == "ulster38"


def test_resolve_pyfai_env_uses_global_config_when_explicit_missing(monkeypatch):
    harness = _Harness({"conda": "eosdx13"})
    monkeypatch.setattr(harness, "_read_pyfai_conda_from_global_config", lambda: "ulster38")
    assert harness._resolve_pyfai_conda_env() == "ulster38"


def test_resolve_pyfai_env_prefers_ulster_for_eosdx_when_available(monkeypatch):
    harness = _Harness({"conda": "eosdx13"})
    monkeypatch.setattr(harness, "_read_pyfai_conda_from_global_config", lambda: "")
    monkeypatch.setattr(harness, "_list_conda_env_names", lambda: ["base", "ulster38", "eosdx13"])
    assert harness._resolve_pyfai_conda_env() == "ulster38"


def test_resolve_pyfai_env_defaults_to_ulster38_for_eosdx_when_unknown(monkeypatch):
    harness = _Harness({"conda": "IOSDX13"})
    monkeypatch.setattr(harness, "_read_pyfai_conda_from_global_config", lambda: "")
    monkeypatch.setattr(harness, "_list_conda_env_names", lambda: ["base", "eosdx13"])
    assert harness._resolve_pyfai_conda_env() == "ulster38"


def test_resolve_pyfai_env_uses_conda_for_non_eosdx(monkeypatch):
    harness = _Harness({"conda": "research-env"})
    monkeypatch.setattr(harness, "_read_pyfai_conda_from_global_config", lambda: "")
    assert harness._resolve_pyfai_conda_env() == "research-env"
