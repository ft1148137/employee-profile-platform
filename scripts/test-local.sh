#! /usr/bin/env bash

# Exit in case of error
set -e

docker compose -f compose.yml -f compose.dev.yml down -v --remove-orphans # Remove possibly previous broken stacks left hanging after an error

if [ "$(uname -s)" = "Linux" ]; then
    echo "Remove __pycache__ files"
    sudo find . -type d -name __pycache__ -exec rm -r {} \+
fi

docker compose -f compose.yml -f compose.dev.yml build
docker compose -f compose.yml -f compose.dev.yml up -d
docker compose -f compose.yml -f compose.dev.yml exec -T backend bash scripts/tests-start.sh "$@"
