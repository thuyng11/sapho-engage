from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import BASE_DIR, get_db, init_db
from app.models import ActivityLog, GeneratedResponse, Influencer, Post, utc_now
from app.services.llm_service import GenerationError, generate_response as generate_llm_response

GOALS = ("Thought Leadership", "Engagement", "Lead Generation", "Relationship Building")
TONES = ("Professional", "Conversational", "Technical", "Educational")
LENGTHS = ("Short", "Medium", "Detailed")
POST_STATUSES = ("new", "drafted", "approved", "posted")
ACTIVITY_LABELS = {
    "generated": "Response generated",
    "regenerated": "Response regenerated",
    "draft_saved": "Draft saved",
    "approved": "Response approved",
    "marked_posted": "Marked as posted",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Sapho LinkedIn Assistant", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Annotated[Session, Depends(get_db)]):
    curated_query = (
        select(Post)
        .options(joinedload(Post.influencer))
        .where(
            Post.is_sample.is_(False),
            Post.source_type == "manual_research",
        )
        .order_by(Post.posted_at.desc(), Post.id.desc())
    )
    posts = db.scalars(curated_query).all()
    using_curated = bool(posts)
    if not using_curated:
        posts = db.scalars(
            select(Post)
            .options(joinedload(Post.influencer))
            .where(Post.is_sample.is_(True))
            .order_by(Post.posted_at.desc(), Post.id.desc())
        ).all()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"posts": posts, "using_curated": using_curated},
    )


@app.get("/influencers", response_class=HTMLResponse)
def influencers(request: Request, db: Annotated[Session, Depends(get_db)]):
    ranked = db.scalars(
        select(Influencer)
        .where(
            Influencer.is_sample.is_(False),
            Influencer.source_type == "manual_research",
        )
        .order_by(Influencer.overall_score.desc(), Influencer.name.asc())
        .limit(10)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="influencers.html",
        context={"influencers": ranked},
    )


def get_post_or_404(db: Session, post_id: int) -> Post:
    post = db.scalar(select(Post).options(joinedload(Post.influencer)).where(Post.id == post_id))
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found.")
    return post


@app.get("/posts/{post_id}", response_class=HTMLResponse)
def post_detail(
    request: Request,
    post_id: int,
    db: Annotated[Session, Depends(get_db)],
    response_id: int | None = None,
    saved: bool = False,
    approved: bool = False,
    posted: bool = False,
    regenerated: bool = False,
):
    post = get_post_or_404(db, post_id)
    return render_post_detail(
        request, post, db, response_id=response_id, saved=saved,
        approved=approved, posted=posted, regenerated=regenerated,
    )


def render_post_detail(
    request: Request, post: Post, db: Session,
    response_id: int | None = None, saved: bool = False,
    approved: bool = False, posted: bool = False, regenerated: bool = False,
    form_values: dict | None = None, error: str | None = None,
):
    drafts = db.scalars(
        select(GeneratedResponse)
        .where(GeneratedResponse.post_id == post.id)
        .order_by(GeneratedResponse.created_at.desc(), GeneratedResponse.id.desc())
    ).all()
    active_response = drafts[0] if drafts else None
    if response_id is not None:
        active_response = next((draft for draft in drafts if draft.id == response_id), None)
        if active_response is None:
            raise HTTPException(status_code=404, detail="Response not found for this post.")
    if form_values is None:
        form_values = {
            "goal": active_response.goal if active_response else "Thought Leadership",
            "tone": active_response.tone if active_response else "Professional",
            "length": active_response.length if active_response else "Short",
            "custom_instruction": (active_response.custom_instruction or "") if active_response else "",
        }
    return templates.TemplateResponse(
        request=request,
        name="post_detail.html",
        status_code=503 if error else 200,
        context={
            "post": post, "drafts": drafts, "active_response": active_response,
            "goals": GOALS, "tones": TONES, "lengths": LENGTHS,
            "saved": saved and active_response is not None,
            "approved": approved and active_response is not None,
            "posted": posted and active_response is not None,
            "regenerated": regenerated and active_response is not None,
            "form_values": form_values, "error": error,
        },
    )


@app.post("/posts/{post_id}/generate")
def generate_response(
    request: Request,
    post_id: int,
    db: Annotated[Session, Depends(get_db)],
    goal: Annotated[str, Form()],
    tone: Annotated[str, Form()],
    length: Annotated[str, Form()],
    custom_instruction: Annotated[str | None, Form()] = None,
):
    post = get_post_or_404(db, post_id)
    for field, value, choices in (("goal", goal, GOALS), ("tone", tone, TONES), ("length", length, LENGTHS)):
        if value not in choices:
            raise HTTPException(status_code=422, detail=f"Invalid {field}.")
    form_values = {
        "goal": goal, "tone": tone, "length": length,
        "custom_instruction": custom_instruction or "",
    }
    custom_instruction = (custom_instruction or "").strip() or None
    try:
        text = generate_llm_response(post, goal, tone, length, custom_instruction)
    except GenerationError:
        return render_post_detail(
            request, post, db, form_values=form_values,
            error="Unable to generate a response right now. Please check the API configuration and try again.",
        )
    response = GeneratedResponse(
        post=post, goal=goal, tone=tone, length=length,
        custom_instruction=custom_instruction,
        generated_text=text, edited_text=text, status="draft",
    )
    if post.status == "new":
        post.status = "drafted"
    db.add(response)
    db.add(ActivityLog(post=post, response=response, action="generated"))
    db.commit()
    url = request.url_for("post_detail", post_id=post_id).include_query_params(response_id=response.id)
    return RedirectResponse(url=str(url), status_code=303)


@app.post("/responses/{response_id}/regenerate")
def regenerate_response(
    request: Request,
    response_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    current = db.get(GeneratedResponse, response_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Response not found.")
    if current.status != "draft":
        raise HTTPException(status_code=409, detail="Only draft responses can be regenerated.")
    post = get_post_or_404(db, current.post_id)
    try:
        text = generate_llm_response(
            post, current.goal, current.tone, current.length,
            current.custom_instruction,
        )
    except GenerationError:
        return render_post_detail(
            request, post, db, response_id=current.id,
            error="Unable to generate a response right now. Please check the API configuration and try again.",
        )
    response = GeneratedResponse(
        post=post,
        goal=current.goal,
        tone=current.tone,
        length=current.length,
        custom_instruction=current.custom_instruction,
        generated_text=text,
        edited_text=text,
        status="draft",
    )
    db.add(response)
    db.add(ActivityLog(post=post, response=response, action="regenerated"))
    db.commit()
    url = request.url_for("post_detail", post_id=post.id).include_query_params(
        response_id=response.id, regenerated="true"
    )
    return RedirectResponse(url=str(url), status_code=303)


@app.post("/responses/{response_id}/save")
def save_response(
    request: Request,
    response_id: int,
    db: Annotated[Session, Depends(get_db)],
    edited_text: Annotated[str, Form()],
):
    response = db.get(GeneratedResponse, response_id)
    if response is None:
        raise HTTPException(status_code=404, detail="Response not found.")
    if not edited_text.strip():
        raise HTTPException(status_code=422, detail="Edited response must not be empty.")
    changed = edited_text != (response.edited_text or response.generated_text)
    response.edited_text = edited_text
    response.updated_at = utc_now()
    if changed and response.status == "approved":
        response.status = "draft"
        response.post.status = "drafted"
    if response.status == "draft" and response.post.status == "new":
        response.post.status = "drafted"
    db.add(ActivityLog(post=response.post, response=response, action="draft_saved"))
    db.commit()
    url = request.url_for("post_detail", post_id=response.post_id).include_query_params(
        response_id=response.id, saved="true"
    )
    return RedirectResponse(url=str(url), status_code=303)


@app.post("/responses/{response_id}/approve")
def approve_response(
    request: Request,
    response_id: int,
    db: Annotated[Session, Depends(get_db)],
    edited_text: Annotated[str, Form()],
):
    response = db.get(GeneratedResponse, response_id)
    if response is None:
        raise HTTPException(status_code=404, detail="Response not found.")
    if not edited_text.strip():
        raise HTTPException(status_code=422, detail="Edited response must not be empty.")
    if response.status == "posted":
        raise HTTPException(status_code=409, detail="A posted response cannot be approved again.")
    response.edited_text = edited_text
    response.updated_at = utc_now()
    response.status = "approved"
    response.post.status = "approved"
    db.add(ActivityLog(post=response.post, response=response, action="approved"))
    db.commit()
    url = request.url_for("post_detail", post_id=response.post_id).include_query_params(
        response_id=response.id, approved="true"
    )
    return RedirectResponse(url=str(url), status_code=303)


@app.post("/responses/{response_id}/posted")
def mark_response_posted(
    request: Request,
    response_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    response = db.get(GeneratedResponse, response_id)
    if response is None:
        raise HTTPException(status_code=404, detail="Response not found.")
    if response.status != "approved":
        raise HTTPException(
            status_code=409, detail="Response must be approved before marking it as posted."
        )
    response.status = "posted"
    response.updated_at = utc_now()
    response.post.status = "posted"
    db.add(ActivityLog(post=response.post, response=response, action="marked_posted"))
    db.commit()
    url = request.url_for("post_detail", post_id=response.post_id).include_query_params(
        response_id=response.id, posted="true"
    )
    return RedirectResponse(url=str(url), status_code=303)


@app.get("/activity", response_class=HTMLResponse)
def activity(request: Request, db: Annotated[Session, Depends(get_db)]):
    events = db.scalars(
        select(ActivityLog)
        .options(
            joinedload(ActivityLog.post).joinedload(Post.influencer),
            joinedload(ActivityLog.response),
        )
        .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="activity.html",
        context={"events": events, "action_labels": ACTIVITY_LABELS},
    )
