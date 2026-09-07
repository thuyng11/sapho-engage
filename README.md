# Sapho LinkedIn Engagement Assistant

Milestones 1–6A of a take-home assessment for Sapho Bio: a curated HTML post queue and manual LinkedIn engagement workflow using FastAPI, Jinja2, SQLite, SQLAlchemy 2.x, and Gemini.

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
python -m scripts.import_influencers
python -m scripts.import_posts
uvicorn app.main:app --reload
```

Before generating a response, open `.env` and add your Google AI Studio key after `GEMINI_API_KEY=`. Keep the configured `GEMINI_MODEL` value. Never commit `.env` or paste the key into a command. Open http://127.0.0.1:8000/ to see curated posts newest first. The queue, filters, influencer ranking, activity history, and saved drafts remain browsable without an API key.

For demo fallback data, run `python -m scripts.seed_data`. Repeating any importer preserves existing statuses and response history. The application creates or additively upgrades the SQLite schema on startup; no database reset is required.

To create the tables without importing data:

```bash
python -c "from app.database import init_db; init_db()"
```

The application also creates missing tables on startup and shows an empty state if there are no posts. Existing databases are upgraded in place; keep the database file. The lightweight SQLite upgrade adds curated post fields and rebuilds only the `posts` table when needed to make engagement counts nullable. It preserves post IDs, statuses, and response relationships. No reset or Alembic migration is needed.

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
│   ├── post_detail.html
│   └── activity.html
├── static/
│   ├── styles.css
│   └── app.js
├── data/
│   ├── influencers.json
│   └── posts.json
├── scripts/
│   ├── seed_data.py
│   ├── import_influencers.py
│   └── import_posts.py
├── research/
│   ├── influencers.csv
│   ├── influencers_ranked_research.csv
│   └── posts.csv
├── tests/
│   ├── test_workflow.py
│   ├── test_llm_service.py
│   ├── test_influencers.py
│   ├── test_posts.py
│   └── test_engagement.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Data flow

1. `scripts/seed_data.py` keeps the fictional JSON fixtures available, while the two research importers load curated influencers and post summaries from CSV.
2. SQLite stores `influencers` and `posts`, linked by `posts.influencer_id`. Foreign key enforcement is enabled for each connection. `Post.status` defaults to `new` when omitted.
3. `GET /` opens a request-scoped session and selects curated posts newest first. It selects demo posts only when no curated posts exist.
4. FastAPI passes the results to `templates/index.html`. Jinja labels curated content as a research summary, renders available engagement and provenance, and links to the original public post. FastAPI serves CSS at `/static/styles.css`.

The implementation uses [SQLAlchemy 2.x typed models and selects](https://docs.sqlalchemy.org/en/20/orm/quickstart.html) and [FastAPI's Jinja2 template integration](https://fastapi.tiangolo.com/advanced/templates/).

## Response workflow

1. Select **Generate Response** on a queue card to open `GET /posts/{post_id}`. The original post appears beside the configuration form. Initial defaults are Thought Leadership, Professional, and Short; an existing draft restores its settings.
2. Submit the goal, tone, length, and optional custom instructions to `POST /posts/{post_id}/generate`. The backend validates the post and choices and calls `app/services/llm_service.py`. It sends the post, author context, selected settings, and optional instructions to Gemini. No API keys or internal prompt text are sent to the frontend.
3. Successful generation creates a `GeneratedResponse` with status `draft`, original `generated_text`, an identical `edited_text`, and UTC creation/update timestamps. A 303 redirect returns to the detail page with that response selected. Refreshing the resulting page does not generate another record.
4. Edit the textarea and select **Save Draft** to submit to `POST /responses/{response_id}/save`. Saving updates only the editable copy and update time, keeps status `draft`, and changes a `new` post to `drafted`. Other post statuses are preserved. The response and post updates commit together.
5. **Regenerate** reuses the selected response's post, goal, tone, length, and custom instruction. A successful call creates a separate draft; a failed call leaves history unchanged.
6. **Approve** saves the current textarea and changes both response and post to `approved`. Saving meaningful changes to approved text returns both to the draft stage so the changed text requires approval again.
7. After approval, copy the current textarea through the browser Clipboard API, open the original post, comment manually, and select **Mark as Posted**. This changes both statuses to `posted`; it does not contact LinkedIn.
8. Existing responses remain newest first in history. Server-side lifecycle events appear newest first at `/activity`.

Generation stores a draft immediately. Textarea changes persist only after Save Draft; navigating away or generating another response before saving discards unsaved edits. Multiple generations create separate drafts. Drafts are never posted to LinkedIn.

## Engagement workflow

The response lifecycle is `draft` → `approved` → `posted`; the associated post moves from `new` to `drafted`, `approved`, and `posted`. Marking a response as posted requires approval. Posted responses are read-only in the page, and the application describes posting as manual tracking rather than verified publishing.

Generation, regeneration, draft saves, approvals, and manual posted actions create lightweight `ActivityLog` rows. Clipboard use stays in the browser and is not persisted. The activity page shows timestamp, influencer, post topic or context, action, and response status. Existing drafts remain valid; the new activity table is created additively on startup.

The main queue accepts `influencer`, `topic`, and `status` query parameters. Filter options come from the currently available curated dataset, or from demo data when fallback mode is active. Unknown values are ignored with visible feedback. Curated-only queue selection and newest-first ordering remain unchanged.

Forms use FastAPI's standard [`Form` handling](https://fastapi.tiangolo.com/tutorial/request-forms/), which requires `python-multipart`. Nonexistent records return 404; malformed IDs, missing required form values, unsupported options, and blank saved text return 422. Validation errors use FastAPI's normal JSON error response. Generation failures return the detail page with HTTP 503, a concise error, and all submitted configuration fields preserved for retry. Existing drafts remain visible; no new row is inserted. There is no placeholder fallback.

## Gemini service and prompt

`build_prompt()` separates higher-priority instructions from JSON-encoded source data. Instructions contain the task, supplied Sapho Bio facts, brand voice, accuracy rules, distinct goal/tone guidance, explicit sentence/word targets, and output-only requirements. The JSON input contains the original post, author name/title/company, and optional custom instructions. Source data is not authoritative instruction text; custom preferences must remain subordinate to the brand and accuracy rules.

Length targets are Short: 1–2 sentences / ideally at most 60 words; Medium: 2–3 / 100 words; Detailed: 3–5 / 160 words. These are prompt targets, not automatic truncation rules. The supplied brand context is the only authorized source of company claims. Prompts reduce unsupported claims but cannot guarantee output quality, so review generated comments before using them.

The service creates a request-scoped `genai.Client` only when generating, calls `client.models.generate_content(model=..., contents=..., config=GenerateContentConfig(system_instruction=...))`, and reads `response.text`. It uses a 60-second SDK network timeout and one attempt per submission. SDK API errors and HTTP transport failures become a small `GenerationError` for the route. Missing configuration, blocked output, empty text, and incomplete responses use the same failure path. Only responses with a normal STOP finish reason are accepted. Provider details and API keys are not rendered or logged by application code. The normal workflow never calls the retained placeholder generator.

References: [official Python SDK guidance](https://googleapis.github.io/python-genai/), [Gemini text generation](https://ai.google.dev/gemini-api/docs/text-generation), and [Gemini 3.7 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash). Use a Gemini Developer API key from [Google AI Studio](https://aistudio.google.com/apikey). Your API project must have access to the configured model.

## Influencer research

The prototype ranks manually researched compounding-pharmacy industry voices with one reusable formula: 40% industry relevance + 20% posting activity + 20% engagement + 20% professional credibility. Component scores must be numeric values from 0 through 100; the calculated overall score is rounded to one decimal place. This ranking is a research heuristic, not an official LinkedIn metric.

`research/influencers.csv` is the application-ready source. `research/influencers_ranked_research.csv` retains extra provenance and research notes and is not imported. The importer calculates `overall_score` itself, marks curated records as `manual_research`, and identifies records by normalized LinkedIn URL. Re-running it skips unchanged profiles and updates changed profiles without duplicating them or changing their post relationships.

Candidates were manually researched from publicly available information. Activity and engagement observations are a snapshot and can change. The application does not scrape LinkedIn or create posts for real people.

From inside `sapho-engage`, import the curated data with:

```bash
source .venv/bin/activate
python -m scripts.import_influencers
```

The importer validates every row before writing. Invalid rows are skipped with the file, row, and reason; all valid rows are committed together in one transaction. Existing databases are upgraded in place with the new influencer scoring and source fields; sample posts and saved response drafts are retained.

## Curated LinkedIn Post Dataset

The MVP uses manually researched information from publicly accessible LinkedIn posts. `research/posts.csv` contains concise research summaries rather than complete post text collected through automated scraping. Public availability differs by profile, so the number of researched posts varies across influencers.

Import the dataset with:

```bash
python -m scripts.import_posts
```

The importer matches each post to an existing curated influencer by normalized LinkedIn profile URL. It uses the post URL for duplicate-safe upserts, preserves existing post statuses and generated-response history, and marks imported posts as `manual_research`. Blank reaction or comment counts remain unknown (`NULL`) rather than becoming a misleading zero. Re-running an unchanged import skips every existing row.

The normal queue shows only curated posts once at least one has been imported. The six explicitly marked demo posts remain in SQLite for tests and fallback use.

## Manual engagement smoke test

```bash
cd /Users/minhthuynguyen/sapho-linkedin-assistant/sapho-engage
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.import_influencers
python -m scripts.import_posts
python -m unittest discover -s tests -v
uvicorn app.main:app --reload
```

With `GEMINI_API_KEY` configured in `.env`:

1. Open http://127.0.0.1:8000/ and confirm the curated post and influencer counts.
2. Apply individual and combined queue filters.
3. Select a combination with no results and confirm the filtered empty state is useful.
4. Open an original LinkedIn post and confirm it uses a new tab.
5. Open a curated post and select **Generate Response**; confirm the button changes to **Generating...** and cannot be submitted twice.
6. Confirm Gemini returns an editable response with the chosen settings.
7. Edit the response and select **Save Draft**.
8. Select **Regenerate** and confirm its loading state and a second history record.
9. Select **Copy Response** and confirm **Copied ✓** appears briefly.
10. Approve the current response and confirm both lifecycle badges update.
11. Use the prominent **Open LinkedIn** action.
12. After manually commenting, select **Mark as Posted** and confirm its manual-tracking message.
13. Return to the queue and confirm the post displays Posted.
14. Open http://127.0.0.1:8000/activity and confirm lifecycle events appear newest first.
15. Open http://127.0.0.1:8000/influencers and confirm the top-10 ranking still loads.
16. Narrow the browser and confirm filters stack and the post-detail columns collapse cleanly.
17. Stop and restart Uvicorn, then confirm statuses, response history, and activity persist.
18. Remove `GEMINI_API_KEY`, restart, and confirm browsing still works while generation shows a concise configuration error.

## Automated checks

From the parent workspace directory (`sapho-linkedin-assistant`), run the following. If you are already inside `sapho-engage`, skip the `cd` command.

```bash
cd sapho-engage
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Tests use temporary SQLite databases and mocked generation/SDK calls; they never call Gemini, LinkedIn, or another external service and require no API key. Coverage includes importers, prompt construction, generation failures, response lifecycle, filters, empty states, loading-state markup, provenance links, and prior milestone regressions.

## Configuration and sample format

`.env` is optional. `DATABASE_PATH` defaults to `sapho.db` in this project directory. Relative paths are resolved from this directory; absolute paths also work. The database file's parent directory must exist. An exported environment variable takes precedence over `.env`. Response generation requires `GEMINI_API_KEY`. `GEMINI_MODEL` defaults to `gemini-3.7-flash` when unset; an explicitly blank model is a configuration error. The existing python-dotenv setup loads the project `.env` at startup. Restart the server after changing `.env`. Git ignores `.env`; `.env.example` contains no key.

JSON fields match the models. Each post's `influencer_id` must reference an existing influencer or one supplied in the influencers file. IDs must be unique within each file. Use ISO 8601 timestamps with an offset or `Z`; timestamps without an offset are treated as UTC. SQLite stores dates without time zone information after normalization, and the page labels them UTC. Status is a free-text field; the fixtures use `new` and `reviewed` to demonstrate display only.
