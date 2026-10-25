from murmur import personality
from murmur import settings
from murmur.app import MurmurApp


def test_brief_is_the_default_style_and_friendly_is_saved() -> None:
    assert personality.get_mode() is personality.Mode.BRIEF

    personality.set_mode(personality.Mode.FRIENDLY)

    assert personality.get_mode() is personality.Mode.FRIENDLY
    assert settings.get("style") == "Friendly"


def test_the_old_smooth_setting_is_ignored() -> None:
    # Saved by older versions under "personality". Everyone starts on Brief now.
    settings.PATH.write_text('{"personality": "Smooth"}', encoding="utf-8")

    assert personality.get_mode() is personality.Mode.BRIEF


def test_personality_choice_is_loaded_from_saved_settings() -> None:
    settings.set("style", "Brief")

    assert personality.get_mode() is personality.Mode.BRIEF


def test_unknown_saved_style_falls_back_to_brief() -> None:
    settings.set("style", "Loud")

    assert personality.get_mode() is personality.Mode.BRIEF


def test_focused_mode_keeps_summary_plain() -> None:
    personality.set_mode(personality.Mode.BRIEF)

    assert personality.style_summary("One test failed in file dot py, line 6.", failed=True) == (
        "One test failed in file dot py, line 6."
    )


def test_test_voice_sample_is_one_sentence_in_each_mode() -> None:
    personality.set_mode(personality.Mode.FRIENDLY)
    assert personality.sample_sentence() == "Ooh, Murmur is ready to roll, cool cat."

    personality.set_mode(personality.Mode.BRIEF)
    assert personality.sample_sentence() == "Murmur voice check."


def test_smooth_mode_varies_its_openers_and_keeps_facts_intact() -> None:
    personality.set_mode(personality.Mode.FRIENDLY)
    facts = "One test failed in failing check dot py, line 6: it expected 19.8 but got 18."

    first = personality.style_summary(facts, failed=True)
    second = personality.style_summary(facts, failed=True)

    assert first != second
    assert facts in first and facts in second
    assert any(first.endswith(closer) for closer in personality._PLAYFUL_CLOSERS)


def test_smooth_becomes_encouraging_after_three_failures() -> None:
    personality.set_mode(personality.Mode.FRIENDLY)
    personality.record_result(False)
    for _ in range(3):
        personality.record_result(True)

    result = personality.style_summary("One test failed.", failed=True)

    assert result.startswith("We'll take it one step at a time.") or result.startswith(
        "You've got this; let's work through it."
    ) or result.startswith("Let's take a breath and handle one detail at a time.")
    assert "cool cat" not in result
    assert not any(closer in result for closer in personality._PLAYFUL_CLOSERS)
    assert any(result.endswith(closer) for closer in personality._ENCOURAGING_CLOSERS)


def _split(result: str, openers, closers) -> tuple[str, str]:
    opener = next(o for o in openers if result.startswith(o))
    closer = next(c for c in closers if result.endswith(c))
    return opener, closer


def test_smooth_never_repeats_an_opener_or_closer_twice_in_a_row() -> None:
    personality.set_mode(personality.Mode.FRIENDLY)
    personality.record_result(False)
    seen_openers, seen_closers = [], []

    for _ in range(60):
        result = personality.style_summary("One test failed.", failed=True)
        opener, closer = _split(result, personality._PLAYFUL_OPENERS, personality._PLAYFUL_CLOSERS)
        seen_openers.append(opener)
        seen_closers.append(closer)
        personality.record_result(False)

    assert all(a != b for a, b in zip(seen_closers, seen_closers[1:]))
    assert all(a != b for a, b in zip(seen_openers, seen_openers[1:]))
    # Real variety, not just two lines taking turns.
    assert len(set(seen_closers)) == len(personality._PLAYFUL_CLOSERS)
    assert len(set(seen_openers)) == len(personality._PLAYFUL_OPENERS)


def test_successes_get_a_varied_closer_too() -> None:
    personality.set_mode(personality.Mode.FRIENDLY)
    closers = []
    for _ in range(20):
        result = personality.style_summary("All 12 tests passed.", failed=False)
        _, closer = _split(result, personality._SUCCESS_OPENERS, personality._SUCCESS_CLOSERS)
        closers.append(closer)

    assert all(a != b for a, b in zip(closers, closers[1:]))


def test_long_summaries_skip_the_closer() -> None:
    personality.set_mode(personality.Mode.FRIENDLY)
    personality.record_result(False)
    facts = "Two tests failed. The first is in app dot py, line 3. The second is in cart dot py, line 9."

    result = personality.style_summary(facts, failed=True)

    assert result.endswith(facts)


def test_two_sentence_summaries_still_get_a_smooth_closer() -> None:
    # Found in the live test: a summary plus "The error is in …" sounded flat in Smooth.
    personality.set_mode(personality.Mode.FRIENDLY)
    personality.record_result(False)
    facts = "One test failed: the total was 18.40 instead of 21.60. The error is in shop dot py, line 9."

    result = personality.style_summary(facts, failed=True)

    assert facts in result
    assert any(result.endswith(closer) for closer in personality._PLAYFUL_CLOSERS)


def test_done_line_in_each_mode() -> None:
    personality.set_mode(personality.Mode.BRIEF)
    assert personality.done_line() == "Done."

    personality.set_mode(personality.Mode.FRIENDLY)
    lines = [personality.done_line() for _ in range(20)]
    assert all(line in personality._FRIENDLY_DONE_LINES for line in lines)
    assert all(a != b for a, b in zip(lines, lines[1:]))
    assert "All done, smooth as butter." in personality._FRIENDLY_DONE_LINES
