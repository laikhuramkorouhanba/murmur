"""A big floating caption that shows what Murmur is saying, for a room or a projector.

It's for the people watching, not for the developer: the developer hears the voice.
So the window never takes focus (VoiceOver stays where it was), is hidden from
VoiceOver (so nothing is announced twice), and lets every click pass through.

Long lines are never cut off: the text is split into two-line pieces, and each
piece is shown in turn, timed to match how long the voice takes to say it.

Speech happens on background threads, but macOS only lets the main thread touch
windows, so every change is handed to the main thread first.
"""

import textwrap
from collections.abc import Callable

from murmur import settings


CLEAR_AFTER_SECONDS = 3.0  # how long a caption stays up after Murmur stops talking
PIECE_LINES = 2  # lines per piece
FONT_SIZE = 40
# If the voice finishes before every piece was shown (our timing guess was slow),
# the pieces left over still get this long each, so nobody misses the end.
LEFTOVER_PIECE_SECONDS = 1.2


def caption_pieces(text: str, chars_per_line: int, lines: int = PIECE_LINES) -> list[str]:
    """Split text into pieces of at most `lines` lines of `chars_per_line` characters."""
    wrapped = textwrap.wrap(" ".join(text.split()), width=max(10, chars_per_line), break_on_hyphens=False)
    return ["\n".join(wrapped[start : start + lines]) for start in range(0, len(wrapped), lines)] or [""]


def _estimate_seconds(text: str) -> float:
    from murmur import voice  # here, because voice is heavier and captions load first

    return voice.expected_seconds(text)


def enabled() -> bool:
    return bool(settings.get("captions"))


class CaptionWindow:
    """The actual macOS panel. Only ever used on the main thread."""

    def __init__(self) -> None:
        import AppKit

        self._appkit = AppKit
        screen = AppKit.NSScreen.mainScreen().visibleFrame()
        width = screen.size.width * 0.8
        font = AppKit.NSFont.systemFontOfSize_weight_(FONT_SIZE, AppKit.NSFontWeightSemibold)
        line_height = font.ascender() - font.descender() + font.leading()
        # Room for three lines, though pieces have two: a safety margin if a line wraps differently.
        height = int(line_height * (PIECE_LINES + 1) + 36)
        frame = AppKit.NSMakeRect(
            screen.origin.x + (screen.size.width - width) / 2,
            screen.origin.y + 60,  # near the bottom, like subtitles
            width,
            height,
        )
        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, AppKit.NSBackingStoreBuffered, False
        )
        panel.setLevel_(AppKit.NSStatusWindowLevel)  # above normal windows
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces  # every desktop
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary  # over full-screen apps too
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle  # never in ⌘` window switching
        )
        panel.setIgnoresMouseEvents_(True)  # clicks go straight through
        panel.setOpaque_(False)
        panel.setHasShadow_(True)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHidesOnDeactivate_(False)

        # A plain rounded box: near-black, so white text stands out on any projector.
        box = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, width, height))
        box.setBoxType_(AppKit.NSBoxCustom)
        box.setTitlePosition_(AppKit.NSNoTitle)
        box.setBorderWidth_(0)
        box.setCornerRadius_(18)
        box.setFillColor_(AppKit.NSColor.colorWithWhite_alpha_(0.05, 0.88))
        box.setContentViewMargins_(AppKit.NSMakeSize(0, 0))
        panel.setContentView_(box)

        label = AppKit.NSTextField.wrappingLabelWithString_("")
        label.setFrame_(AppKit.NSInsetRect(AppKit.NSMakeRect(0, 0, width, height), 28, 18))
        label.setAlignment_(AppKit.NSTextAlignmentCenter)
        label.setTextColor_(AppKit.NSColor.whiteColor())
        label.setFont_(font)
        label.setMaximumNumberOfLines_(0)  # never cut off with "…"
        label.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
        box.addSubview_(label)

        # Hidden from VoiceOver: the developer already hears these words.
        for thing in (panel, box, label):
            try:
                thing.setAccessibilityElement_(False)
            except AttributeError:
                pass

        self._panel = panel
        self._label = label
        # How many characters fit on one line, measured with this font, minus a little to be safe.
        sample = "the quick brown fox jumps over a lazy dog, 0123456789 shop.py line"
        sample_width = AppKit.NSString.stringWithString_(sample).sizeWithAttributes_({AppKit.NSFontAttributeName: font}).width
        self.chars_per_line = int((width - 56) / (sample_width / len(sample)) * 0.9)

    def show(self, text: str) -> None:
        self._label.setStringValue_(text)
        self._panel.orderFrontRegardless()  # shows it without making Murmur the active app

    def hide(self) -> None:
        self._panel.orderOut_(None)


def _on_main(work: Callable[[], None]) -> None:
    from PyObjCTools import AppHelper

    AppHelper.callAfter(work)


def _on_main_later(seconds: float, work: Callable[[], None]) -> None:
    from PyObjCTools import AppHelper

    AppHelper.callLater(seconds, work)


class Captions:
    """Listens to Murmur's speech and keeps the caption window in step with it."""

    def __init__(
        self,
        on_main: Callable[[Callable[[], None]], None] = _on_main,
        on_main_later: Callable[[float, Callable[[], None]], None] = _on_main_later,
        make_window: Callable[[], CaptionWindow] = CaptionWindow,
        estimate: Callable[[str], float] = _estimate_seconds,
    ) -> None:
        self._on_main = on_main
        self._on_main_later = on_main_later
        self._make_window = make_window
        self._estimate = estimate
        self._window: CaptionWindow | None = None
        # Goes up with every change, so an old timer can't show or wipe a newer caption.
        self._generation = 0
        self._pieces: list[str] = []
        self._shown = -1  # the last piece shown so far

    def heard(self, text: str | None) -> None:
        """The voice.on_speech listener. Called from any thread."""
        self._on_main(lambda: self._update(text))

    def _update(self, text: str | None) -> None:
        self._generation += 1
        generation = self._generation
        if text is None:
            self._speech_ended(generation)
            return
        if not enabled():
            self._pieces = []
            return
        if self._window is None:
            self._window = self._make_window()
        self._pieces = caption_pieces(text, self._window.chars_per_line)
        self._shown = -1
        self._show_piece(generation, 0)
        # Spread the other pieces over the time the voice needs, by how much text each has.
        seconds = self._estimate(text)
        total = sum(len(piece) for piece in self._pieces) or 1
        at = 0.0
        for index, piece in enumerate(self._pieces[:-1], start=1):
            at += seconds * len(piece) / total
            self._on_main_later(at, lambda index=index: self._show_piece(generation, index))

    def _show_piece(self, generation: int, index: int) -> None:
        if generation != self._generation or self._window is None or index >= len(self._pieces):
            return
        self._shown = index
        self._window.show(self._pieces[index])

    def _speech_ended(self, generation: int) -> None:
        # Any pieces the voice got through faster than we guessed are still shown, one by one.
        leftover = range(self._shown + 1, len(self._pieces))
        for step, index in enumerate(leftover, start=1):
            self._on_main_later(step * LEFTOVER_PIECE_SECONDS, lambda index=index: self._show_piece(generation, index))
        clear_at = len(leftover) * LEFTOVER_PIECE_SECONDS + CLEAR_AFTER_SECONDS
        self._on_main_later(clear_at, lambda: self._clear_if_still(generation))

    def _clear_if_still(self, generation: int) -> None:
        if generation == self._generation and self._window is not None:
            self._window.hide()

    def set_enabled(self, on: bool) -> None:
        """Turn captions on or off (the menu toggle, and Demo mode). Saved in settings."""
        settings.set("captions", on)
        if not on:
            self._on_main(self._hide_now)

    def _hide_now(self) -> None:
        self._generation += 1
        if self._window is not None:
            self._window.hide()


CAPTIONS = Captions()
