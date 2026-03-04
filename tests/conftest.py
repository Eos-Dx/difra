import sys
import importlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


try:
    from difra._local_dependency_aliases import bootstrap_local_dependency_aliases

    bootstrap_local_dependency_aliases()
except Exception:
    pass


try:
    sys.modules.setdefault("hardware", importlib.import_module("difra.hardware"))
except Exception:
    pass
