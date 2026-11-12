#! /usr/bin/env bash

set -e
set -x

cd backend
FASTAPI_ENV=development uv run python -c "import app.main; import json; print(json.dumps(app.main.app.openapi()))" > ../openapi.json
cd ..
mv openapi.json frontend/
bun run --filter frontend generate-client
# Format via stdin because the frontend config excludes generated files from lint.
for file in frontend/src/client/*.ts; do
  (cd frontend && bunx biome format --stdin-file-path=generated.ts) < "$file" > "$file.formatted"
  mv "$file.formatted" "$file"
done
bun run lint
