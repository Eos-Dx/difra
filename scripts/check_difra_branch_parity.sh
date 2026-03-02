#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -d "$ROOT_DIR/src/hardware/difra" ]]; then
  echo "Missing DiFRA package directory" >&2
  exit 2
fi

echo "DiFRA standalone repo layout check passed"
