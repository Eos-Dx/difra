from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import difra._local_dependency_aliases as local_dependency_aliases


def _clear_modules(*names: str) -> None:
    for name in names:
        sys.modules.pop(name, None)


def test_alias_package_from_root_layout_repo(tmp_path: Path) -> None:
    repo_dir = tmp_path / "eosdx-container"
    repo_dir.mkdir()
    (repo_dir / "__init__.py").write_text("from .submodule import VALUE\n", encoding="utf-8")
    (repo_dir / "submodule.py").write_text("VALUE = 42\n", encoding="utf-8")

    import_name = "test_alias_root_pkg"
    try:
        assert (
            local_dependency_aliases.alias_package_from_repo(
                import_name, repo_dir.name, search_roots=[tmp_path]
            )
            is True
        )
        module = importlib.import_module(import_name)
        submodule = importlib.import_module(f"{import_name}.submodule")
        assert module.VALUE == 42
        assert submodule.VALUE == 42
    finally:
        _clear_modules(f"{import_name}.submodule", import_name)


def test_alias_package_from_src_layout_repo(tmp_path: Path) -> None:
    package_dir = tmp_path / "protocol" / "src" / "test_alias_src_pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("from .submodule import VALUE\n", encoding="utf-8")
    (package_dir / "submodule.py").write_text("VALUE = 7\n", encoding="utf-8")

    import_name = "test_alias_src_pkg"
    try:
        assert (
            local_dependency_aliases.alias_package_from_repo(
                import_name, "protocol", search_roots=[tmp_path]
            )
            is True
        )
        module = importlib.import_module(import_name)
        submodule = importlib.import_module(f"{import_name}.submodule")
        assert module.VALUE == 7
        assert submodule.VALUE == 7
    finally:
        _clear_modules(f"{import_name}.submodule", import_name)


def test_load_package_returns_false_without_init_file(tmp_path: Path) -> None:
    assert local_dependency_aliases._load_package("missing_pkg", tmp_path) is False


def test_load_package_returns_false_without_loader(tmp_path: Path, monkeypatch) -> None:
    package_dir = tmp_path / "pkg_without_loader"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")

    monkeypatch.setattr(
        local_dependency_aliases.importlib.util,
        "spec_from_file_location",
        lambda *args, **kwargs: SimpleNamespace(loader=None),
    )

    assert local_dependency_aliases._load_package("pkg_without_loader", package_dir) is False


def test_load_package_rolls_back_module_when_import_fails(tmp_path: Path) -> None:
    package_dir = tmp_path / "pkg_broken"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    with pytest.raises(RuntimeError):
        local_dependency_aliases._load_package("pkg_broken", package_dir)

    assert "pkg_broken" not in sys.modules


def test_ensure_local_dependency_returns_false_for_unknown_module() -> None:
    _clear_modules("definitely_missing_dependency_for_difra_tests")
    assert (
        local_dependency_aliases.ensure_local_dependency("definitely_missing_dependency_for_difra_tests")
        is False
    )


def test_ensure_local_dependency_reraises_nested_missing_module(monkeypatch) -> None:
    def _import_nested_missing(name: str):
        error = ModuleNotFoundError("nested")
        error.name = "nested_dependency"
        raise error

    monkeypatch.setattr(local_dependency_aliases.importlib, "import_module", _import_nested_missing)

    with pytest.raises(ModuleNotFoundError):
        local_dependency_aliases.ensure_local_dependency("protocol")


def test_ensure_local_dependency_tries_known_repo_aliases(monkeypatch) -> None:
    calls = []

    def _import_missing(name: str):
        error = ModuleNotFoundError(name)
        error.name = name
        raise error

    def _alias(import_name: str, repo_dir_name: str) -> bool:
        calls.append((import_name, repo_dir_name))
        return repo_dir_name == "eosdx-protocol"

    monkeypatch.setattr(local_dependency_aliases.importlib, "import_module", _import_missing)
    monkeypatch.setattr(local_dependency_aliases, "alias_package_from_repo", _alias)

    assert local_dependency_aliases.ensure_local_dependency("protocol") is True
    assert calls == [
        ("protocol", "protocol"),
        ("protocol", "eosdx-protocol"),
    ]


def test_alias_package_from_repo_returns_false_when_repo_is_missing(tmp_path: Path) -> None:
    assert (
        local_dependency_aliases.alias_package_from_repo(
            "missing_pkg", "missing_repo", search_roots=[tmp_path]
        )
        is False
    )


def test_default_search_roots_skips_cwd_resolution_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        local_dependency_aliases.Path,
        "cwd",
        classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("cwd failed"))),
    )

    roots = local_dependency_aliases._default_search_roots()

    assert roots


def test_bootstrap_local_dependency_aliases_collects_successes(monkeypatch) -> None:
    def _ensure(import_name: str) -> bool:
        if import_name == "container":
            return True
        if import_name == "protocol":
            raise RuntimeError("broken checkout")
        return False

    monkeypatch.setattr(local_dependency_aliases, "ensure_local_dependency", _ensure)

    assert local_dependency_aliases.bootstrap_local_dependency_aliases() == ("container",)
