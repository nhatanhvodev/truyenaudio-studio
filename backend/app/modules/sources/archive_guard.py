from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import zipfile


MAX_ARCHIVE_FILE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ZIP_ENTRIES = 10_000
MAX_COMPRESSION_RATIO = 100

_BLOCKED_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".jar",
    ".js",
    ".jse",
    ".msi",
    ".ps1",
    ".scr",
    ".sh",
    ".vbs",
    ".wsf",
}
_CHINESE_HEADING_RE = re.compile(r"^\s*(第\s*([0-9零〇一二两三四五六七八九十百千万]+)\s*[章节回卷部][^\n]*)")
_VIET_HEADING_RE = re.compile(r"^\s*((?:Chuong|Chương)\s+([0-9]+)\b[^\n]*)", re.IGNORECASE)
_NATURAL_PART_RE = re.compile(r"(\d+)")


class InputArchiveUnsafe(ValueError):
    pass


class InputArchiveTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    file_size: int
    compressed_size: int


@dataclass(frozen=True)
class ArchiveInspection:
    path: Path
    entry_count: int
    total_uncompressed_bytes: int
    members: tuple[ArchiveMember, ...]


@dataclass(frozen=True)
class ImportCandidate:
    ordinal: int | None
    title: str | None
    text: str
    source_path: str
    warnings: tuple[str, ...] = ()


def inspect_archive(path: str | Path) -> ArchiveInspection:
    archive_path = Path(path)
    if archive_path.stat().st_size > MAX_ARCHIVE_FILE_BYTES:
        raise InputArchiveTooLarge("INPUT_ARCHIVE_FILE_TOO_LARGE")
    with archive_path.open("rb") as archive_file:
        magic = archive_file.read(4)
    if magic not in {b"PK\x03\x04", b"PK\x05\x06"}:
        raise InputArchiveUnsafe("INPUT_ARCHIVE_NOT_ZIP")

    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise InputArchiveTooLarge("INPUT_ARCHIVE_TOO_MANY_ENTRIES")

            members: list[ArchiveMember] = []
            total_uncompressed = 0
            for info in infos:
                _validate_member(info)
                if info.is_dir():
                    continue
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                    raise InputArchiveTooLarge("INPUT_ARCHIVE_UNCOMPRESSED_TOO_LARGE")
                if info.file_size > MAX_UNCOMPRESSED_BYTES:
                    raise InputArchiveTooLarge("INPUT_ARCHIVE_MEMBER_TOO_LARGE")
                if info.compress_size == 0 and info.file_size > 0:
                    raise InputArchiveTooLarge("INPUT_ARCHIVE_COMPRESSION_RATIO_TOO_HIGH")
                if info.compress_size > 0 and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                    raise InputArchiveTooLarge("INPUT_ARCHIVE_COMPRESSION_RATIO_TOO_HIGH")
                members.append(ArchiveMember(info.filename, info.file_size, info.compress_size))
    except zipfile.BadZipFile as exc:
        raise InputArchiveUnsafe("INPUT_ARCHIVE_INVALID_ZIP") from exc

    return ArchiveInspection(
        path=archive_path,
        entry_count=len(members),
        total_uncompressed_bytes=total_uncompressed,
        members=tuple(members),
    )


def split_chapter_sections(text: str, source_path: str) -> tuple[tuple[str, str], ...]:
    current_title: str | None = None
    current_lines: list[str] = []
    sections: list[tuple[str, str]] = []
    saw_heading = False

    for line in text.splitlines():
        heading = detect_heading(line)
        if heading and current_lines:
            sections.append((current_title or source_path, "\n".join(current_lines).strip()))
            current_lines = []
        if heading:
            saw_heading = True
            current_title = heading[1]
        current_lines.append(line)

    if current_lines or not saw_heading:
        sections.append((current_title or source_path, "\n".join(current_lines).strip()))
    return tuple(sections)


def build_import_candidates(sections: tuple[tuple[str, str], ...]) -> tuple[ImportCandidate, ...]:
    draft: list[ImportCandidate] = []
    ordinal_counts: dict[int, int] = {}

    for source_path, text in sections:
        heading = detect_heading(_first_non_empty_line(text))
        ordinal = heading[0] if heading else None
        title = heading[1] if heading else None
        warnings: list[str] = []
        if ordinal is None:
            warnings.append("ORDINAL_MISSING")
        else:
            ordinal_counts[ordinal] = ordinal_counts.get(ordinal, 0) + 1
        if not text.strip():
            warnings.append("EMPTY_CHAPTER")
        draft.append(
            ImportCandidate(
                ordinal=ordinal,
                title=title,
                text=text,
                source_path=source_path,
                warnings=tuple(warnings),
            )
        )

    candidates: list[ImportCandidate] = []
    for candidate in draft:
        warnings = list(candidate.warnings)
        if candidate.ordinal is not None and ordinal_counts[candidate.ordinal] > 1:
            warnings.append("DUPLICATE_ORDINAL")
        candidates.append(
            ImportCandidate(
                ordinal=candidate.ordinal,
                title=candidate.title,
                text=candidate.text,
                source_path=candidate.source_path,
                warnings=tuple(warnings),
            )
        )
    return tuple(candidates)


def detect_heading(line: str) -> tuple[int, str] | None:
    stripped = line.strip()
    if not stripped:
        return None
    viet = _VIET_HEADING_RE.match(stripped)
    if viet:
        return int(viet.group(2)), viet.group(1).strip()
    chinese = _CHINESE_HEADING_RE.match(stripped)
    if chinese:
        return _parse_chinese_ordinal(chinese.group(2)), chinese.group(1).strip()
    return None


def natural_key(value: str) -> tuple[object, ...]:
    return tuple(int(part) if part.isdigit() else part.lower() for part in _NATURAL_PART_RE.split(value))


def _validate_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    normalized = name.replace("\\", "/")
    windows = PureWindowsPath(name)
    if windows.drive or windows.is_absolute() or normalized.startswith("/"):
        raise InputArchiveUnsafe("INPUT_ARCHIVE_ABSOLUTE_MEMBER")
    parts = PurePosixPath(normalized.rstrip("/")).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise InputArchiveUnsafe("INPUT_ARCHIVE_TRAVERSAL_MEMBER")
    if info.flag_bits & 0x1:
        raise InputArchiveUnsafe("INPUT_ARCHIVE_ENCRYPTED_MEMBER")
    if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
        raise InputArchiveUnsafe("INPUT_ARCHIVE_SYMLINK_MEMBER")

    lower_name = normalized.lower()
    if lower_name.endswith("vbaproject.bin") or PurePosixPath(lower_name).suffix in _BLOCKED_EXTENSIONS:
        raise InputArchiveUnsafe("INPUT_ARCHIVE_ACTIVE_CONTENT")


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line
    return ""


def _parse_chinese_ordinal(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = 0
    current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in units:
            unit = units[char]
            total += (current or 1) * unit
            current = 0
    return total + current
