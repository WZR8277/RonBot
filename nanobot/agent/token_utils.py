"""Token/character conversion for context budgeting (no tokenizer dependency)."""

# Conservative: mixed Chinese/English ~2 chars per token. Use for capping content.
CHARS_PER_TOKEN = 2


def tokens_to_chars(tokens: int) -> int:
    """Conservative estimate: chars that fit in given token budget."""
    return max(0, int(tokens * CHARS_PER_TOKEN))


def chars_to_tokens_estimate(chars: int) -> int:
    """Conservative estimate: token count for given char count."""
    return max(0, (chars + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)
