from __future__ import annotations

from pathlib import Path, PurePosixPath
import posixpath
import zipfile

from defusedxml import ElementTree

from app.modules.sources.archive_guard import (
    ImportCandidate,
    InputArchiveUnsafe,
    build_import_candidates,
    inspect_archive,
)


def read_epub(path: str | Path) -> tuple[ImportCandidate, ...]:
    archive_path = inspect_archive(path).path
    with zipfile.ZipFile(archive_path) as archive:
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        opf_path = _rootfile_path(container)
        opf = ElementTree.fromstring(archive.read(opf_path))
        manifest = _manifest_items(opf)
        spine_paths = _spine_paths(opf, manifest, opf_path)
        sections = tuple((item_path, _extract_xhtml_text(archive.read(item_path))) for item_path in spine_paths)
    return build_import_candidates(sections)


def _rootfile_path(container) -> str:
    for element in container.iter():
        if _local_name(element.tag) == "rootfile":
            path = element.attrib.get("full-path")
            if path:
                return path
    raise InputArchiveUnsafe("EPUB_ROOTFILE_MISSING")


def _manifest_items(opf) -> dict[str, str]:
    items: dict[str, str] = {}
    for element in opf.iter():
        if _local_name(element.tag) == "item":
            item_id = element.attrib.get("id")
            href = element.attrib.get("href")
            if item_id and href:
                items[item_id] = href
    return items


def _spine_paths(opf, manifest: dict[str, str], opf_path: str) -> tuple[str, ...]:
    base = PurePosixPath(opf_path).parent
    paths: list[str] = []
    for element in opf.iter():
        if _local_name(element.tag) != "itemref":
            continue
        item_id = element.attrib.get("idref")
        href = manifest.get(item_id or "")
        if not href:
            raise InputArchiveUnsafe("EPUB_SPINE_ITEM_MISSING")
        normalized = posixpath.normpath(str(base / href))
        if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
            raise InputArchiveUnsafe("EPUB_SPINE_PATH_UNSAFE")
        paths.append(normalized)
    return tuple(paths)


def _extract_xhtml_text(payload: bytes) -> str:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise InputArchiveUnsafe("EPUB_XHTML_INVALID") from exc
    for element in root.iter():
        if _local_name(element.tag) in {"script", "style"}:
            raise InputArchiveUnsafe("EPUB_ACTIVE_CONTENT")

    lines: list[str] = []
    for element in root.iter():
        if _local_name(element.tag) in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li"}:
            text = " ".join("".join(element.itertext()).split())
            if text:
                lines.append(text)
    return "\n".join(lines)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
