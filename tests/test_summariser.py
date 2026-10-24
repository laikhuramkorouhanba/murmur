from types import SimpleNamespace

from murmur import config
from murmur.decision import fallback_speech_summary
from murmur.summariser import SUMMARY_INSTRUCTIONS, summarise


class FakeCompletions:
    def __init__(self, content: str | None = "The test failed.", error: Exception | None = None):
        self.content = content
        self.error = error
        self.arguments = None

    def create(self, **arguments):
        self.arguments = arguments
        if self.error:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeClient:
    def __init__(self, completions: FakeCompletions):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=completions.create)
        )


def sample_failure() -> str:
    return (
        "FAILED demo/failing_check.py::test_total_includes_the_tip - assert 18.0 == 19.8\n"
        "E       assert 18.0 == 19.8\n"
        "demo/failing_check.py:6: AssertionError"
    )


def test_uses_configured_model_prompt_and_limits(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    completions = FakeCompletions("The command failed.")
    client = FakeClient(completions)

    result = summarise("short output", client=client)

    assert result == "The command failed."
    assert completions.arguments["model"] == "gpt-4o-mini"
    assert completions.arguments["temperature"] == 0
    assert completions.arguments["max_tokens"] == 120
    assert completions.arguments["timeout"] == 8
    assert completions.arguments["messages"][0]["content"] == SUMMARY_INSTRUCTIONS
    assert completions.arguments["messages"][1]["content"] == "short output"


def test_failure_summary_says_expected_and_actual_without_raw_path(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    client = FakeClient(FakeCompletions("The test failed."))

    result = summarise(sample_failure(), client=client)

    assert result == (
        "One test failed in failing check dot py, line 6: "
        "it expected 19.8 but got 18."
    )


def test_model_location_is_normalized_and_not_repeated(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    original = sample_failure()
    client = FakeClient(FakeCompletions("The test failed at demo/failing_check.py:6."))

    result = summarise(original, client=client)

    assert result == (
        "One test failed in failing check dot py, line 6: "
        "it expected 19.8 but got 18."
    )
    assert result.count("failing check dot py, line 6") == 1
    assert "demo/failing_check.py:6" not in result


def test_api_error_returns_natural_error_fallback(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    original = sample_failure()
    client = FakeClient(FakeCompletions(error=TimeoutError("offline")))

    assert summarise(original, client=client) == fallback_speech_summary(original)


def test_missing_key_skips_api_and_returns_natural_fallback(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    original = sample_failure()

    assert summarise(original) == fallback_speech_summary(original)


def test_empty_api_response_returns_fallback(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    original = sample_failure()
    client = FakeClient(FakeCompletions("  "))

    assert summarise(original, client=client) == fallback_speech_summary(original)
