from types import SimpleNamespace

import pytest

from murmur import config, hotkey


class FakeHotKey:
    parsed = None
    instance = None

    @classmethod
    def parse(cls, value: str):
        cls.parsed = value
        return {"parsed-hotkey"}

    def __init__(self, keys, on_activate):
        self.keys = keys
        self.on_activate = on_activate
        self.press = lambda key: None
        self.release = lambda key: None
        FakeHotKey.instance = self


class FakeListener:
    def __init__(self, *, on_press, on_release, trusted=True):
        self.on_press = on_press
        self.on_release = on_release
        self.IS_TRUSTED = trusted
        self.started = False
        self.waited = False
        self.stopped = False

    def start(self):
        self.started = True

    def wait(self):
        self.waited = True

    def stop(self):
        self.stopped = True


def install_fake_keyboard(monkeypatch, *, trusted=True):
    listeners = []

    def listener_factory(**kwargs):
        listener = FakeListener(**kwargs, trusted=trusted)
        listeners.append(listener)
        return listener

    monkeypatch.setattr(
        hotkey,
        "keyboard",
        SimpleNamespace(HotKey=FakeHotKey, Listener=listener_factory),
    )
    return listeners


def test_starts_the_configured_global_hotkey(monkeypatch) -> None:
    listeners = install_fake_keyboard(monkeypatch)
    activate = lambda: None

    listener = hotkey.start_hotkey_listener(activate)

    assert FakeHotKey.parsed == config.HOTKEY == "<cmd>+<alt>+<shift>+m"
    assert FakeHotKey.instance.keys == {"parsed-hotkey"}
    assert FakeHotKey.instance.on_activate is activate
    assert listener is listeners[0]
    assert listener.started and listener.waited
    assert listener.on_press == FakeHotKey.instance.press
    assert listener.on_release == FakeHotKey.instance.release


def test_untrusted_listener_is_stopped_with_clear_permission_steps(monkeypatch) -> None:
    listeners = install_fake_keyboard(monkeypatch, trusted=False)

    with pytest.raises(RuntimeError, match="Input Monitoring and Accessibility"):
        hotkey.start_hotkey_listener(lambda: None)

    assert listeners[0].stopped
