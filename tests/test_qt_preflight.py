import subprocess

from difra.gui.qt_preflight import ensure_qt_api_for_gui


def test_preflight_keeps_auto_when_pyqt6_probe_passes():
    env = {}
    calls = []

    def runner(*args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="PyQt6\n", stderr="")

    result = ensure_qt_api_for_gui(
        src_root="/repo/src",
        env=env,
        runner=runner,
        timeout_s=1,
    )

    assert result.selected == "pyqt6"
    assert result.fallback is False
    assert "DIFRA_QT_API" not in env
    assert calls[0]["env"]["DIFRA_QT_API"] == "pyqt6"
    assert calls[0]["env"]["PYTHONPATH"].startswith("/repo/src")


def test_preflight_falls_back_to_pyqt5_when_pyqt6_probe_fails():
    env = {"DIFRA_QT_API": "auto"}

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 134, stdout="", stderr="qt crash")

    result = ensure_qt_api_for_gui(env=env, runner=runner, timeout_s=1)

    assert result.selected == "pyqt5"
    assert result.fallback is True
    assert env["DIFRA_QT_API"] == "pyqt5"
    assert "qt crash" in env["DIFRA_QT_PREFLIGHT_FALLBACK_REASON"]


def test_preflight_honors_explicit_qt_api():
    env = {"DIFRA_QT_API": "pyqt6"}

    def runner(*args, **kwargs):
        raise AssertionError("runner should not be called")

    result = ensure_qt_api_for_gui(env=env, runner=runner)

    assert result.selected == "pyqt6"
    assert result.fallback is False
    assert env["DIFRA_QT_API"] == "pyqt6"


def test_preflight_can_be_skipped():
    env = {"DIFRA_SKIP_QT_PREFLIGHT": "1"}

    def runner(*args, **kwargs):
        raise AssertionError("runner should not be called")

    result = ensure_qt_api_for_gui(env=env, runner=runner)

    assert result.selected == "auto"
    assert result.fallback is False
    assert result.reason == "skipped"
