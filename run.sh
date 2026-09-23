#!/usr/bin/env bash
# Start the Auto Rate Explorer: makes sure Ollama is up, then serves the app.
set -euo pipefail
cd "$(dirname "$0")"

# Find ollama: explicit override, then PATH (standard installs), then a user-local install.
OLLAMA_BIN="${OLLAMA_BIN:-$(command -v ollama 2>/dev/null || echo "$HOME/.local/ollama/bin/ollama")}"
MODEL="${OLLAMA_MODEL:-llama3.2:3b}"
PORT="${PORT:-5000}"

echo "== Auto Rate Explorer =="

# --- Ollama -----------------------------------------------------------------
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "[ok]   ollama already running"
elif [ -x "$OLLAMA_BIN" ]; then
  echo "[..]   starting ollama"
  nohup "$OLLAMA_BIN" serve >/tmp/ollama-serve.log 2>&1 &
  for _ in $(seq 1 20); do
    curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
  curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 \
    && echo "[ok]   ollama up" \
    || echo "[warn] ollama did not start - app will use deterministic fallbacks"
else
  echo "[warn] ollama not found at $OLLAMA_BIN - app will use deterministic fallbacks"
fi

if curl -sf http://localhost:11434/api/tags 2>/dev/null | grep -q "$MODEL"; then
  echo "[ok]   model $MODEL present"
elif [ -x "$OLLAMA_BIN" ]; then
  echo "[..]   pulling $MODEL (one time, ~2GB)"
  "$OLLAMA_BIN" pull "$MODEL"
fi

# --- data -------------------------------------------------------------------
if [ ! -f data/counties.json ] || [ ! -f data/places.json ]; then
  echo "[..]   building reference data from Census sources"
  python3 scripts/build_county_data.py
  python3 scripts/build_geo_index.py
fi
echo "[ok]   data ready"

# --- app --------------------------------------------------------------------
PY=./venv/bin/python
[ -x "$PY" ] || PY=python3
echo "[ok]   serving on http://localhost:$PORT"
echo
OLLAMA_MODEL="$MODEL" PORT="$PORT" exec "$PY" backend/app.py
