#! /usr/bin/env sh

# Exit in case of error
set -e
set -x

docker compose -f compose.yml -f compose.dev.yml build
docker compose -f compose.yml -f compose.dev.yml down -v --remove-orphans # Remove possibly previous broken stacks left hanging after an error
docker compose -f compose.yml -f compose.dev.yml up -d
docker compose -f compose.yml -f compose.dev.yml exec -T backend bash scripts/tests-start.sh "$@"
docker compose -f compose.yml -f compose.dev.yml down -v --remove-orphans
