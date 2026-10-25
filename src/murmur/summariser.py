"""Short spoken summaries with natural error locations and a local fallback."""

from typing import Any

from murmur import config
from murmur.decision import (
    contains_error,
    fallback_speech_summary,
    speech_failure_summary,
)


SUMMARY_INSTRUCTIONS = """You summarise terminal output for a blind developer who will hear it, not read it.
Reply in one or two short sentences, plain spoken English, with no markdown. Start with the result itself, like "One test failed" or "The build worked"; never open with a bare "The command failed".
If it failed, say what failed and explain the actual cause. For an assertion like `assert actual == expected`, say what was expected and what was received.
Do not include file paths, line numbers, or copy an exact error line; Murmur will add the location naturally after your summary.
If tests ran, say how many passed and failed. Do not suggest fixes. Do not mention anything that is not in the output."""


def _fallback(text: str) -> str:
    if contains_error(text):
        return fallback_speech_summary(text)
    return "\n".join(line for line in text.splitlines()[-3:] if line.strip())


def summarise(text: str, client: Any | None = None) -> str:
    """Summarise output, or make a natural local summary if the API is unavailable."""
    if not config.OPENAI_API_KEY:
        return _fallback(text)

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
        if contains_error(text):
            return speech_failure_summary(text, summary.strip())
        return summary.strip()
    except Exception:
        return _fallback(text)
