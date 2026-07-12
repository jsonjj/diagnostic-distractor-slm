#!/bin/bash
# Launch the internal Wayline demo with the real local Qwen GGUF.
# The bridge verifies every generated misconception/computation/answer binding;
# Unity falls back to its sealed deterministic batch if verification fails.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${1:-$HOME/Downloads/wayline_qwen3_4b_q4_k_m.gguf}"
SERVER="$ROOT/.wayline-build/llama.cpp/build/bin/llama-server"
BRIDGE="$ROOT/services/wayline_forge/scripts/dev_live_bridge.py"
APP="$ROOT/unity/Wayline/Builds/MacDevelopment/Wayline-Development-arm64.app/Contents/MacOS/Wayline"
LLAMA_PORT=8081
BRIDGE_PORT=8090
PYTHON="$ROOT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi

for required in "$MODEL" "$SERVER" "$BRIDGE" "$APP"; do
  if [ ! -e "$required" ]; then
    echo "Missing required live-demo artifact: $required" >&2
    exit 2
  fi
done

LLAMA_PID=""
BRIDGE_PID=""
cleanup() {
  if [ -n "$BRIDGE_PID" ]; then kill "$BRIDGE_PID" 2>/dev/null || true; fi
  if [ -n "$LLAMA_PID" ]; then kill "$LLAMA_PID" 2>/dev/null || true; fi
  if [ -n "$BRIDGE_PID" ]; then wait "$BRIDGE_PID" 2>/dev/null || true; fi
  if [ -n "$LLAMA_PID" ]; then wait "$LLAMA_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

echo "Starting pinned llama-server with:"
echo "  $MODEL"
"$SERVER" \
  -m "$MODEL" \
  --host 127.0.0.1 \
  --port "$LLAMA_PORT" \
  -c 4096 \
  -ngl 999 \
  --no-webui \
  > /tmp/wayline-llama-server.log 2>&1 &
LLAMA_PID=$!

echo -n "Loading Qwen"
READY=0
for _ in $(seq 1 180); do
  if curl -fsS "http://127.0.0.1:$LLAMA_PORT/health" >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ! kill -0 "$LLAMA_PID" 2>/dev/null; then
    echo
    echo "llama-server exited during startup. See /tmp/wayline-llama-server.log" >&2
    exit 3
  fi
  echo -n "."
  sleep 1
done
echo
if [ "$READY" != "1" ]; then
  echo "llama-server did not become ready. See /tmp/wayline-llama-server.log" >&2
  exit 3
fi

PYTHONPATH="$ROOT" "$PYTHON" -u "$BRIDGE" \
  --llama-url "http://127.0.0.1:$LLAMA_PORT" \
  --port "$BRIDGE_PORT" \
  > /tmp/wayline-live-bridge.log 2>&1 &
BRIDGE_PID=$!

for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$BRIDGE_PORT/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
if ! curl -fsS "http://127.0.0.1:$BRIDGE_PORT/health" >/dev/null; then
  echo "Verifier bridge did not start. See /tmp/wayline-live-bridge.log" >&2
  exit 4
fi

echo "LIVE LOCAL QWEN ready. Launching Wayline."
echo "Close the game normally to stop both local services."
WAYLINE_LIVE_BRIDGE="http://127.0.0.1:$BRIDGE_PORT" \
  "$APP" -logFile /tmp/wayline-live-player.log
