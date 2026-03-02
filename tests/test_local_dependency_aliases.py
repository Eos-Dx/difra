from __future__ import annotations

import importlib
import sys
from pathlib import Path

from difra._local_dependency_aliases import alias_package_from_repo


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
        assert alias_package_from_repo(import_name, repo_dir.name, search_roots=[tmp_path]) is True
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
        assert alias_package_from_repo(import_name, "protocol", search_roots=[tmp_path]) is True
        module = importlib.import_module(import_name)
        submodule = importlib.import_module(f"{import_name}.submodule")
        assert module.VALUE == 7
        assert submodule.VALUE == 7
    finally:
        _clear_modules(f"{import_name}.submodule", import_name)
