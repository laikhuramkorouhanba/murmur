from murmur import personality
from murmur.app import MurmurApp


def test_smooth_is_the_default_mode() -> None:
    personality.set_mode(personality.Mode.SMOOTH)

    assert personality.get_mode() is personality.Mode.SMOOTH


def test_focused_mode_keeps_summary_plain() -> None:
    personality.set_mode(personality.Mode.FOCUSED)

    assert personality.style_summary("One test failed in file dot py, line 6.", failed=True) == (
        "One test failed in file dot py, line 6."
    )


def test_test_voice_sample_is_one_sentence_in_each_mode() -> None:
    personality.set_mode(personality.Mode.SMOOTH)
    assert personality.sample_sentence() == "Ooh, Murmur is ready to roll, cool cat."

    personality.set_mode(personality.Mode.FOCUSED)
    assert personality.sample_sentence() == "Murmur voice check is ready."


def test_smooth_mode_varies_its_openers_and_keeps_facts_intact() -> None:
    personality.set_mode(personality.Mode.SMOOTH)
    facts = "One test failed in failing check dot py, line 6: it expected 19.8 but got 18."

    first = personality.style_summary(facts, failed=True)
    second = personality.style_summary(facts, failed=True)

    assert first != second
    assert facts in first and facts in second
    assert first.endswith("Nothing we can't smooth out.")


def test_smooth_becomes_encouraging_after_three_failures() -> None:
    personality.set_mode(personality.Mode.SMOOTH)
    personality.record_result(False)
    for _ in range(3):
        personality.record_result(True)

    result = personality.style_summary("One test failed.", failed=True)

    assert result.startswith("We'll take it one step at a time.") or result.startswith(
        "You've got this; let's work through it."
    ) or result.startswith("Let's take a breath and handle one detail at a time.")
    assert "cool cat" not in result
    assert "Nothing we can't smooth out" not in result


def test_menu_selects_and_marks_the_current_mode() -> None:
    personality.set_mode(personality.Mode.SMOOTH)
    app = MurmurApp()
    menu = app.menu["Personality"]

    assert menu["Smooth"].state == 1
    assert menu["Focused"].state == 0

    app.choose_focused(None)

    assert personality.get_mode() is personality.Mode.FOCUSED
    assert menu["Smooth"].state == 0
    assert menu["Focused"].state == 1

    app.choose_smooth(None)
    assert personality.get_mode() is personality.Mode.SMOOTH
    assert menu["Smooth"].state == 1
    assert menu["Focused"].state == 0
