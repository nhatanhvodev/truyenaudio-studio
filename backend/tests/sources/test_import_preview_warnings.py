"""U03: import preview must flag duplicates and name the offending file/chapter.

The preview is what the user reviews before confirming, so a bad import has to be
attributable: every warning carries the `sourcePath` of the section it belongs to,
and a duplicated chapter number is flagged on **all** sections that share it (not
only the second one, which would make the first look innocent).
"""

from __future__ import annotations

from app.modules.sources.archive_guard import build_import_candidates


def test_duplicate_ordinals_are_flagged_on_every_section_with_its_file() -> None:
    candidates = build_import_candidates(
        (
            ("book/chapter-01.txt", "第1章 Mở đầu\nNội dung một."),
            ("book/chapter-01-copy.txt", "第1章 Mở đầu\nNội dung trùng số."),
            ("book/chapter-02.txt", "第2章 Tiếp theo\nNội dung hai."),
        )
    )

    assert len(candidates) == 3
    assert [candidate.ordinal for candidate in candidates] == [1, 1, 2]

    duplicates = [candidate for candidate in candidates if "DUPLICATE_ORDINAL" in candidate.warnings]
    assert len(duplicates) == 2
    assert {candidate.source_path for candidate in duplicates} == {
        "book/chapter-01.txt",
        "book/chapter-01-copy.txt",
    }
    # The unique chapter keeps its file and gets no duplicate warning.
    unique = next(candidate for candidate in candidates if candidate.ordinal == 2)
    assert unique.source_path == "book/chapter-02.txt"
    assert unique.warnings == ()


def test_missing_and_empty_sections_name_the_file_and_the_reason() -> None:
    candidates = build_import_candidates(
        (
            ("book/no-heading.txt", "Không có số chương.\nNội dung."),
            ("book/blank.txt", "   \n  "),
        )
    )

    missing = candidates[0]
    assert missing.ordinal is None
    assert missing.source_path == "book/no-heading.txt"
    assert "ORDINAL_MISSING" in missing.warnings

    blank = candidates[1]
    assert blank.source_path == "book/blank.txt"
    assert "EMPTY_CHAPTER" in blank.warnings


def test_heading_detection_accepts_thousands_of_chapters() -> None:
    candidates = build_import_candidates(
        (
            ("book/chapter-1200.txt", "第1200章 Rất dài\nNội dung."),
            ("book/chapter-without-number.txt", "Ngoại truyện\nNội dung."),
        )
    )

    assert candidates[0].ordinal == 1200
    # The detected title keeps the raw heading text (the UI shows it as read).
    assert candidates[0].title == "第1200章 Rất dài"
    assert candidates[1].ordinal is None


def test_confirmed_sections_keep_their_order_and_text_unchanged() -> None:
    sections = (
        ("book/a.txt", "第1章 A\nNội dung A."),
        ("book/b.txt", "第2章 B\nNội dung B."),
    )

    candidates = build_import_candidates(sections)

    # Preview is read-only: the text a user confirms is the text that was read.
    assert [candidate.text for candidate in candidates] == [text for _, text in sections]
    assert [candidate.source_path for candidate in candidates] == [path for path, _ in sections]
