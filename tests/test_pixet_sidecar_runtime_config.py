from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_eosdx_pixet_env_targets_python_312():
    env_path = REPO_ROOT / "src" / "difra" / "environment-eosdx-pixet.yml"
    payload = yaml.safe_load(env_path.read_text(encoding="utf-8"))

    assert payload["name"] == "eosdx_pixet"
    assert "python=3.12" in payload["dependencies"]
    assert "numpy" in payload["dependencies"]


def test_windows_pixet_bootstrap_downloads_advacam_sdk():
    bootstrap = (
        REPO_ROOT / "src" / "difra" / "bin" / "ensure_pixet_sidecar_runtime.bat"
    ).read_text(encoding="utf-8")
    launcher = (REPO_ROOT / "src" / "difra" / "bin" / "run_difra.bat").read_text(
        encoding="utf-8"
    )
    sidecar_launcher = (
        REPO_ROOT / "src" / "difra" / "bin" / "run_pixet_sidecar.bat"
    ).read_text(encoding="utf-8")

    assert "eosdx_pixet" in bootstrap
    assert "python 3.12 x64" in bootstrap.lower()
    assert "PIXet_Pro_GUI_1.8.5_Windows_x64.zip" in bootstrap
    assert "https://advacam.com/content/uploads/2026/03/PIXet_Pro_GUI_1.8.5_Windows_x64.zip" in bootstrap
    assert r"D:\API_PIXet_Pro_1.8.5_Windows_x64" in bootstrap
    assert r"%LOCALAPPDATA%\DiFRA\pixet\API_PIXet_Pro_1.8.5_Windows_x64" in bootstrap
    assert r"set PIXET_EXTRACT_ROOT=%PIXET_CACHE_ROOT%\sdk" in bootstrap
    assert "pxcore.dll" in bootstrap
    assert "conda_packages=python=3.12 pip numpy" in bootstrap.lower()
    assert "install -y -n %sidecar_env% %pixet_conda_packages%" in bootstrap.lower()
    assert ":resolve_env_python" in bootstrap
    assert "Checking PIXet sidecar Python" in bootstrap
    assert "PIXet sidecar Python OK" in bootstrap
    assert "conda run --no-capture-output -n %SIDECAR_ENV% python" not in bootstrap
    assert "import sys,platform,numpy" in bootstrap
    assert "ensure_pixet_sidecar_runtime.bat" in launcher
    assert "ensure_pixet_sidecar_runtime.bat" in sidecar_launcher
    assert launcher.index("call :auto_update_repo") < launcher.index("set GUI_ENV=")
    assert launcher.index("call :auto_update_repo") < launcher.index(
        "ensure_pixet_sidecar_runtime.bat"
    )
    assert "filter_startup_stderr.py" in launcher
    assert "pixet_sidecar_server.py\" --host" in launcher
    assert "\"%startup_stderr_filter%\" -- \"%sidecar_py_exe%\" -u" in launcher.lower()
    assert "difra_sidecar_log_path" in launcher.lower()
    assert "difra_sidecar_log_path" in sidecar_launcher.lower()
    assert "pixet_sidecar.log" in launcher.lower()
    assert "eosdx_pixet" in launcher
    assert "sidecar_conda" in launcher
    assert "sidecar_conda" in bootstrap
    assert "sidecar_conda" in sidecar_launcher
    assert "3.12 64bit" in sidecar_launcher


def test_shipped_configs_point_pixet_detectors_to_managed_sdk_env():
    config_dir = REPO_ROOT / "src" / "difra" / "resources" / "config"
    offenders = []
    for path in config_dir.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        if "API_PIXet_Pro_1.8.3" in text or "1.8.3_Windows_x86_64" in text:
            offenders.append(path.relative_to(REPO_ROOT).as_posix())

    assert offenders == []

    for path in (config_dir / "main.json", config_dir / "main_win.json"):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert payload["sidecar_conda"] == "eosdx_pixet"
        pixet_paths = [
            detector.get("pixet_sdk_path")
            for detector in payload.get("detectors", [])
            if detector.get("type") == "Pixet"
        ]
        assert pixet_paths
        assert set(pixet_paths) == {"%PIXET_SDK_PATH%"}
