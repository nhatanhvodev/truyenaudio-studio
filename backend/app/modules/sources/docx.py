from __future__ import annotations

from pathlib import Path

from app.modules.sources.archive_guard import ImportCandidate, build_import_candidates, inspect_archive, split_chapter_sections


def read_docx(path: str | Path) -> tuple[ImportCandidate, ...]:
    archive_path = inspect_archive(path).path
    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(archive_path)
    lines: list[str] = []
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            text = Paragraph(child, document).text.strip()
            if text:
                lines.append(text)
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            for row in table.rows:
                for cell in row.cells:
                    text = "\n".join(paragraph.text.strip() for paragraph in cell.paragraphs if paragraph.text.strip())
                    if text:
                        lines.append(text)
    return build_import_candidates(split_chapter_sections("\n".join(lines), archive_path.name))
