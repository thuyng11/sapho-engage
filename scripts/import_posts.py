import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select

from app.database import BASE_DIR, SessionLocal, init_db
from app.models import Influencer, Post

RESEARCH_FILE = BASE_DIR / "research/posts.csv"
REQUIRED_FIELDS = (
    "influencer_linkedin_url", "content", "post_url", "posted_at", "topic"
)


@dataclass
class ImportSummary:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0


class RowValidationError(ValueError):
    pass


def normalize_url(value: str) -> str:
    return value.strip().rstrip("/").lower()


def validate_url(value: str, field: str, row_number: int) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RowValidationError(f"row {row_number}: {field} must be a valid HTTP URL")
    return value


def parse_datetime(value: str, field: str, row_number: int, required: bool) -> datetime | None:
    value = value.strip()
    if not value:
        if required:
            raise RowValidationError(f"row {row_number}: {field} is required")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RowValidationError(f"row {row_number}: {field} must be a valid ISO date") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def parse_nullable_count(value: str, field: str, row_number: int) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        count = int(value)
    except ValueError as exc:
        raise RowValidationError(
            f"row {row_number}: {field} must be blank or a non-negative integer"
        ) from exc
    if count < 0:
        raise RowValidationError(
            f"row {row_number}: {field} must be blank or a non-negative integer"
        )
    return count


def validate_row(
    row: dict[str, str], row_number: int, curated_by_url: dict[str, Influencer]
) -> dict:
    for field in REQUIRED_FIELDS:
        if not (row.get(field) or "").strip():
            raise RowValidationError(f"row {row_number}: {field} is required")

    influencer_url = validate_url(
        row["influencer_linkedin_url"], "influencer_linkedin_url", row_number
    )
    influencer = curated_by_url.get(normalize_url(influencer_url))
    if influencer is None:
        raise RowValidationError(f"row {row_number}: unknown influencer LinkedIn URL")

    source_url = (row.get("source_url") or "").strip()
    return {
        "influencer_id": influencer.id,
        "content": row["content"].strip(),
        "content_type": (row.get("content_type") or "").strip(),
        "post_url": validate_url(row["post_url"], "post_url", row_number),
        "posted_at": parse_datetime(row["posted_at"], "posted_at", row_number, True),
        "likes": parse_nullable_count(row.get("likes") or "", "likes", row_number),
        "comments": parse_nullable_count(row.get("comments") or "", "comments", row_number),
        "topic": row["topic"].strip(),
        "collected_at": parse_datetime(
            row.get("collected_at") or "", "collected_at", row_number, False
        ),
        "verification_status": (row.get("verification_status") or "").strip(),
        "engagement_note": (row.get("engagement_note") or "").strip() or None,
        "source_url": validate_url(source_url, "source_url", row_number) if source_url else None,
        "source_type": "manual_research",
        "is_sample": False,
    }


def read_and_validate(
    path: Path, curated_by_url: dict[str, Influencer]
) -> tuple[list[dict], list[str]]:
    valid: list[dict] = []
    validation_errors: list[str] = []
    seen_post_urls: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        missing = [field for field in REQUIRED_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            return [], [f"missing required columns: {', '.join(missing)}"]
        for row_number, row in enumerate(reader, start=2):
            try:
                values = validate_row(row, row_number, curated_by_url)
                post_url_key = normalize_url(values["post_url"])
                if post_url_key in seen_post_urls:
                    raise RowValidationError(
                        f"row {row_number}: duplicate post_url "
                        f"(first seen on row {seen_post_urls[post_url_key]})"
                    )
                seen_post_urls[post_url_key] = row_number
                valid.append(values)
            except RowValidationError as exc:
                validation_errors.append(str(exc))
    return valid, validation_errors


def import_posts(path: Path = RESEARCH_FILE, session_factory=SessionLocal) -> ImportSummary:
    init_db()
    summary = ImportSummary()
    with session_factory.begin() as session:
        curated_by_url = {
            normalize_url(influencer.linkedin_url): influencer
            for influencer in session.scalars(
                select(Influencer).where(
                    Influencer.is_sample.is_(False),
                    Influencer.source_type == "manual_research",
                )
            ).all()
        }
        rows, validation_errors = read_and_validate(path, curated_by_url)
        summary.errors = len(validation_errors)
        summary.skipped = len(validation_errors)
        for error in validation_errors:
            print(f"{path} {error}")

        existing = {
            normalize_url(post.post_url): post
            for post in session.scalars(select(Post)).all()
        }
        for values in rows:
            post_url_key = normalize_url(values["post_url"])
            post = existing.get(post_url_key)
            if post is None:
                post = Post(**values, status="new")
                session.add(post)
                existing[post_url_key] = post
                summary.created += 1
            elif all(getattr(post, field) == value for field, value in values.items()):
                summary.skipped += 1
            else:
                for field, value in values.items():
                    setattr(post, field, value)
                summary.updated += 1
    return summary


def print_summary(summary: ImportSummary) -> None:
    print("Curated Post Import")
    print(f"Created: {summary.created}")
    print(f"Updated: {summary.updated}")
    print(f"Skipped: {summary.skipped}")
    print(f"Errors: {summary.errors}")


if __name__ == "__main__":
    print_summary(import_posts())
