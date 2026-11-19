#! /usr/bin/env bash

set -e
set -x

formatted=$(mktemp -d)
trap 'rm -rf "$formatted"' EXIT
(cd backend && FASTAPI_ENV=development uv run python -c "import app.main; import json; print(json.dumps(app.main.app.openapi()))") > "$formatted/openapi.json"
mv "$formatted/openapi.json" frontend/
bun run --filter frontend generate-client
# Stage the files outside src/client so Biome can format them with the frontend config.
cp frontend/src/client/*.ts "$formatted/"
(cd frontend && bunx biome format --write openapi.json "$formatted"/*.ts)
mv "$formatted"/*.ts frontend/src/client/
