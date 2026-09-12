"""Plain text out of an uploaded document, whatever the provider stored it on.

Shared by every storage adapter: they hand the bytes over, this module
knows the formats. PDF through pypdf, DOCX through python-docx (paragraphs
then table cells), text and Markdown as UTF-8. Anything else — a scanned
image, an old ``.doc``, an ``.odt`` — is :class:`UnsupportedFormat`, which
the use case treats as "nothing to analyse", not as an error. A PDF whose
pages carry no text layer yields an empty string for the same reason.

Libraries are imported lazily: a page that never extracts text pays
nothing for them.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from pathlib import PurePosixPath

#: Suffixes the extractor understands (lower case, with the dot).
SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md"})


class UnsupportedFormat(ValueError):
    """The file is not one the extractor can read."""


def suffix_of(file_name: str) -> str:
    return PurePosixPath(file_name).suffix.lower()


def is_supported(file_name: str) -> bool:
    return suffix_of(file_name) in SUPPORTED_SUFFIXES


def extract_text(file_name: str, data: bytes) -> str:
    """The document's text, possibly empty; ``UnsupportedFormat`` otherwise."""
    suffix = suffix_of(file_name)
    if suffix == ".pdf":
        return _pdf(data)
    if suffix == ".docx":
        return _docx(data)
    if suffix in SUPPORTED_SUFFIXES:
        return data.decode("utf-8", errors="replace").strip()
    shown = suffix or "sans extension"
    accepted = ", ".join(sorted(s.lstrip(".").upper() for s in SUPPORTED_SUFFIXES))
    raise UnsupportedFormat(
        f"Format non pris en charge ({shown}) ; formats acceptés : {accepted}."
    )


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
    from pypdf.generic import DictionaryObject

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # An owner password only: the content opens with an empty one.
            if not reader.decrypt(""):
                raise UnsupportedFormat("PDF protégé par un mot de passe.")
        pages = []
        for page in reader.pages:
            if "/Contents" not in page:
                pages.append("")
                continue
            resources = page["/Resources"] if "/Resources" in page else {}
            xobjects = (
                resources["/XObject"]
                if isinstance(resources, DictionaryObject) and "/XObject" in resources else {}
            )
            objects = (
                (obj.get_object() for obj in xobjects.values())
                if isinstance(xobjects, DictionaryObject) else ()
            )
            if any(
                isinstance(obj, DictionaryObject) and obj.get("/Subtype") == "/Form"
                for obj in objects
            ):
                # Layout mode skips Form XObjects, even when regular page
                # text is present. Plain extraction follows their text too.
                pages.append(page.extract_text() or "")
                continue
            # PDF drawing order can split sentences into one word per line
            # or concatenate separate skill categories. Rebuild visual lines
            # before anonymization and analysis; keep rotated text as well.
            text = page.extract_text(
                extraction_mode="layout",
                layout_mode_space_vertically=False,
                layout_mode_strip_rotated=False,
            )
            pages.append(text if text.strip() else (page.extract_text() or ""))
    except PyPdfError as exc:
        raise UnsupportedFormat(f"PDF illisible : {exc}") from exc
    return "\n\n".join(pages).strip()


def _docx(data: bytes) -> str:
    """Every paragraph of the document, wherever Word put it.

    ``document.paragraphs`` and ``document.tables`` only reach the top level
    of the body. CV templates routinely park the contact block in a header
    and a whole column in a text box, and a table inside a table cell is
    just as common — all of which would come back empty, and a CV under
    ``MIN_TEXT_LENGTH`` looks exactly like a scan. So the paragraphs are
    walked in the XML, which also keeps a paragraph's runs joined: the
    anonymiser's rules read a line, not a fragment.
    """
    import docx
    from docx.opc.exceptions import PackageNotFoundError
    from docx.oxml.ns import qn

    try:
        document = docx.Document(io.BytesIO(data))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise UnsupportedFormat(f"DOCX illisible : {exc}") from exc

    paragraph_tag, cell_tag = qn("w:p"), qn("w:tc")
    fallback_tag = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"

    def duplicated(element) -> bool:
        # A shape is stored twice, under mc:Choice and under mc:Fallback.
        node = element.getparent()
        while node is not None:
            if node.tag == fallback_tag:
                return True
            node = node.getparent()
        return False

    def text_of(element) -> str:
        # A text box can nest paragraphs inside another paragraph. Each is
        # visited by walk(), so only read text belonging to this paragraph.
        return "".join(
            node.text or "" for node in element.iter(qn("w:t"))
            if next(node.iterancestors(paragraph_tag), None) is element
        )

    def walk(root) -> Iterator[str]:
        for element in root.iter(paragraph_tag):
            if duplicated(element):
                continue
            line = text_of(element).strip()
            if not line:
                continue
            parent = element.getparent()
            # Cells of one row read as one line, as they are laid out.
            yield f"\t{line}" if parent is not None and parent.tag == cell_tag else line

    parts: list[str] = []
    seen_parts: set[str] = set()
    for section in document.sections:
        for header_or_footer in (section.header, section.footer,
                                 section.first_page_header, section.first_page_footer,
                                 section.even_page_header, section.even_page_footer):
            # Linked sections reference the same part. Deduplicate that
            # structural repetition, never equal text in separate paragraphs:
            # repeated employers and dates still belong to each experience.
            part_name = str(header_or_footer.part.partname)
            if part_name not in seen_parts:
                seen_parts.add(part_name)
                parts.extend(walk(header_or_footer._element))
    parts.extend(walk(document.element.body))
    # A cell's line is marked with a tab; join a run of them with « · ».
    lines: list[str] = []
    cells: list[str] = []
    for part in parts:
        if part.startswith("\t"):
            cells.append(part[1:])
            continue
        if cells:
            lines.append(" · ".join(cells))
            cells = []
        lines.append(part)
    if cells:
        lines.append(" · ".join(cells))
    return "\n".join(lines).strip()
