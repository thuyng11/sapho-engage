from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import BASE_DIR, get_db, init_db
from app.models import GeneratedResponse, Post, utc_now
from app.services.placeholder_generator import generate_placeholder_response

GOALS = ("Thought Leadership", "Engagement", "Lead Generation", "Relationship Building")
TONES = ("Professional", "Conversational", "Technical", "Educational")
LENGTHS = ("Short", "Medium", "Detailed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Sapho LinkedIn Assistant", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Annotated[Session, Depends(get_db)]):
    posts = db.scalars(
        select(Post)
        .options(joinedload(Post.influencer))
        .order_by(Post.posted_at.desc(), Post.id.desc())
    ).all()
    return templates.TemplateResponse(
        request=request, name="index.html", context={"posts": posts}
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
):
    post = get_post_or_404(db, post_id)
    drafts = db.scalars(
        select(GeneratedResponse)
        .where(GeneratedResponse.post_id == post_id)
        .order_by(GeneratedResponse.created_at.desc(), GeneratedResponse.id.desc())
    ).all()
    active_response = drafts[0] if drafts else None
    if response_id is not None:
        active_response = next((draft for draft in drafts if draft.id == response_id), None)
        if active_response is None:
            raise HTTPException(status_code=404, detail="Response not found for this post.")
    return templates.TemplateResponse(
        request=request,
        name="post_detail.html",
        context={
            "post": post, "drafts": drafts, "active_response": active_response,
            "goals": GOALS, "tones": TONES, "lengths": LENGTHS,
            "saved": saved and active_response is not None,
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
    custom_instruction = (custom_instruction or "").strip() or None
    text = generate_placeholder_response(post, goal, tone, length, custom_instruction)
    response = GeneratedResponse(
        post=post, goal=goal, tone=tone, length=length,
        custom_instruction=custom_instruction,
        generated_text=text, edited_text=text, status="draft",
    )
    db.add(response)
    db.commit()
    url = request.url_for("post_detail", post_id=post_id).include_query_params(response_id=response.id)
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
    response.edited_text = edited_text
    response.updated_at = utc_now()
    response.status = "draft"
    if response.post.status == "new":
        response.post.status = "drafted"
    db.commit()
    url = request.url_for("post_detail", post_id=response.post_id).include_query_params(
        response_id=response.id, saved="true"
    )
    return RedirectResponse(url=str(url), status_code=303)
