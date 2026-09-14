#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PROTO_DIR=src/labgrid_tui/coordinator/proto
STUBS_DIR=src/labgrid_tui/coordinator/stubs
uv run python -m grpc_tools.protoc \
  -I "$PROTO_DIR" \
  --python_out="$STUBS_DIR" \
  --pyi_out="$STUBS_DIR" \
  --grpc_python_out="$STUBS_DIR" \
  "$PROTO_DIR/labgrid-coordinator.proto"
sed -i 's/^import labgrid_coordinator_pb2/from . import labgrid_coordinator_pb2/' \
  "$STUBS_DIR/labgrid_coordinator_pb2_grpc.py"
