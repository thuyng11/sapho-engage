from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import BASE_DIR, get_db, init_db
from app.models import Post


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
