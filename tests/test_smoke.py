import difra as difra
from pathlib import Path


def test_package_imports():
    assert hasattr(difra, "__getattr__")


def test_standalone_layout_files_exist():
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "src" / "difra").is_dir()
    assert (repo_root / "scripts" / "check_protocol_sync.sh").is_file()
    assert (repo_root / "scripts" / "check_difra_branch_parity.sh").is_file()
