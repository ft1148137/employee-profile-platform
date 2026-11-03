#!/usr/bin/env bash

set -e
set -x

FASTAPI_ENV=development coverage run -m pytest tests/ --durations=25
coverage report
coverage html --title "${@-coverage}"
