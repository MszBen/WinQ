"""Cue model, CueList and show-file tests (no Qt)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from winq.audio.voice import INFINITE
from winq.model import show_file
from winq.model.cue import INFINITE_REPEATS, AudioCue
from winq.model.cue_list import CueList
from winq.model.show_file import ShowFileError, load_show, save_show


def cue(name: str, number: str = "", **kw) -> AudioCue:
    return AudioCue(file_path=Path(f"C:/audio/{name}.wav"), number=number, name=name, **kw)


def test_infinite_constants_agree():
    assert INFINITE_REPEATS == INFINITE


def test_new_cue_defaults():
    c = AudioCue(file_path=Path("x.wav"))
    assert c.level_db == 0.0 and c.stop_fade == 1.0 and c.repeats == 0
    assert c.fade_in == 0.0 and c.end_fade == 0.0
    assert c.is_missing  # not probed yet
    assert len(c.id) == 32 and c.id != AudioCue(file_path=Path("x.wav")).id


def test_pass_duration_respects_trim_and_file_length():
    c = cue("a", start_time=2.0)
    assert c.pass_duration is None
    c.file_duration = 10.0
    assert c.pass_duration == 8.0
    c.end_time = 6.5
    assert c.pass_duration == 4.5
    c.end_time = 50.0  # beyond the file: clamp
    assert c.pass_duration == 8.0


@pytest.mark.parametrize(
    ("numbers", "expected"),
    [([], 1), (["1", "2", "3"], 4), (["1", "7", "3"], 8), (["2.5", "intro", ""], 3), (["x"], 1)],
)
def test_next_number(numbers, expected):
    assert CueList([cue(str(i), n) for i, n in enumerate(numbers)]).next_number() == expected


def test_cue_list_insert_remove_move():
    cl = CueList([cue("a"), cue("b"), cue("c")])
    cl.insert(1, [cue("x"), cue("y")])
    assert [c.name for c in cl] == ["a", "x", "y", "b", "c"]
    removed = cl.remove(1, 2)
    assert [c.name for c in removed] == ["x", "y"]
    cl.move(0, 2)
    assert [c.name for c in cl] == ["b", "c", "a"]
    cl.move(2, 0)
    assert [c.name for c in cl] == ["a", "b", "c"]
    assert cl.index_of(cl[1].id) == 1
    assert cl.index_of("nope") is None


def test_show_roundtrip_with_relative_paths(tmp_path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    cues = [
        AudioCue(file_path=audio_dir / "one.wav", number="1", name="One", level_db=-6.0),
        AudioCue(
            file_path=audio_dir / "two.mp3",
            number="2",
            name="Two",
            notes="loop me",
            start_time=1.5,
            end_time=9.25,
            fade_in=0.5,
            end_fade=2.0,
            stop_fade=3.0,
            repeats=INFINITE_REPEATS,
        ),
    ]
    path = tmp_path / "show.winq"
    save_show(path, cues)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["format_version"] == show_file.FORMAT_VERSION
    assert raw["cues"][0]["file"] == "audio/one.wav"  # relative, forward slashes

    loaded = load_show(path).cues
    assert loaded == cues  # dataclass equality ignores runtime-only fields
    assert all(c.file_path.is_absolute() for c in loaded)


def test_show_moves_with_its_folder(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    save_show(a / "show.winq", [AudioCue(file_path=a / "snd" / "x.wav")])
    b = tmp_path / "b"
    a.rename(b)
    assert load_show(b / "show.winq").cues[0].file_path == (b / "snd" / "x.wav").resolve()


def test_other_drive_saved_absolute(tmp_path, monkeypatch):
    def boom(*_a):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(show_file.os.path, "relpath", boom)
    save_show(tmp_path / "s.winq", [AudioCue(file_path=Path("D:/sfx/bang.wav"))])
    raw = json.loads((tmp_path / "s.winq").read_text(encoding="utf-8"))
    assert raw["cues"][0]["file"] == "D:/sfx/bang.wav"


def test_missing_fields_get_defaults(tmp_path):
    path = tmp_path / "old.winq"
    path.write_text(json.dumps({"cues": [{"file": "a.wav"}]}), encoding="utf-8")
    show = load_show(path)
    assert show.master_db == 0.0  # older files have no master level
    (c,) = show.cues
    assert c.stop_fade == 1.0 and c.level_db == 0.0 and c.end_time is None and c.id


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps([1, 2]),
        json.dumps({"format_version": 99, "cues": []}),
        json.dumps({"cues": [{"name": "no file"}]}),
        json.dumps({"cues": [{"file": "a.wav", "level_db": "loud"}]}),
    ],
)
def test_bad_show_files_raise(tmp_path, content):
    path = tmp_path / "bad.winq"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ShowFileError):
        load_show(path)


def test_save_leaves_no_temp_file(tmp_path):
    save_show(tmp_path / "s.winq", [])
    assert [p.name for p in tmp_path.iterdir()] == ["s.winq"]
