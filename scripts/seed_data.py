import json
from datetime import datetime, timezone

from app.database import BASE_DIR, SessionLocal, init_db
from app.models import Influencer, Post


def seed_data() -> None:
    influencers = json.loads((BASE_DIR / "data/influencers.json").read_text(encoding="utf-8"))
    posts = json.loads((BASE_DIR / "data/posts.json").read_text(encoding="utf-8"))
    init_db()
    added_influencers = 0
    added_posts = 0

    # One transaction prevents partially imported data if a record is invalid.
    with SessionLocal.begin() as session:
        for record in influencers:
            if session.get(Influencer, record["id"]) is None:
                session.add(Influencer(**record))
                added_influencers += 1
        session.flush()

        for record in posts:
            if session.get(Post, record["id"]) is None:
                posted_at = datetime.fromisoformat(record["posted_at"].replace("Z", "+00:00"))
                if posted_at.tzinfo is None:
                    posted_at = posted_at.replace(tzinfo=timezone.utc)
                record["posted_at"] = posted_at.astimezone(timezone.utc).replace(tzinfo=None)
                session.add(Post(**record))
                added_posts += 1

    print(f"Added {added_influencers} SAMPLE influencers and {added_posts} SAMPLE posts.")
    print("Existing IDs were skipped; existing data was preserved.")


if __name__ == "__main__":
    seed_data()
