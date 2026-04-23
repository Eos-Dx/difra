from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(name, None)
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(name, None)


def test_ensure_runtime_dependencies_script_exports_runtime_main():
    module = _load_module(
        REPO_ROOT / "src" / "difra" / "scripts" / "ensure_runtime_dependencies.py",
        "test_ensure_runtime_dependencies_script",
    )
    import difra.runtime_deps as runtime_deps

    assert module.REPO_ROOT == REPO_ROOT
    assert module.SRC_ROOT == REPO_ROOT / "src"
    assert module.main is runtime_deps.main


def test_validate_container_parser_supports_quiet_and_json_flags():
    module = _load_module(
        REPO_ROOT / "src" / "difra" / "scripts" / "validate_container.py",
        "test_validate_container_script_parser",
    )

    parser = module._build_parser()
    args = parser.parse_args(["/tmp/demo.h5", "--kind", "technical", "--json", "--quiet"])

    assert args.file == "/tmp/demo.h5"
    assert args.kind == "technical"
    assert args.json is True
    assert args.quiet is True


def test_validate_container_main_emits_json_and_errors(monkeypatch, capsys):
    module = _load_module(
        REPO_ROOT / "src" / "difra" / "scripts" / "validate_container.py",
        "test_validate_container_script_main",
    )

    report = SimpleNamespace(
        file_path="/tmp/demo.h5",
        schema_version="0.2",
        container_kind="technical",
        is_valid=True,
        messages=[SimpleNamespace(severity="warning", path="/entry", message="ok")],
    )
    monkeypatch.setattr(
        module,
        "_build_parser",
        lambda: SimpleNamespace(
            parse_args=lambda: SimpleNamespace(file="/tmp/demo.h5", kind="technical", json=True, quiet=False)
        ),
    )
    monkeypatch.setattr(module, "validate_container", lambda file_path, container_kind: report)

    assert module.main() == 0
    out = capsys.readouterr().out
    assert '"schema_version": "0.2"' in out
    assert '"container_kind": "technical"' in out

    monkeypatch.setattr(
        module,
        "_build_parser",
        lambda: SimpleNamespace(
            parse_args=lambda: SimpleNamespace(file="/tmp/demo.h5", kind="auto", json=False, quiet=False)
        ),
    )
    monkeypatch.setattr(module, "validate_container", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bad")))

    assert module.main() == 2
    assert "Error: bad" in capsys.readouterr().err


def test_validate_technical_h5_main_handles_missing_and_quiet_success(
    monkeypatch, capsys, tmp_path
):
    module = _load_module(
        REPO_ROOT / "src" / "difra" / "scripts" / "validate_technical_h5.py",
        "test_validate_technical_h5_script_main",
    )

    missing_file = tmp_path / "missing.h5"
    monkeypatch.setattr(
        module.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(file=str(missing_file), strict=False, quiet=False),
    )
    assert module.main() == 2
    assert "File not found" in capsys.readouterr().err

    real_file = tmp_path / "technical.h5"
    real_file.write_bytes(b"stub")
    monkeypatch.setattr(
        module.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(file=str(real_file), strict=True, quiet=True),
    )
    monkeypatch.setattr(
        module,
        "validate_technical_container",
        lambda file_path, strict: (True, [], ["warning"]),
    )

    assert module.main() == 0
    assert "VALID:" in capsys.readouterr().out


def test_sync_archive_to_onedrive_parser_supports_overrides_and_dry_run():
    module = _load_module(
        REPO_ROOT / "src" / "difra" / "scripts" / "sync_archive_to_onedrive.py",
        "test_sync_archive_to_onedrive_script_parser",
    )

    parser = module._build_parser()
    args = parser.parse_args(
        [
            "--config",
            "/tmp/main_win.json",
            "--source-root",
            "/tmp/Archive",
            "--mirror-root",
            "/tmp/OneDrive",
            "--dry-run",
        ]
    )

    assert args.config == "/tmp/main_win.json"
    assert args.source_root == "/tmp/Archive"
    assert args.mirror_root == "/tmp/OneDrive"
    assert args.dry_run is True


def test_hardware_compat_module_lazy_loads_submodules():
    path = REPO_ROOT / "src" / "hardware" / "__init__.py"
    spec = importlib.util.spec_from_file_location("hardware", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.pop("hardware", None)
    sys.modules["hardware"] = module
    try:
        spec.loader.exec_module(module)
        import difra.hardware as hardware_impl

        sys.modules.pop("difra.hardware.auxiliary", None)
        if hasattr(hardware_impl, "auxiliary"):
            delattr(hardware_impl, "auxiliary")

        submodule = module.__getattr__("auxiliary")
        assert submodule.__name__ == "difra.hardware.auxiliary"
        assert sys.modules["hardware.auxiliary"] is submodule
    finally:
        if previous is None:
            sys.modules.pop("hardware", None)
        else:
            sys.modules["hardware"] = previous
        sys.modules.pop("difra.hardware.auxiliary", None)
        sys.modules.pop("hardware.auxiliary", None)
