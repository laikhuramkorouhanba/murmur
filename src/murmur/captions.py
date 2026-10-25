"""A big floating caption that shows what Murmur is saying, for a room or a projector.

It's for the people watching, not for the developer: the developer hears the voice.
So the window never takes focus (VoiceOver stays where it was), is hidden from
VoiceOver (so nothing is announced twice), and lets every click pass through.

Speech happens on background threads, but macOS only lets the main thread touch
windows, so every change is handed to the main thread first.
"""

from collections.abc import Callable

from murmur import settings


CLEAR_AFTER_SECONDS = 3.0  # how long a caption stays up after Murmur stops talking
MAX_LINES = 3

# Big text for short lines, a bit smaller for long ones, so most lines fit in three rows.
_FONT_SIZES = ((90, 48), (160, 38), (None, 30))


def font_size_for(text: str) -> int:
    """Pick a font size: 48 pt for short lines, smaller as the text gets longer."""
    for longest, size in _FONT_SIZES:
        if longest is None or len(text) <= longest:
            return size
    return _FONT_SIZES[-1][1]


def enabled() -> bool:
    return bool(settings.get("captions"))


class CaptionWindow:
    """The actual macOS panel. Only ever used on the main thread."""

    def __init__(self) -> None:
        import AppKit

        self._appkit = AppKit
        screen = AppKit.NSScreen.mainScreen().visibleFrame()
        width = screen.size.width * 0.8
        height = 220
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
        label.setMaximumNumberOfLines_(MAX_LINES)
        label.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        box.addSubview_(label)

        # Hidden from VoiceOver: the developer already hears these words.
        for thing in (panel, box, label):
            try:
                thing.setAccessibilityElement_(False)
            except AttributeError:
                pass

        self._panel = panel
        self._label = label

    def show(self, text: str) -> None:
        appkit = self._appkit
        self._label.setFont_(appkit.NSFont.systemFontOfSize_weight_(font_size_for(text), appkit.NSFontWeightSemibold))
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
    ) -> None:
        self._on_main = on_main
        self._on_main_later = on_main_later
        self._make_window = make_window
        self._window: CaptionWindow | None = None
        # Goes up with every change, so an old "clear in 3 s" can't wipe a newer caption.
        self._generation = 0

    def heard(self, text: str | None) -> None:
        """The voice.on_speech listener. Called from any thread."""
        self._on_main(lambda: self._update(text))

    def _update(self, text: str | None) -> None:
        self._generation += 1
        if text is None:
            generation = self._generation
            self._on_main_later(CLEAR_AFTER_SECONDS, lambda: self._clear_if_still(generation))
            return
        if not enabled():
            return
        if self._window is None:
            self._window = self._make_window()
        self._window.show(text)

    def _clear_if_still(self, generation: int) -> None:
        if generation == self._generation and self._window is not None:
            self._window.hide()

    def set_enabled(self, on: bool) -> None:
        """Turn captions on or off (the menu toggle, and Demo mode later). Saved in settings."""
        settings.set("captions", on)
        if not on:
            self._on_main(self._hide_now)

    def _hide_now(self) -> None:
        self._generation += 1
        if self._window is not None:
            self._window.hide()


CAPTIONS = Captions()
