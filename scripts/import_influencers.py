import csv
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from app.database import BASE_DIR, SessionLocal, init_db
from app.models import Influencer
from app.services.influencer_scoring import calculate_overall_score

RESEARCH_FILE = BASE_DIR / "research/influencers.csv"
REQUIRED_FIELDS = (
    "name", "company", "linkedin_url", "relevance_score", "activity_score",
    "engagement_score", "credibility_score",
)
SCORE_FIELDS = (
    "relevance_score", "activity_score", "engagement_score", "credibility_score"
)


@dataclass
class ImportSummary:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0


class RowValidationError(ValueError):
    pass


def validate_row(row: dict[str, str], row_number: int) -> dict:
    for field in REQUIRED_FIELDS:
        if not (row.get(field) or "").strip():
            raise RowValidationError(f"row {row_number}: {field} is required")

    values: dict = {
        "name": row["name"].strip(),
        "title": (row.get("title") or "").strip(),
        "company": row["company"].strip(),
        "linkedin_url": row["linkedin_url"].strip(),
        "notes": (row.get("notes") or "").strip(),
    }
    for field in SCORE_FIELDS:
        try:
            score = float(row[field])
        except ValueError as exc:
            raise RowValidationError(f"row {row_number}: {field} must be numeric") from exc
        if not 0 <= score <= 100:
            raise RowValidationError(
                f"row {row_number}: {field} must be between 0 and 100"
            )
        values[field] = score
    values["overall_score"] = calculate_overall_score(
        *(values[field] for field in SCORE_FIELDS)
    )
    values["source_type"] = "manual_research"
    values["is_sample"] = False
    return values


def read_and_validate(path: Path) -> tuple[list[dict], list[str]]:
    valid: list[dict] = []
    errors: list[str] = []
    seen_urls: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        missing = [field for field in REQUIRED_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            return [], [f"missing required columns: {', '.join(missing)}"]
        for row_number, row in enumerate(reader, start=2):
            try:
                values = validate_row(row, row_number)
                url_key = values["linkedin_url"].rstrip("/").lower()
                if url_key in seen_urls:
                    raise RowValidationError(
                        f"row {row_number}: duplicate linkedin_url (first seen on row {seen_urls[url_key]})"
                    )
                seen_urls[url_key] = row_number
                valid.append(values)
            except RowValidationError as exc:
                errors.append(str(exc))
    return valid, errors


def import_influencers(path: Path = RESEARCH_FILE, session_factory=SessionLocal) -> ImportSummary:
    rows, validation_errors = read_and_validate(path)
    summary = ImportSummary(errors=len(validation_errors))
    for error in validation_errors:
        print(f"{path} {error}")
    init_db()
    summary.skipped = len(validation_errors)
    with session_factory.begin() as session:
        existing = {
            influencer.linkedin_url.rstrip("/").lower(): influencer
            for influencer in session.scalars(select(Influencer)).all()
        }
        for values in rows:
            url_key = values["linkedin_url"].rstrip("/").lower()
            influencer = existing.get(url_key)
            if influencer is None:
                influencer = Influencer(**values)
                session.add(influencer)
                existing[url_key] = influencer
                summary.created += 1
            elif all(getattr(influencer, field) == value for field, value in values.items()):
                summary.skipped += 1
            else:
                for field, value in values.items():
                    setattr(influencer, field, value)
                summary.updated += 1
    return summary


def print_summary(summary: ImportSummary) -> None:
    print("Influencer Import")
    print(f"Created: {summary.created}")
    print(f"Updated: {summary.updated}")
    print(f"Skipped: {summary.skipped}")
    print(f"Errors: {summary.errors}")


if __name__ == "__main__":
    result = import_influencers()
    print_summary(result)
