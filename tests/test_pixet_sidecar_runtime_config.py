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
    assert "pxcore.dll" in bootstrap
    assert "ensure_pixet_sidecar_runtime.bat" in launcher
    assert "ensure_pixet_sidecar_runtime.bat" in sidecar_launcher
    assert "eosdx_pixet" in launcher
    assert "3.12 64bit" in sidecar_launcher
