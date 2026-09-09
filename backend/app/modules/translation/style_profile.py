"""Versioned translation style profiles (task C01).

A style profile is a named combination of genre + tone + optional custom
instruction for one source/target language pair. Profiles are immutable
revisions chained by ``supersedes_id`` exactly like glossary entries: any
semantic change inserts a new revision and a new canonical hash. Global
presets live in this module as configuration; editing a preset for a project
creates a project-scoped copy-on-write revision (contract C03).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import new_id
from app.db.models import Project, TranslationStyle


ALLOWED_GENRES = frozenset(
    {"general", "webnovel", "lightnovel", "ancient", "modern", "fantasy", "wuxia", "xianxia"}
)
ALLOWED_TONES = frozenset({"faithful", "natural", "literary"})
DEFAULT_LANGUAGES = ("zh-CN", "vi-VN")
DEFAULT_PROMPT_TEMPLATE_VERSION = "prompt-builder.v1"
MAX_INSTRUCTION_CHARS = 4000


@dataclass(frozen=True)
class StylePreset:
    key: str
    name: str
    genre: str
    tone: str
    user_instruction: str


# Curated defaults: config + revision history, never a quality badge.
_PRESET_INSTRUCTION_NATURAL = (
    "Dịch tự nhiên, mượt mà như văn nói tiếng Việt hiện đại; giữ nghĩa đầy đủ, "
    "bảo toàn số liệu, tên riêng và thuật ngữ khóa."
)
_PRESET_INSTRUCTION_LITERARY = (
    "Dịch theo phong cách văn học, trau chuốt câu văn, giữ không khí và giọng "
    "kể của nguyên tác; không thêm lời bình của dịch giả."
)

PRESETS: tuple[StylePreset, ...] = (
    StylePreset("sat-nghia", "Sát nghĩa", "general", "faithful", "Dịch sát nghĩa gốc, ưu tiên chính xác hơn trau chuốt."),
    StylePreset("tu-nhien", "Tự nhiên", "general", "natural", _PRESET_INSTRUCTION_NATURAL),
    StylePreset("van-hoc", "Văn học", "general", "literary", _PRESET_INSTRUCTION_LITERARY),
    StylePreset("web-novel", "Web Novel", "webnovel", "natural", _PRESET_INSTRUCTION_NATURAL),
    StylePreset("light-novel", "Light Novel", "lightnovel", "natural", _PRESET_INSTRUCTION_NATURAL),
    StylePreset("co-trang", "Cổ trang", "ancient", "literary", _PRESET_INSTRUCTION_LITERARY),
    StylePreset("hien-dai", "Hiện đại", "modern", "natural", _PRESET_INSTRUCTION_NATURAL),
    StylePreset("fantasy", "Fantasy", "fantasy", "literary", _PRESET_INSTRUCTION_LITERARY),
    StylePreset("wuxia", "Wuxia", "wuxia", "literary", _PRESET_INSTRUCTION_LITERARY),
    StylePreset("xianxia", "Xianxia", "xianxia", "literary", _PRESET_INSTRUCTION_LITERARY),
)

PRESETS_BY_KEY = {preset.key: preset for preset in PRESETS}


@dataclass(frozen=True)
class StyleCommand:
    name: str
    genre: str
    tone: str
    source_language: str = DEFAULT_LANGUAGES[0]
    target_language: str = DEFAULT_LANGUAGES[1]
    user_instruction: str = ""
    prompt_template_version: str = DEFAULT_PROMPT_TEMPLATE_VERSION


@dataclass(frozen=True)
class StyleView:
    id: str
    project_id: str | None
    revision_no: int
    supersedes_id: str | None
    name: str
    genre: str
    tone: str
    source_language: str
    target_language: str
    user_instruction: str
    prompt_template_version: str
    sha256: str


@dataclass(frozen=True)
class StyleSnapshot:
    style: StyleView
    sha256: str


class StyleProfileService:
    def __init__(self, session: Session, id_factory: Callable[[], str] = new_id) -> None:
        self.session = session
        self.id_factory = id_factory

    def upsert(self, project_id: str, command: StyleCommand) -> StyleSnapshot:
        """Create a new project style, or revise the active style of the same
        name (copy-on-write when the name matches a global preset)."""
        project = self.session.get(Project, project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        normalized = _validate_and_normalize(command)
        active = _active_style_for_name(self.session, project_id, normalized["name"])
        changed = active is None or _style_payload(active) != normalized
        if not changed:
            return StyleSnapshot(_style_view(active), style_sha256(_style_payload(active)))
        revision_no = (
            self.session.scalar(
                select(func.max(TranslationStyle.revision_no)).where(
                    TranslationStyle.project_id == project_id,
                    TranslationStyle.name == normalized["name"],
                )
            )
            or 0
        ) + 1
        row = TranslationStyle(
            id=self.id_factory(),
            project_id=project_id,
            revision_no=revision_no,
            supersedes_id=active.id if active is not None else None,
            **normalized,
        )
        self.session.add(row)
        self.session.flush()
        self.session.commit()
        return StyleSnapshot(_style_view(row), style_sha256(normalized))


def active_styles(session: Session, project_id: str) -> tuple[StyleView, ...]:
    return tuple(
        _style_view(entry)
        for entry in _active_entries(session, project_id)
    )


def active_style_by_id(session: Session, project_id: str, style_id: str) -> StyleView | None:
    for view in active_styles(session, project_id):
        if view.id == style_id:
            return view
    return None


def _active_entries(session: Session, project_id: str) -> tuple[TranslationStyle, ...]:
    superseded = (
        select(TranslationStyle.supersedes_id)
        .where(
            TranslationStyle.project_id == project_id,
            TranslationStyle.supersedes_id.is_not(None),
        )
        .subquery()
    )
    return tuple(
        session.scalars(
            select(TranslationStyle)
            .where(
                TranslationStyle.project_id == project_id,
                TranslationStyle.id.not_in(select(superseded.c.supersedes_id)),
            )
            .order_by(TranslationStyle.name, TranslationStyle.revision_no.desc(), TranslationStyle.id)
        ).all()
    )


def _active_style_for_name(
    session: Session, project_id: str, name: str
) -> TranslationStyle | None:
    superseded = (
        select(TranslationStyle.supersedes_id)
        .where(
            TranslationStyle.project_id == project_id,
            TranslationStyle.supersedes_id.is_not(None),
        )
        .subquery()
    )
    return session.scalar(
        select(TranslationStyle)
        .where(
            TranslationStyle.project_id == project_id,
            TranslationStyle.name == name,
            TranslationStyle.id.not_in(select(superseded.c.supersedes_id)),
        )
        .order_by(TranslationStyle.revision_no.desc(), TranslationStyle.id.desc())
    )


def style_sha256(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_and_normalize(command: StyleCommand) -> dict[str, object]:
    name = command.name.strip()
    if not name:
        raise ValueError("STYLE_NAME_REQUIRED")
    if len(name) > 255:
        raise ValueError("STYLE_NAME_TOO_LONG")
    genre = command.genre.strip()
    tone = command.tone.strip()
    if genre not in ALLOWED_GENRES:
        raise ValueError(f"STYLE_GENRE_INVALID:{genre}")
    if tone not in ALLOWED_TONES:
        raise ValueError(f"STYLE_TONE_INVALID:{tone}")
    source_language = command.source_language.strip()
    target_language = command.target_language.strip()
    if not source_language or not target_language:
        raise ValueError("STYLE_LANGUAGE_REQUIRED")
    instruction = command.user_instruction or ""
    if len(instruction) > MAX_INSTRUCTION_CHARS:
        raise ValueError("STYLE_INSTRUCTION_TOO_LONG")
    template = command.prompt_template_version.strip() or DEFAULT_PROMPT_TEMPLATE_VERSION
    return {
        "name": name,
        "genre": genre,
        "tone": tone,
        "source_language": source_language,
        "target_language": target_language,
        "user_instruction": instruction,
        "prompt_template_version": template,
    }


def _style_payload(row: TranslationStyle) -> dict[str, object]:
    return {
        "name": row.name,
        "genre": row.genre,
        "tone": row.tone,
        "source_language": row.source_language,
        "target_language": row.target_language,
        "user_instruction": row.user_instruction,
        "prompt_template_version": row.prompt_template_version,
    }


def _style_view(row: TranslationStyle) -> StyleView:
    payload = _style_payload(row)
    return StyleView(
        id=row.id,
        project_id=row.project_id,
        revision_no=row.revision_no,
        supersedes_id=row.supersedes_id,
        name=str(payload["name"]),
        genre=str(payload["genre"]),
        tone=str(payload["tone"]),
        source_language=str(payload["source_language"]),
        target_language=str(payload["target_language"]),
        user_instruction=str(payload["user_instruction"]),
        prompt_template_version=str(payload["prompt_template_version"]),
        sha256=style_sha256(payload),
    )
