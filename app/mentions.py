import re

MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_-]+)")


def extract_mentions(text: str) -> list[str]:
    """Extract @mentioned names from text, lowercase, de-duplicated, order preserved."""
    seen: dict[str, None] = {}
    for match in MENTION_PATTERN.findall(text):
        seen.setdefault(match.lower(), None)
    return list(seen.keys())
