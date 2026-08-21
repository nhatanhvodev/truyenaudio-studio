from __future__ import annotations

from pathlib import Path
import stat

from app.modules.sources.archive_guard import ImportCandidate, build_import_candidates, natural_key
from app.modules.sources.paste_txt import decode_txt


class InputPathUnsafe(ValueError):
    pass


def read_folder(root: str | Path, subfolder: str | Path | None = None) -> tuple[ImportCandidate, ...]:
    root_input = Path(root)
    if _is_reparse_or_symlink(root_input):
        raise InputPathUnsafe("INPUT_FOLDER_REPARSE_POINT")
    root_path = root_input.resolve()
    selected = _resolve_selected(root_path, Path(subfolder)) if subfolder is not None else root_path
    if not _is_relative_to(selected, root_path):
        raise InputPathUnsafe("INPUT_FOLDER_OUTSIDE_ROOT")
    if _is_reparse_or_symlink(selected):
        raise InputPathUnsafe("INPUT_FOLDER_REPARSE_POINT")

    text_files = _collect_txt_files(selected, root_path)
    sections = []
    for file_path in sorted(text_files, key=lambda path: natural_key(path.relative_to(root_path).as_posix())):
        payload = file_path.read_bytes()
        decoded = decode_txt(payload)
        sections.append((file_path.relative_to(root_path).as_posix(), decoded.text))
    return build_import_candidates(tuple(sections))


def _collect_txt_files(selected: Path, root_path: Path) -> list[Path]:
    files: list[Path] = []
    for entry in selected.iterdir():
        if entry.is_symlink() or _is_reparse_or_symlink(entry):
            raise InputPathUnsafe("INPUT_FOLDER_REPARSE_POINT")
        entry_path = entry.resolve()
        if not _is_relative_to(entry_path, root_path):
            raise InputPathUnsafe("INPUT_FOLDER_OUTSIDE_ROOT")
        if entry.is_file() and entry.suffix.lower() == ".txt":
            files.append(entry_path)
    return files


def _resolve_selected(root_path: Path, subfolder: Path) -> Path:
    if subfolder.is_absolute():
        raise InputPathUnsafe("INPUT_FOLDER_OUTSIDE_ROOT")
    current = root_path
    for part in subfolder.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise InputPathUnsafe("INPUT_FOLDER_OUTSIDE_ROOT")
        current = current / part
        if current.exists() and _is_reparse_or_symlink(current):
            raise InputPathUnsafe("INPUT_FOLDER_REPARSE_POINT")
    return current.resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        file_stat = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise InputPathUnsafe("INPUT_FOLDER_STAT_FAILED") from exc
    if stat.S_ISLNK(file_stat.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(file_stat, "st_file_attributes", 0) & reparse_flag)
