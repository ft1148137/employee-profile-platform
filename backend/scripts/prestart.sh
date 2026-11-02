#! /usr/bin/env bash

set -e
set -x

# Run migrations
alembic upgrade head

# Create initial data in DB
python app/initial_data.py

if python -c 'import sys; from app.core.config import settings; sys.exit(not settings.SEED_DEMO_DATA)'; then
    python -m app.seed load --allow-non-dev
fi
