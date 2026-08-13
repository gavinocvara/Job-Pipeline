"""
Thin wrapper around the Anthropic API so every agent calls the model
the same way and JSON-mode parsing is handled in one place.
"""
import json
import re
import anthropic
import config


_client = None


def client():
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def ask(system: str, user: str, max_tokens: int = 3000) -> str:
    """Plain text call."""
    resp = client().messages.create(
        model=config.MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def ask_json(system: str, user: str, max_tokens: int = 3000) -> dict:
    """Call the model and parse a JSON object out of the response, stripping
    stray markdown fences if the model adds them despite instructions."""
    system = system + "\n\nRespond ONLY with valid JSON. No prose, no markdown fences."
    raw = ask(system, user, max_tokens=max_tokens)
    cleaned = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Last resort: grab the largest {...} block
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
