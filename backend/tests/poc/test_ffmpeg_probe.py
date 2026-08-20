from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


def _load_ffmpeg_probe():
    path = Path(__file__).parents[3] / "scripts" / "poc" / "ffmpeg_probe.py"
    spec = importlib.util.spec_from_file_location("ffmpeg_probe_for_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF....WAVEfmt ")
    return path


class RecordingSubprocess:
    def __init__(self, pass1_stderr: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.pass1_stderr = pass1_stderr or (
            "noise before\n"
            + json.dumps(
                {
                    "input_i": "-19.01",
                    "input_tp": "-2.33",
                    "input_lra": "8.40",
                    "input_thresh": "-29.20",
                    "target_offset": "0.31",
                }
            )
            + "\nnoise after"
        )

    def run(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append({"argv": argv, "kwargs": kwargs})
        index = len(self.calls)
        if index == 1:
            return _Completed(0, "", self.pass1_stderr)
        if index == 2:
            Path(argv[-1]).write_bytes(b"mastered")
            return _Completed(0, "", "")
        return _Completed(
            0,
            json.dumps(
                {
                    "streams": [{"codec_name": "mp3", "sample_rate": "44100", "channels": 1}],
                    "format": {"duration": "12.34", "bit_rate": "128000"},
                }
            ),
            "",
        )


class _Completed:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_ffmpeg_probe_uses_safe_contained_concat_and_measured_loudnorm_pass2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_ffmpeg_probe()
    recorder = RecordingSubprocess()
    monkeypatch.setattr(module.subprocess, "run", recorder.run)
    source = _wav(tmp_path / "input" / "chapter-one.wav")
    output = tmp_path / "work" / "out.mp3"

    result = module.run_probe(wav_paths=[source], output_path=output, work_root=tmp_path)

    assert result["status"] == "ok"
    assert len(recorder.calls) == 3
    pass1_argv = recorder.calls[0]["argv"]
    pass2_argv = recorder.calls[1]["argv"]
    assert "-safe" in pass1_argv
    assert pass1_argv[pass1_argv.index("-safe") + 1] == "1"
    assert recorder.calls[0]["kwargs"]["shell"] is False
    assert "measured_I=-19.01" in " ".join(pass2_argv)
    assert "measured_TP=-2.33" in " ".join(pass2_argv)
    assert "measured_LRA=8.40" in " ".join(pass2_argv)
    assert "measured_thresh=-29.20" in " ".join(pass2_argv)
    assert "offset=0.31" in " ".join(pass2_argv)
    concat_path = Path(pass1_argv[pass1_argv.index("-i") + 1])
    assert concat_path.parent == tmp_path
    assert concat_path.read_text(encoding="utf-8").startswith("file 'input/")


def test_ffmpeg_probe_rejects_concat_unsafe_relative_path_without_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_ffmpeg_probe()
    recorder = RecordingSubprocess()
    monkeypatch.setattr(module.subprocess, "run", recorder.run)
    source = _wav(tmp_path / "input" / "chapter one.wav")

    result = module.run_probe(wav_paths=[source], output_path=tmp_path / "out.mp3", work_root=tmp_path)

    assert result["status"] == "error"
    assert result["error_code"] == "INPUT_UNSAFE_PATH"
    assert recorder.calls == []


@pytest.mark.parametrize(
    "relative",
    [
        "../escape.wav",
        "http://example.test/a.wav",
        "file://local.wav",
    ],
)
def test_ffmpeg_probe_rejects_escape_protocol_and_outside_paths_without_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    module = _load_ffmpeg_probe()
    recorder = RecordingSubprocess()
    monkeypatch.setattr(module.subprocess, "run", recorder.run)
    path = Path(relative)
    if not relative.startswith("http") and not relative.startswith("file:"):
        path = tmp_path / "input" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF....WAVEfmt ")

    result = module.run_probe(wav_paths=[path], output_path=tmp_path / "out.mp3", work_root=tmp_path)

    assert result["status"] == "error"
    assert result["error_code"] == "INPUT_UNSAFE_PATH"
    assert recorder.calls == []
    assert str(tmp_path) not in str(result)


def test_ffmpeg_probe_rejects_symlink_escape_without_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_ffmpeg_probe()
    recorder = RecordingSubprocess()
    monkeypatch.setattr(module.subprocess, "run", recorder.run)
    outside = tmp_path.parent / "outside.wav"
    outside.write_bytes(b"RIFF....WAVEfmt ")
    link = tmp_path / "link.wav"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    result = module.run_probe(wav_paths=[link], output_path=tmp_path / "out.mp3", work_root=tmp_path)

    assert result["status"] == "error"
    assert result["error_code"] == "INPUT_UNSAFE_PATH"
    assert recorder.calls == []


def test_ffmpeg_probe_rejects_malformed_loudnorm_json_before_pass2_or_ffprobe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_ffmpeg_probe()
    recorder = RecordingSubprocess(pass1_stderr='{"input_i":"nan"}')
    monkeypatch.setattr(module.subprocess, "run", recorder.run)
    source = _wav(tmp_path / "in.wav")

    result = module.run_probe(wav_paths=[source], output_path=tmp_path / "out.mp3", work_root=tmp_path)

    assert result["status"] == "error"
    assert result["error_code"] == "FFMPEG_FAILED"
    assert result["redacted_error"] == "loudnorm_metrics_invalid"
    assert len(recorder.calls) == 1
