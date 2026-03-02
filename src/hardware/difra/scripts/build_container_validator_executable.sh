#!/usr/bin/env bash
set -euo pipefail

if ! python3 -m PyInstaller --version >/dev/null 2>&1; then
  echo "PyInstaller is not installed. Install it first with:"
  echo "  python3 -m pip install pyinstaller"
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

cd "${REPO_ROOT}"

python3 -m PyInstaller \
  --clean \
  --noconfirm \
  --onefile \
  --name difra-container-validate-macos \
  --paths src \
  --collect-all h5py \
  src/hardware/difra/scripts/validate_container.py

echo "Built executable: ${REPO_ROOT}/dist/difra-container-validate-macos"
