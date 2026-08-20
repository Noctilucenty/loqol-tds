#!/usr/bin/env bash
# Render build: install Python deps, then build the SPA into app/static so the
# whole product ships as one service with no CORS and no second origin.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/node.sh 2>/dev/null || true
if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found on the build image" >&2
  exit 1
fi

cd web
npm ci --no-audit --no-fund
npm run build
