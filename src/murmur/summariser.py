"""Short terminal summaries with exact-error preservation and a local fallback."""

from typing import Any

from murmur import config
from murmur.decision import add_missing_exact_details, exact_fallback_text


SUMMARY_INSTRUCTIONS = """You summarise terminal output for a blind developer who will hear it, not read it.
Reply in one or two short sentences, plain spoken English, with no markdown. Say first whether the command succeeded or failed.
If it failed, give the file name, line number, and error message exactly as written in the output. Never paraphrase, shorten, or fix them.
If tests ran, say how many passed and failed. Do not suggest fixes. Do not mention anything that is not in the output."""


def summarise(text: str, client: Any | None = None) -> str:
    """Summarise output, or keep exact error details if the API is unavailable."""
    if not config.OPENAI_API_KEY:
        return exact_fallback_text(text)

    try:
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=config.OPENAI_API_KEY,
                timeout=config.SUMMARY_TIMEOUT_SECONDS,
            )

        response = client.chat.completions.create(
            model=config.SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": SUMMARY_INSTRUCTIONS},
                {"role": "user", "content": text[: config.SUMMARY_MAX_INPUT_CHARS]},
            ],
            temperature=config.SUMMARY_TEMPERATURE,
            max_tokens=config.SUMMARY_MAX_TOKENS,
            timeout=config.SUMMARY_TIMEOUT_SECONDS,
        )
        summary = response.choices[0].message.content
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("The summary response was empty.")
        return add_missing_exact_details(summary.strip(), text)
    except Exception:
        return exact_fallback_text(text)
