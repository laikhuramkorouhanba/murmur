"""Things that only happen inside the built Murmur.app."""

import sys

from murmur import app, config, hotkey, packaged


def test_tool_folders_are_added_after_the_short_app_path() -> None:
    environ = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}

    packaged.add_tool_paths(environ)

    parts = environ["PATH"].split(":")
    assert parts[:4] == ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    assert "/usr/local/bin" in parts  # where Homebrew puts codex on an Intel Mac
    packaged.add_tool_paths(environ)
    assert environ["PATH"].count("/usr/local/bin") == 1  # never added twice


def test_output_goes_to_the_log_line_by_line(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "Logs" / "murmur.log"
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)

    packaged.send_output_to_log(log_path)
    print("hello from the app")
    sys.stderr.write("a warning\n")
    log = sys.stdout
    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    log.close()

    text = log_path.read_text()
    assert "===== Murmur started" in text
    assert "hello from the app" in text and "a warning" in text


def test_a_big_log_is_set_aside(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "murmur.log"
    log_path.write_text("x" * 20)
    monkeypatch.setattr(packaged, "LOG_MAX_BYTES", 10)
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)

    packaged.send_output_to_log(log_path)
    sys.stdout.close()

    assert (tmp_path / "murmur.old.log").read_text() == "x" * 20


def test_prepare_does_nothing_when_developing(monkeypatch) -> None:
    monkeypatch.setattr(packaged, "add_tool_paths", lambda: (_ for _ in ()).throw(AssertionError("no")))

    packaged.prepare()  # not frozen, so nothing happens


def test_the_built_app_names_itself_in_permission_hints(monkeypatch) -> None:
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)

    assert hotkey.launching_app_name() == "Murmur"


def test_show_log_opens_textedit_only_when_there_is_a_log(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(app.subprocess, "run", lambda command, check: calls.append(command))
    monkeypatch.setattr(packaged, "LOG_PATH", tmp_path / "murmur.log")

    assert not app.open_log()
    (tmp_path / "murmur.log").write_text("hi\n")
    assert app.open_log()
    assert calls == [["open", "-e", str(tmp_path / "murmur.log")]]
