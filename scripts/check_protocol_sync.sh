#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for f in \
  "$ROOT_DIR/src/hardware/difra/grpc_server/generated/hub/v1/hub_pb2.py" \
  "$ROOT_DIR/src/hardware/difra/grpc_server/generated/hub/v1/hub_pb2_grpc.py"
do
  if [[ ! -f "$f" ]]; then
    echo "Missing generated protocol stub: $f" >&2
    exit 2
  fi
done

echo "Protocol sync check passed"
