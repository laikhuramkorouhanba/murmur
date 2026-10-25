"""The Keychain helper, with `security` replaced by a fake so the real Keychain is never touched."""

import subprocess

import pytest

from murmur import keychain


class FakeSecurity:
    """Pretends to be the `security` tool, keeping one saved password in memory."""

    def __init__(self) -> None:
        self.saved: str | None = None
        self.calls: list[list[str]] = []
        self.stdin: list[str] = []

    def __call__(self, command, input=None, **_kwargs):
        self.calls.append(command)
        if command[:2] == ["security", "-i"]:
            self.stdin.append(input)
            self.saved = input.split(" -w ", 1)[1].strip()
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1] == "find-generic-password":
            if self.saved is None:
                return subprocess.CompletedProcess(command, 44, "", "not found")
            return subprocess.CompletedProcess(command, 0, self.saved + "\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def security(monkeypatch) -> FakeSecurity:
    fake = FakeSecurity()
    monkeypatch.setattr(keychain.subprocess, "run", fake)
    return fake


KEY = "sk-proj-abcdefghijklmnop_1234-XYZ"


def test_no_saved_key_reads_as_none(security) -> None:
    assert keychain.read_key() is None


def test_a_saved_key_is_read_back(security) -> None:
    keychain.save_key(f"  {KEY}\n")

    assert keychain.read_key() == KEY


def test_the_key_goes_in_through_stdin_never_the_command_line(security) -> None:
    keychain.save_key(KEY)

    assert all(KEY not in " ".join(command) for command in security.calls)
    assert KEY in security.stdin[0]


@pytest.mark.parametrize("bad", ["", "hello", "sk-short", 'sk-abcdefghijklmnop" ; rm -rf ~', "sk-abc def ghi jkl mno"])
def test_things_that_are_not_keys_are_refused(security, bad: str) -> None:
    with pytest.raises(ValueError, match="start with sk-"):
        keychain.save_key(bad)
    assert security.stdin == []


def test_a_save_the_keychain_did_not_keep_is_reported(security, monkeypatch) -> None:
    monkeypatch.setattr(keychain, "read_key", lambda: None)

    with pytest.raises(ValueError, match="didn't keep the key"):
        keychain.save_key(KEY)
