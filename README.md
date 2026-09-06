# Sapho LinkedIn Assistant

Milestones 1 and 2 of a take-home assessment for Sapho Bio: a local HTML post queue and editable demo-response drafts using FastAPI, Jinja2, SQLite, and SQLAlchemy 2.x.

**All 3 sample influencers and 6 sample posts are fictional.** Names, companies, content, dates, relevance scores, and engagement counts are invented. LinkedIn URLs are clearly named sample placeholders and do not identify real profiles or posts.

Responses are deterministic, clearly labeled placeholders. There is no AI/LLM integration, external API call, scraping, authentication, deployment configuration, or LinkedIn posting.

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

The seed command creates `sapho.db` and missing tables before importing JSON. Running it again skips existing primary keys, preserving existing content, statuses, and response drafts. It does not synchronize edits to existing JSON records. Both JSON files are imported in one transaction, so an invalid record rolls back that run's inserts.

To create the tables without importing data:

```bash
python -c "from app.database import init_db; init_db()"
```

The application also creates missing tables on startup and shows an empty state if there are no posts. For an existing Milestone 1 database, this adds only the new `generated_responses` table; keep your database file. No reset or Alembic migration is needed. Table creation does not change columns in existing tables.

## Project files

```text
sapho-engage/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── database.py
│   ├── models.py
│   └── services/
│       ├── __init__.py
│       └── placeholder_generator.py
├── templates/
│   ├── base.html
│   ├── index.html
│   └── post_detail.html
├── static/
│   └── styles.css
├── data/
│   ├── influencers.json
│   └── posts.json
├── scripts/
│   └── seed_data.py
├── tests/
│   └── test_workflow.py
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

## Response workflow

1. Select **Generate Response** on a queue card to open `GET /posts/{post_id}`. The original post appears beside the configuration form. Initial defaults are Thought Leadership, Professional, and Short; an existing draft restores its settings.
2. Submit the goal, tone, length, and optional custom instructions to `POST /posts/{post_id}/generate`. The backend validates the post and choices and calls `app/services/placeholder_generator.py`. The demo text includes the selected settings and echoes custom instructions without executing them; it does not attempt to match an actual word count.
3. Generation creates a `GeneratedResponse` with status `draft`, original `generated_text`, an identical `edited_text`, and UTC creation/update timestamps. A 303 redirect returns to the detail page with that response selected. Refreshing the resulting page does not generate another record.
4. Edit the textarea and select **Save Draft** to submit to `POST /responses/{response_id}/save`. Saving updates only the editable copy and update time, keeps status `draft`, and changes a `new` post to `drafted`. Other post statuses are preserved. The response and post updates commit together.
5. A 303 redirect shows **Draft saved.** Existing drafts appear newest first, with their creation date, settings, stored text, and status. **Edit draft** reopens an older draft. The original generated text remains stored separately.

Generation stores a draft immediately. Textarea changes persist only after Save Draft; navigating away or generating another placeholder before saving discards unsaved edits. Multiple generations create separate drafts. Drafts are never posted to LinkedIn.

Forms use FastAPI's standard [`Form` handling](https://fastapi.tiangolo.com/tutorial/request-forms/), which requires `python-multipart`. Nonexistent records return 404; malformed IDs, missing required form values, unsupported options, and blank saved text return 422. Errors use FastAPI's normal JSON error response.

## Automated checks

From the parent workspace directory (`sapho-linkedin-assistant`), run the following. If you are already inside `sapho-engage`, skip the `cd` command.

```bash
cd sapho-engage
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install 'httpx>=0.28,<1.0'
python -m unittest discover -s tests -v
```

HTTPX is only needed for the tests. The tests use temporary SQLite databases, leaving your local data untouched. They cover upgrading the Milestone 1 schema, seed/queue regression checks, configuration defaults, deterministic generation, saved edits and timestamps, HTML escaping, draft history and isolation, status transitions, and invalid requests.

To run Milestone 2 from an existing checkout:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.seed_data
uvicorn app.main:app --reload
```

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
- Open a post and inspect the desktop two-column layout and stacked narrow-screen layout. Check labels, keyboard navigation, and textarea resizing.
- Try different response settings and custom instructions. Confirm the demo header reflects the settings and that all draft text remains editable.
- Edit and save a response, confirm **Draft saved.**, then reload and revisit the queue. A previously `new` post should now be `drafted`.
- Generate a second draft and reopen the first via **Edit draft**. Verify newest-first history and that both drafts retain their own text.
- Whitespace-only saved text should return a 422 error; browser validation may prevent submitting a completely empty textarea. Use the browser Back button to return from an error page.
