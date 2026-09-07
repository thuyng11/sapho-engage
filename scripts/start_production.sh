#!/bin/sh
set -eu

python -c "from app.database import init_db; init_db()"
python -m scripts.import_influencers
python -m scripts.import_posts

exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
