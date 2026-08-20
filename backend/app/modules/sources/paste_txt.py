from __future__ import annotations

from dataclasses import dataclass


MAX_TXT_BYTES = 52_428_800


@dataclass(frozen=True)
class DecodedText:
    text: str
    encoding: str


def decode_txt(payload: bytes) -> DecodedText:
    if len(payload) > MAX_TXT_BYTES:
        raise ValueError("INPUT_FILE_TOO_LARGE")
    if payload.startswith(b"\xef\xbb\xbf"):
        return DecodedText(payload.decode("utf-8-sig"), "utf-8-sig")
    if payload.startswith(b"\xff\xfe") or payload.startswith(b"\xfe\xff"):
        return DecodedText(payload.decode("utf-16"), "utf-16")
    if b"\x00" in payload[:128]:
        raise ValueError("INPUT_ENCODING_CONFIRMATION_REQUIRED")
    try:
        return DecodedText(payload.decode("utf-8"), "utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("INPUT_ENCODING_CONFIRMATION_REQUIRED") from exc
