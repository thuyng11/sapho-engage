from app.models import Post


def generate_placeholder_response(
    post: Post, goal: str, tone: str, length: str, custom_instruction: str | None
) -> str:
    text = (
        f"[Demo response — {goal} / {tone} / {length}]\n\n"
        f"This is a placeholder response for post #{post.id} by {post.influencer.name}. "
        "It validates the engagement workflow; no AI or external service was used.\n\n"
        "A future version will use the selected post and Sapho Bio brand instructions "
        "to generate a context-aware response."
    )
    if custom_instruction:
        text += f"\n\nCustom instructions received (not executed): {custom_instruction}"
    return text
