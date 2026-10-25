"""The caption window's logic, with a pretend window and a pretend main thread."""

from murmur import app, captions, settings


class FakeWindow:
    chars_per_line = 40

    def __init__(self) -> None:
        self.text: str | None = None
        self.visible = False
        self.history: list[str] = []

    def show(self, text: str) -> None:
        self.text, self.visible = text, True
        self.history.append(text)

    def hide(self) -> None:
        self.visible = False


class Harness:
    """Runs "main thread" work straight away, and keeps "later" work until we say so."""

    def __init__(self) -> None:
        self.windows: list[FakeWindow] = []
        self.later: list[tuple[float, object]] = []
        self.captions = captions.Captions(
            on_main=lambda work: work(),
            on_main_later=lambda seconds, work: self.later.append((seconds, work)),
            make_window=self._make,
            estimate=lambda text: 6.0,  # pretend every line takes 6 s to say
        )

    def _make(self) -> FakeWindow:
        self.windows.append(FakeWindow())
        return self.windows[-1]

    def run_later(self) -> None:
        """Run everything that was scheduled, soonest first."""
        jobs, self.later = sorted(self.later, key=lambda job: job[0]), []
        for _seconds, work in jobs:
            work()


def test_captions_are_off_by_default_and_no_window_is_made() -> None:
    harness = Harness()

    harness.captions.heard("Two tests failed.")

    assert harness.windows == []


def test_caption_shows_the_exact_words_then_clears_three_seconds_after() -> None:
    settings.set("captions", True)
    harness = Harness()

    harness.captions.heard("Two tests failed in shop.py.")
    window = harness.windows[0]
    assert window.text == "Two tests failed in shop.py." and window.visible

    harness.captions.heard(None)
    assert window.visible  # still up while people read it
    assert harness.later[0][0] == 3.0
    harness.run_later()
    assert not window.visible


def test_an_old_clear_never_wipes_a_newer_caption() -> None:
    settings.set("captions", True)
    harness = Harness()

    harness.captions.heard("First line.")
    harness.captions.heard(None)
    harness.captions.heard("Second line.")  # starts before the first one's 3 s are up
    harness.run_later()

    assert harness.windows[0].visible and harness.windows[0].text == "Second line."
    assert len(harness.windows) == 1  # one window, reused


def test_turning_captions_off_hides_the_window_straight_away() -> None:
    settings.set("captions", True)
    harness = Harness()
    harness.captions.heard("Showing.")

    harness.captions.set_enabled(False)

    assert not harness.windows[0].visible
    assert settings.get("captions") is False


LONG = "Failed. shop demo check dot py, line 9. Expected 21.60, got 18.40. Undo last fix is in the menu."


def test_long_text_is_split_into_two_line_pieces_and_nothing_is_cut() -> None:
    pieces = captions.caption_pieces(LONG, 20)

    assert all(len(piece.split("\n")) <= 2 for piece in pieces)
    assert all(len(line) <= 20 for piece in pieces for line in piece.split("\n"))
    assert " ".join(" ".join(piece.split("\n")) for piece in pieces) == LONG  # every word is there


def test_pieces_are_timed_across_the_speech() -> None:
    settings.set("captions", True)
    harness = Harness()

    harness.captions.heard(LONG)
    window = harness.windows[0]
    pieces = captions.caption_pieces(LONG, FakeWindow.chars_per_line)
    assert window.text == pieces[0]
    times = [seconds for seconds, _work in harness.later]
    assert len(times) == len(pieces) - 1
    assert times == sorted(times) and 0 < times[0] and times[-1] < 6.0

    harness.run_later()
    assert window.history == pieces


def test_if_speech_ends_early_the_rest_is_still_shown_then_cleared() -> None:
    settings.set("captions", True)
    harness = Harness()

    harness.captions.heard(LONG)
    harness.later = []  # pretend no timer fired before the voice stopped
    harness.captions.heard(None)
    harness.run_later()

    window = harness.windows[0]
    assert window.history == captions.caption_pieces(LONG, FakeWindow.chars_per_line)
    assert not window.visible


def test_menu_toggle_saves_and_says_on_or_off(monkeypatch) -> None:
    monkeypatch.setattr(captions, "CAPTIONS", captions.Captions(on_main=lambda work: work()))
    menu_app = app.MurmurApp()
    assert menu_app.captions_item.state == 0
    assert str(menu_app.captions_item._menuitem.accessibilityLabel()).endswith("Off")

    menu_app.toggle_captions(None)

    assert settings.get("captions") is True
    assert menu_app.captions_item.state == 1
    assert str(menu_app.captions_item._menuitem.accessibilityLabel()).endswith("On")
