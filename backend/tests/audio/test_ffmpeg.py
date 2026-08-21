from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.contracts import MasterRequest
from app.providers.ffmpeg_audio import FFmpegAudioProcessor


@pytest.mark.asyncio
async def test_ffmpeg_master_uses_concat_list_two_pass_loudnorm_and_mp3_settings(
    tmp_path: Path,
) -> None:
    source_a = _wav(tmp_path / "segments" / "a.wav")
    source_b = _wav(tmp_path / "segments" / "b.wav")
    output = tmp_path / "masters" / "chapter.mp3"
    runner = RecordingRunner()
    processor = FFmpegAudioProcessor(runner=runner.run)

    result = await processor.master(
        MasterRequest(
            operation_id="master-1",
            ordered_segment_paths=(source_a, source_b),
            pause_after_ms=(250, 0),
            metadata={"title": "Chapter 1", "artist": "Truyen Audio Studio"},
        ),
        output,
    )

    assert len(runner.calls) == 5
    pass1 = next(
        call["argv"]
        for call in runner.calls
        if "-f" in call["argv"] and "concat" in call["argv"] and "-af" in call["argv"]
    )
    pass2 = [
        call["argv"]
        for call in runner.calls
        if "-f" in call["argv"] and "concat" in call["argv"] and "-b:a" in call["argv"]
    ][0]
    assert pass1[0] == "ffmpeg"
    assert "-f" in pass1 and pass1[pass1.index("-f") + 1] == "concat"
    concat_path = Path(pass1[pass1.index("-i") + 1])
    concat_text = concat_path.read_text(encoding="utf-8")
    assert "file " in concat_text
    assert str(source_a).replace("\\", "/") in concat_text
    assert str(source_b).replace("\\", "/") in concat_text
    assert "silence.wav" in concat_text
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in " ".join(pass1)
    pass2_text = " ".join(pass2)
    assert "measured_I=-18.10" in pass2_text
    assert "measured_TP=-2.40" in pass2_text
    assert "-ar 44100" in pass2_text
    assert "-ac 1" in pass2_text
    assert "-b:a 128k" in pass2_text
    assert "-write_id3v2 1" in pass2_text
    assert "-id3v2_version 3" in pass2_text
    assert result.codec == "mp3"
    assert result.sample_rate == 44_100
    assert result.channels == 1
    assert result.bitrate_kbps == 128
    assert result.integrated_lufs == pytest.approx(-16.1)
    assert result.true_peak_dbtp == pytest.approx(-1.6)
    assert result.sha256 == hashlib.sha256(output.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_ffmpeg_probe_requires_file_checksum_to_match(tmp_path: Path) -> None:
    output = tmp_path / "chapter.mp3"
    output.write_bytes(b"mp3-bytes")
    processor = FFmpegAudioProcessor(runner=RecordingRunner().run)

    with pytest.raises(ValueError, match="checksum"):
        await processor.probe(output, expected_sha256="0" * 64)


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.null_calls = 0

    def run(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append({"argv": argv, "kwargs": kwargs})
        if argv[0] == "ffmpeg" and "-f" in argv and "null" in argv:
            self.null_calls += 1
            input_i = "-18.10" if self.null_calls == 1 else "-16.10"
            input_tp = "-2.40" if self.null_calls == 1 else "-1.60"
            return Completed(
                0,
                "",
                "before\n"
                + json.dumps(
                    {
                        "input_i": input_i,
                        "input_tp": input_tp,
                        "input_lra": "7.20",
                        "input_thresh": "-28.20",
                        "target_offset": "0.20",
                    }
                )
                + "\nafter",
            )
        if argv[0] == "ffmpeg":
            Path(argv[-1]).write_bytes(b"mastered-mp3")
            return Completed(0, "", "")
        if argv[0] == "ffprobe":
            return Completed(
                0,
                json.dumps(
                    {
                        "streams": [
                            {
                                "codec_name": "mp3",
                                "sample_rate": "44100",
                                "channels": 1,
                                "bit_rate": "128000",
                            }
                        ],
                        "format": {"duration": "91.0", "bit_rate": "128000"},
                    }
                ),
                "",
            )
        raise AssertionError(argv)


class Completed:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF....WAVEfmt ")
    return path
