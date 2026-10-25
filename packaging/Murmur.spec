# PyInstaller recipe for Murmur.app. Build it with: ./build_app.sh
#
# Why PyInstaller and not py2app: py2app crashes on uv's Python, because zlib is
# built into that interpreter and py2app expects a separate file for it.
# PyInstaller handles it, and already knows how to bundle sounddevice's PortAudio
# and pynput's macOS keyboard code.

import tomllib
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT / "src")],
    # The place for pre-rendered audio (M7). It travels with the app.
    datas=[(str(ROOT / "src" / "murmur" / "audio"), "murmur/audio")],
    excludes=["tkinter", "pytest"],
)

# sounddevice also ships Windows copies of PortAudio. A Mac never needs them.
a.binaries = [item for item in a.binaries if not item[0].endswith(".dll")]
a.datas = [item for item in a.datas if not item[0].endswith(".dll")]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Murmur",
    console=False,  # no terminal window
)

coll = COLLECT(exe, a.binaries, a.datas, name="Murmur")

app = BUNDLE(
    coll,
    name="Murmur.app",
    bundle_identifier="com.korouhanba.murmur",
    version=VERSION,
    info_plist={
        "CFBundleName": "Murmur",
        "CFBundleDisplayName": "Murmur",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        # Menu bar only: no Dock icon and no app menu.
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "MIT License",
        # What macOS shows when it asks for each permission.
        "NSAppleEventsUsageDescription": (
            "Murmur reads the text in your Terminal window so it can speak what your last command printed."
        ),
        "NSMicrophoneUsageDescription": (
            "Murmur listens only while you ask a follow-up question, like “what went wrong”."
        ),
        "NSAccessibilityUsageDescription": (
            "Murmur notices its keyboard shortcuts, like Command Option Shift M, from any app."
        ),
    },
)
