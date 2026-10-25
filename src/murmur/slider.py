"""The Speed slider that sits inside the Murmur menu.

It's a real macOS slider (NSSlider) in a menu item, so it works with the mouse,
and VoiceOver can adjust it too (it's an ordinary accessible slider, named
"Speed", that says its value as a percentage).

While you drag, the speed is saved straight away. When you let go (or stop
pressing VoiceOver's arrow keys for a moment), Murmur says a short sample at the
new speed.
"""

from collections.abc import Callable

import AppKit
import objc
from Foundation import NSObject

SAMPLE_DELAY_SECONDS = 0.4  # quiet time after the last change before the sample plays
WIDTH = 260
HEIGHT = 30


def value_words(position: float) -> str:
    """What VoiceOver says for the slider's value, like "50 percent"."""
    return f"{round(position * 100)} percent"


class _Target(NSObject):
    """Receives the slider's "value changed" messages and hands them to Python."""

    def initWithCallback_(self, callback):
        self = objc.super(_Target, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def changed_(self, sender) -> None:
        self._callback(float(sender.doubleValue()))


class SpeedSlider:
    """Builds the slider's view and calls on_change(position) and on_settled(position).

    on_change: every change, while dragging too. on_settled: once, after you let go.
    later(seconds, work) runs work on the main thread later (tests pass their own).
    """

    def __init__(
        self,
        position: float,
        on_change: Callable[[float], None],
        on_settled: Callable[[float], None],
        later: Callable[[float, Callable[[], None]], None] | None = None,
        mouse_down: Callable[[], bool] | None = None,
    ) -> None:
        self._on_change = on_change
        self._on_settled = on_settled
        self._later = later or _call_later
        self._mouse_down = mouse_down or (lambda: bool(AppKit.NSEvent.pressedMouseButtons() & 1))
        self._generation = 0

        self.view = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, WIDTH, HEIGHT))
        label = AppKit.NSTextField.labelWithString_("Speed")
        label.setFrame_(AppKit.NSMakeRect(20, 6, 52, 18))
        # Hidden from VoiceOver: the slider itself says "Speed". (VoiceOver reads a control's
        # "cell", so the cell is where these settings have to go.)
        label.cell().setAccessibilityElement_(False)
        self.view.addSubview_(label)

        self.slider = AppKit.NSSlider.sliderWithValue_minValue_maxValue_target_action_(
            position, 0.0, 1.0, None, None
        )
        self.slider.setFrame_(AppKit.NSMakeRect(76, 4, WIDTH - 96, 22))
        self.slider.setContinuous_(True)
        self._target = _Target.alloc().initWithCallback_(self._changed)
        self.slider.setTarget_(self._target)
        self.slider.setAction_("changed:")
        self._accessible = self.slider.cell()
        self._accessible.setAccessibilityLabel_("Speed")
        self._accessible.setAccessibilityValueDescription_(value_words(position))
        self.view.addSubview_(self.slider)

    def set_position(self, position: float) -> None:
        """Move the slider to match a speed changed somewhere else (the arrow keys)."""
        self.slider.setDoubleValue_(position)
        self._accessible.setAccessibilityValueDescription_(value_words(position))

    def _changed(self, position: float) -> None:
        self._accessible.setAccessibilityValueDescription_(value_words(position))
        self._on_change(position)
        self._generation += 1
        if self._mouse_down():
            return  # still dragging: the sample waits until you let go
        generation = self._generation
        self._later(SAMPLE_DELAY_SECONDS, lambda: self._settle_if_still(generation, position))

    def _settle_if_still(self, generation: int, position: float) -> None:
        if generation == self._generation:
            self._on_settled(position)


def _call_later(seconds: float, work: Callable[[], None]) -> None:
    from PyObjCTools import AppHelper

    AppHelper.callLater(seconds, work)
