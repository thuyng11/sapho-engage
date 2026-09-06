# Sapho LinkedIn Assistant

Milestone 1 of a take-home assessment for Sapho Bio: a local, read-only HTML queue using FastAPI, Jinja2, SQLite, and SQLAlchemy 2.x.

**All 3 sample influencers and 6 sample posts are fictional.** Names, companies, content, dates, relevance scores, and engagement counts are invented. LinkedIn URLs are clearly named sample placeholders and do not identify real profiles or posts.

This milestone has no AI/LLM functionality, scraping, authentication, deployment configuration, or response generation.

## Local setup

Use Python 3.10 or newer. From the workspace directory containing `sapho-engage`, run these macOS/Linux commands:

```bash
cd sapho-engage
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m scripts.seed_data
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/ to see all six posts, newest first.

The seed command creates `sapho.db` and both tables before importing JSON. Running it again skips existing primary keys, preserving existing content and statuses. It does not synchronize edits to existing JSON records. Both JSON files are imported in one transaction, so an invalid record rolls back that run's inserts.

To create the tables without importing data:

```bash
python -c "from app.database import init_db; init_db()"
```

The application also creates missing tables on startup and shows an empty state if there are no posts. Table creation does not migrate an existing schema.

## Project files

```text
sapho-engage/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── database.py
│   └── models.py
├── templates/
│   ├── base.html
│   └── index.html
├── static/
│   └── styles.css
├── data/
│   ├── influencers.json
│   └── posts.json
├── scripts/
│   └── seed_data.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Data flow

1. `scripts/seed_data.py` reads both JSON arrays, converts ISO 8601 dates to UTC, and inserts influencers before their posts through SQLAlchemy.
2. SQLite stores `influencers` and `posts`, linked by `posts.influencer_id`. Foreign key enforcement is enabled for each connection. `Post.status` defaults to `new` when omitted.
3. `GET /` opens a request-scoped session and selects all posts with their influencers in one query, ordered by date descending, then ID descending.
4. FastAPI passes the results to `templates/index.html`, which extends `base.html`. Jinja escapes text, preserves post line breaks, and renders the author details, content, date, likes, comments, status, and full post URL. FastAPI serves CSS at `/static/styles.css`.

The implementation uses [SQLAlchemy 2.x typed models and selects](https://docs.sqlalchemy.org/en/20/orm/quickstart.html) and [FastAPI's Jinja2 template integration](https://fastapi.tiangolo.com/advanced/templates/).

## Configuration and sample format

`.env` is optional. `DATABASE_PATH` defaults to `sapho.db` in this project directory. Relative paths are resolved from this directory; absolute paths also work. The database file's parent directory must exist. An exported environment variable takes precedence over `.env`. No credentials are needed.

JSON fields match the models. Each post's `influencer_id` must reference an existing influencer or one supplied in the influencers file. IDs must be unique within each file. Use ISO 8601 timestamps with an offset or `Z`; timestamps without an offset are treated as UTC. SQLite stores dates without time zone information after normalization, and the page labels them UTC. Status is a free-text field; the fixtures use `new` and `reviewed` to demonstrate display only.

## Smoke checks

With the virtual environment active and the server running, use another terminal in `sapho-engage`:

```bash
source .venv/bin/activate
python -m scripts.seed_data
python -c "from sqlalchemy import select, func; from app.database import SessionLocal; from app.models import Influencer, Post; s = SessionLocal(); print('Influencers:', s.scalar(select(func.count()).select_from(Influencer))); print('Posts:', s.scalar(select(func.count()).select_from(Post))); s.close()"
curl -f http://127.0.0.1:8000/
curl -f http://127.0.0.1:8000/static/styles.css
```

On an unchanged sample database, reseeding reports zero additions; counts remain 3 influencers and 6 posts. Both HTTP requests should return successfully.

Manual checks:

- Confirm six cards, newest first, with all requested fields and visible SAMPLE labels.
- Confirm multiline content and long URLs remain readable in a narrow browser window, and zero likes/comments display correctly.
- Placeholder LinkedIn URLs may show a missing page or LinkedIn login; they are not real data.
- Check the empty state using a separate database: stop the server, then run `DATABASE_PATH=empty-check.db uvicorn app.main:app --reload`. Stop it and run the usual command to return to the sample database.
- If port 8000 is busy, append `--port 8001` and open that port instead.
