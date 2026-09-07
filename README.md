# Sapho LinkedIn Assistant

Milestones 1–3 of a take-home assessment for Sapho Bio: a local HTML post queue and editable Gemini-generated response drafts using FastAPI, Jinja2, SQLite, and SQLAlchemy 2.x.

**All 3 sample influencers and 6 sample posts are fictional.** Names, companies, content, dates, relevance scores, and engagement counts are invented. LinkedIn URLs are clearly named sample placeholders and do not identify real profiles or posts.

The normal generation workflow uses the official Google Gen AI Python SDK and Gemini generate-content API. Existing sample posts and older demo drafts are preserved. There is no scraping, authentication, deployment configuration, or LinkedIn posting.

## Local setup

Use Python 3.10 or newer. From the workspace directory containing `sapho-engage`, run these macOS/Linux commands:

```bash
cd sapho-engage
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
python -m scripts.seed_data
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/ to see all six posts, newest first. Before generating, configure your API key as described in the manual smoke test below. Queue browsing and draft editing do not need an API key.

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
│       ├── placeholder_generator.py
│       ├── llm_service.py
│       └── influencer_scoring.py
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
│   ├── seed_data.py
│   └── import_influencers.py
├── research/
│   ├── influencers.csv
│   └── influencers_ranked_research.csv
├── tests/
│   ├── test_workflow.py
│   ├── test_llm_service.py
│   └── test_influencers.py
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
2. Submit the goal, tone, length, and optional custom instructions to `POST /posts/{post_id}/generate`. The backend validates the post and choices and calls `app/services/llm_service.py`. It sends the post, author context, selected settings, and optional instructions to Gemini. No API keys or internal prompt text are sent to the frontend.
3. Successful generation creates a `GeneratedResponse` with status `draft`, original `generated_text`, an identical `edited_text`, and UTC creation/update timestamps. A 303 redirect returns to the detail page with that response selected. Refreshing the resulting page does not generate another record.
4. Edit the textarea and select **Save Draft** to submit to `POST /responses/{response_id}/save`. Saving updates only the editable copy and update time, keeps status `draft`, and changes a `new` post to `drafted`. Other post statuses are preserved. The response and post updates commit together.
5. A 303 redirect shows **Draft saved.** Existing drafts appear newest first, with their creation date, settings, stored text, and status. **Edit draft** reopens an older draft. The original generated text remains stored separately.

Generation stores a draft immediately. Textarea changes persist only after Save Draft; navigating away or generating another response before saving discards unsaved edits. Multiple generations create separate drafts. Drafts are never posted to LinkedIn.

Forms use FastAPI's standard [`Form` handling](https://fastapi.tiangolo.com/tutorial/request-forms/), which requires `python-multipart`. Nonexistent records return 404; malformed IDs, missing required form values, unsupported options, and blank saved text return 422. Validation errors use FastAPI's normal JSON error response. Generation failures return the detail page with HTTP 503, a concise error, and all submitted configuration fields preserved for retry. Existing drafts remain visible; no new row is inserted. There is no placeholder fallback.

## Gemini service and prompt

`build_prompt()` separates higher-priority instructions from JSON-encoded source data. Instructions contain the task, supplied Sapho Bio facts, brand voice, accuracy rules, distinct goal/tone guidance, explicit sentence/word targets, and output-only requirements. The JSON input contains the original post, author name/title/company, and optional custom instructions. Source data is not authoritative instruction text; custom preferences must remain subordinate to the brand and accuracy rules.

Length targets are Short: 1–2 sentences / ideally at most 60 words; Medium: 2–3 / 100 words; Detailed: 3–5 / 160 words. These are prompt targets, not automatic truncation rules. The supplied brand context is the only authorized source of company claims. Prompts reduce unsupported claims but cannot guarantee output quality, so review generated comments before using them.

The service creates a request-scoped `genai.Client` only when generating, calls `client.models.generate_content(model=..., contents=..., config=GenerateContentConfig(system_instruction=...))`, and reads `response.text`. It uses a 60-second SDK network timeout and one attempt per submission. SDK API errors and HTTP transport failures become a small `GenerationError` for the route. Missing configuration, blocked output, empty text, and incomplete responses use the same failure path. Only responses with a normal STOP finish reason are accepted. Provider details and API keys are not rendered or logged by application code. The normal workflow never calls the retained placeholder generator.

References: [official Python SDK guidance](https://googleapis.github.io/python-genai/), [Gemini text generation](https://ai.google.dev/gemini-api/docs/text-generation), and [Gemini 3.7 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash). Use a Gemini Developer API key from [Google AI Studio](https://aistudio.google.com/apikey). Your API project must have access to the configured model.

## Influencer research

The prototype ranks manually researched compounding-pharmacy industry voices with one reusable formula: 40% industry relevance + 20% posting activity + 20% engagement + 20% professional credibility. Component scores must be numeric values from 0 through 100; the calculated overall score is rounded to one decimal place. This ranking is a research heuristic, not an official LinkedIn metric.

`research/influencers.csv` is the application-ready source. `research/influencers_ranked_research.csv` retains extra provenance and research notes and is not imported. The importer calculates `overall_score` itself, marks curated records as `manual_research`, and identifies records by normalized LinkedIn URL. Re-running it skips unchanged profiles and updates changed profiles without duplicating them or changing their post relationships.

Candidates were manually researched from publicly available information. Activity and engagement observations are a snapshot and can change. Verified recent-post ingestion will be handled separately; the application does not scrape LinkedIn or create posts for real people.

From inside `sapho-engage`, import the curated data with:

```bash
source .venv/bin/activate
python -m scripts.import_influencers
```

The importer validates every row before writing. Invalid rows are skipped with the file, row, and reason; all valid rows are committed together in one transaction. Existing databases are upgraded in place with the new influencer scoring and source fields; sample posts and saved response drafts are retained.

To verify the influencer milestone locally from the parent workspace directory:

```bash
cd sapho-engage
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.import_influencers
python -m unittest discover -s tests -v
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/influencers and confirm exactly 10 curated profiles appear in descending score order, with names, titles, companies, component scores, notes, and LinkedIn links matching `research/influencers.csv`. Confirm sample names do not appear there. Then open http://127.0.0.1:8000/ and a post detail page to confirm the six sample posts, Gemini generation, and existing saved drafts still work.

## Manual Gemini smoke test

From `sapho-linkedin-assistant` (skip `cd` if already inside `sapho-engage`):

```bash
cd sapho-engage
source .venv/bin/activate
python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
nano .env
```

In the local editor, add your Google AI Studio API key after `GEMINI_API_KEY=` and set `GEMINI_MODEL=gemini-3.7-flash`. If you already have an older `.env`, add these two entries (old OpenAI variables are not used); keep your existing `DATABASE_PATH`. Do not print the key or paste it into a shell command. Save the file, exit the editor, then run:

```bash
git check-ignore .env
python -m scripts.seed_data
uvicorn app.main:app --reload
```

1. Open http://127.0.0.1:8000/posts/1. Select a goal, tone, and length and optionally enter `End with a question.`
2. Select **Generate Response** once. This makes a real Gemini API request, subject to your project’s quota and billing settings. Check that the editable text is a proposed comment, not a demo placeholder; inspect relevance, accuracy, tone, and length.
3. In another terminal, from `sapho-engage` with the virtual environment active, inspect the newest SQLite draft:

```bash
python - <<'PY'
from sqlalchemy import select
from app.database import SessionLocal
from app.models import GeneratedResponse
with SessionLocal() as db:
    draft = db.scalar(select(GeneratedResponse).where(GeneratedResponse.post_id == 1).order_by(GeneratedResponse.id.desc()))
    if draft is None:
        print("No draft exists for post 1.")
    else:
        print("Draft:", draft.id, "Post:", draft.post_id, "Status:", draft.status)
        print("Settings:", draft.goal, draft.tone, draft.length)
        print("Generated:", draft.generated_text)
        print("Edited:", draft.edited_text)
        print("Updated:", draft.updated_at)
PY
```

4. Initially, generated and edited text should match. Change the text in the browser and select **Save Draft**. Confirm **Draft saved.**, reload, and rerun the SQLite check: edited text should change while generated text remains the original output.
5. Generate a second response, then use **Edit draft** to reopen the first. Confirm newest-first history and that both drafts retain their text. Older Milestone 2 placeholders remain as historical drafts.
6. To inspect failure handling without an API request, stop the server and run `GEMINI_API_KEY= uvicorn app.main:app --reload`. Try generating with nondefault settings and custom instructions. Confirm an inline error, retained fields, and no new draft. Stop and restart with the normal command to restore `.env` configuration.

## Automated checks

From the parent workspace directory (`sapho-linkedin-assistant`), run the following. If you are already inside `sapho-engage`, skip the `cd` command.

```bash
cd sapho-engage
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install 'httpx>=0.28,<1.0'
python -m unittest discover -s tests -v
```

Tests use temporary SQLite databases and mocked generation/SDK calls; they never call the real API and require no API key. Existing queue, seed, editing, saving, status, and draft-history regressions remain covered. Additional tests check exact output persistence, configuration forwarding, all 48 prompt combinations, error rendering, missing configuration, provider/network/timeout errors, and empty or incomplete output. The placeholder utility is retained and tested independently. HTTPX is also a dependency of the Google Gen AI SDK; the explicit test install command ensures a compatible test-client version.

To run Milestone 3 from an existing checkout, inside `sapho-engage`:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.seed_data
uvicorn app.main:app --reload
```

## Configuration and sample format

`.env` is optional. `DATABASE_PATH` defaults to `sapho.db` in this project directory. Relative paths are resolved from this directory; absolute paths also work. The database file's parent directory must exist. An exported environment variable takes precedence over `.env`. Response generation requires `GEMINI_API_KEY`. `GEMINI_MODEL` defaults to `gemini-3.7-flash` when unset; an explicitly blank model is a configuration error. The existing python-dotenv setup loads the project `.env` at startup. Restart the server after changing `.env`. Git ignores `.env`; `.env.example` contains no key.

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
- Try different response settings and custom instructions. Review whether the comment responds specifically to the post, follows the selected goal/tone/length, avoids unsupported claims, and remains editable.
- Edit and save a response, confirm **Draft saved.**, then reload and revisit the queue. A previously `new` post should now be `drafted`.
- Generate a second draft and reopen the first via **Edit draft**. Verify newest-first history and that both drafts retain their own text.
- Whitespace-only saved text should return a 422 error; browser validation may prevent submitting a completely empty textarea. Use the browser Back button to return from an error page.
