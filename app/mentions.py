import re

# \w matches Unicode letters (e.g. "í", "ç") by default for str patterns, so this already
# recognizes an accented first letter — it just can't span the space in a multi-word name
# like "Osvaldo Tibúrcio" (see resolve_mentions for that).
MENTION_PATTERN = re.compile(r"@(\w[\w-]*)", re.UNICODE)


def extract_mentions(text: str) -> list[str]:
    """Extract @mentioned tokens from text, lowercase, de-duplicated, order preserved.
    Only ever captures a single word after "@", so a multi-word agent name (e.g. "Osvaldo
    Tibúrcio") comes back truncated to its first word here. Fine where an exact agent match
    isn't needed (e.g. checking whether a message has any @mention at all) — use
    resolve_mentions when the mention needs to be matched against real agent names."""
    seen: dict[str, None] = {}
    for match in MENTION_PATTERN.findall(text):
        seen.setdefault(match.lower(), None)
    return list(seen.keys())


def resolve_mentions(text: str, known_names: list[str]) -> list[str]:
    """Extract @mentions from text, matched against a known set of names — which may
    contain spaces, e.g. "Osvaldo Tibúrcio" — plus the literal keyword "all". At each "@",
    the longest known name that the following text starts with wins (so "Osvaldo Tibúrcio"
    is preferred over a hypothetical shorter "Osvaldo" agent sharing the same prefix), and
    a match is rejected if the next character would extend it into a longer word (so
    "@Osvaldoo" doesn't match a known "Osvaldo"). Case-insensitive, de-duplicated,
    order-preserved."""
    candidates = sorted({name.lower() for name in known_names} | {"all"}, key=len, reverse=True)
    seen: dict[str, None] = {}
    for at_index, char in enumerate(text):
        if char != "@":
            continue
        rest = text[at_index + 1 :]
        rest_lower = rest.lower()
        for name in candidates:
            if not rest_lower.startswith(name):
                continue
            boundary_char = rest[len(name) : len(name) + 1]
            if boundary_char and (boundary_char.isalnum() or boundary_char == "_"):
                continue
            seen.setdefault(name, None)
            break
    return list(seen.keys())
