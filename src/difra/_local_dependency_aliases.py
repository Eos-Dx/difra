"""Resolve local sibling checkouts for standalone DiFRA dependencies."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Iterable


_KNOWN_LOCAL_REPOS = {
    "container": ("container", "eosdx-container"),
    "protocol": ("protocol", "eosdx-protocol"),
}


def _default_search_roots() -> tuple[Path, ...]:
    roots: list[Path] = []

    try:
        roots.append(Path(__file__).resolve().parents[3])
    except IndexError:
        pass

    try:
        roots.append(Path.cwd().resolve().parent)
    except Exception:
        pass

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        unique.append(root)
    return tuple(unique)


def _package_layouts(import_name: str, repo_dir: Path) -> tuple[Path, ...]:
    return (
        repo_dir,
        repo_dir / "src" / import_name,
    )


def _load_package(import_name: str, package_dir: Path) -> bool:
    init_file = package_dir / "__init__.py"
    if not init_file.is_file():
        return False

    spec = importlib.util.spec_from_file_location(
        import_name,
        init_file,
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        return False

    module = importlib.util.module_from_spec(spec)
    sys.modules[import_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(import_name, None)
        raise
    return True


def alias_package_from_repo(
    import_name: str,
    repo_dir_name: str,
    *,
    search_roots: Iterable[Path] | None = None,
) -> bool:
    roots = tuple(search_roots) if search_roots is not None else _default_search_roots()
    for root in roots:
        repo_dir = Path(root) / repo_dir_name
        if not repo_dir.is_dir():
            continue
        for package_dir in _package_layouts(import_name, repo_dir):
            if _load_package(import_name, package_dir):
                return True
    return False


def ensure_local_dependency(import_name: str) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except ModuleNotFoundError as exc:
        if exc.name != import_name:
            raise

    for repo_dir_name in _KNOWN_LOCAL_REPOS.get(import_name, ()):
        if alias_package_from_repo(import_name, repo_dir_name):
            return True
    return False


def bootstrap_local_dependency_aliases() -> tuple[str, ...]:
    loaded: list[str] = []
    for import_name in _KNOWN_LOCAL_REPOS:
        try:
            if ensure_local_dependency(import_name):
                loaded.append(import_name)
        except Exception:
            continue
    return tuple(loaded)
