"""Plain-assert checks for tagit's pure helpers. Run: python3 tests/test_tagit.py (or pytest)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import tagit  # noqa: E402


def test_guess_from_name():
    assert tagit.guess_from_name("/x/01 - Daft Punk - One More Time.mp3") == ("Daft Punk", "One More Time")
    assert tagit.guess_from_name("Artist – Song (Official Video).flac") == ("Artist", "Song")
    assert tagit.guess_from_name("just_a_title.m4a") == ("", "just a title")


def test_titles_and_artists():
    assert tagit.core_title("Song (feat. Someone) [Official Audio]") == "song"
    assert tagit.core_title("Song ft. Someone") == "song"
    assert tagit.artist_names("A & B feat. C") == ["A", "B", "C"]
    assert tagit.artist_score("Beyoncé", "Beyonce") == 1.0


def test_duration_score():
    assert tagit.duration_score(180, 182) == 1.0
    assert tagit.duration_score(180, 187) == 0.5
    assert tagit.duration_score(180, 200) == 0.0
    assert tagit.duration_score(None, 180) == 0.5


def config(text, env=None):
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
        handle.write(text)
    try:
        return tagit.load_send(handle.name, env or {})
    finally:
        os.unlink(handle.name)


def test_config():
    assert config("") is None
    assert config('[send]\ntarget = "me@box:/in/"\n') == {
        "host": "me@box", "folder": "/in", "name": "me@box", "backdate": 0, "note": ""}
    full = config('[send]\ntarget = "me@box:/in"\nname = "box"\nbackdate_minutes = 15\nnote = "hi"\n')
    assert (full["name"], full["backdate"], full["note"]) == ("box", 15, "hi")
    assert config('[send]\ntarget = "no-folder"\n') is None
    assert config('[send]\ntarget = "me@box:/in"\nbackdate_minutes = -1\n') is None
    assert config("", {"TAGIT_SEND_TARGET": "u@h:/d"})["host"] == "u@h"
    assert tagit.load_send("/nonexistent/config.toml", {}) is None


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
    print("ok")
