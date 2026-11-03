#!/usr/bin/env bash

set -e
set -x

FASTAPI_ENV=development pytest tests/ -n auto --dist loadgroup --cov --durations=25
coverage report
coverage html --title "${@-coverage}"
