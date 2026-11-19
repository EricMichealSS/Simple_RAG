"""Validate and extract text from an UNTRUSTED uploaded file. In memory only: nothing is written to disk, nothing is
executed, nothing embedded in the file is followed or evaluated.

Supported: PDF (text layer only, no OCR), DOCX (paragraphs, headings, tables as text), Markdown, plain text.
Rejected with a category (never a stack trace): wrong/unsupported type, content that does not match the extension,
encrypted or unreadable PDFs, DOCX with macros / excessive size or compression ratio / path-traversal member names,
binary data in a "text" file, too many pages, too much extracted text, files that take too long to parse, and
files with no extractable text.

Known limits (documented in docs/week12/phase4.md): parsing happens in-process (pypdf / python-docx). Size, page,
member and time limits bound the cost, but a hostile file that hangs the parser can only be abandoned (the call
times out), not killed. Scanned PDFs yield no text and are rejected. Embedded images/objects are ignored.
"""

from __future__ import annotations

import concurrent.futures
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Callable, List, Optional, TypeVar

T = TypeVar("T")

ALLOWED_EXTENSIONS = {"pdf", "docx", "md", "txt"}
PARSE_TIMEOUT_S = 20.0
MAX_ZIP_MEMBERS = 2000
MAX_ZIP_UNCOMPRESSED = 50_000_000
MAX_ZIP_RATIO = 100

_BIDI_AND_ZERO_WIDTH = dict.fromkeys(list(range(0x200B, 0x2010)) + list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)) + [0xFEFF], None)
_FILENAME_OK = re.compile(r"[^\w.\-() ]", re.UNICODE)


class UploadError(ValueError):
    """A rejected upload. `category` is stable and safe to show; the message never echoes file contents."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


@dataclass
class Unit:
    text: str
    page: Optional[int] = None   # PDF page number (1-based); None for formats without pages


@dataclass
class ExtractedDoc:
    file_type: str
    units: List[Unit]

    @property
    def chars(self) -> int:
        return sum(len(u.text) for u in self.units)


def sanitize_filename(name: object) -> str:
    """Reduce a client-supplied name to a safe, display-only basename. Never used as a path."""
    if not isinstance(name, str) or not name.strip():
        raise UploadError("bad_filename", "a filename is required")
    s = unicodedata.normalize("NFKC", name).translate(_BIDI_AND_ZERO_WIDTH)
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")           # control / format characters
    s = re.split(r"[\\/]", s)[-1]                                               # basename for BOTH separators
    s = _FILENAME_OK.sub("_", s)
    s = re.sub(r"\s+", " ", s).strip().lstrip(". ")                              # no hidden / dot-only names
    if not s or "." not in s:
        raise UploadError("bad_filename", "filename must have a supported extension")
    stem, _, ext = s.rpartition(".")
    if not stem:
        raise UploadError("bad_filename", "filename must have a name before its extension")
    return f"{stem[:100]}.{ext.lower()[:8]}"


def file_extension(clean_name: str) -> str:
    ext = clean_name.rpartition(".")[2].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadError("unsupported_type", f"unsupported file type .{ext[:8]} (allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))})")
    return ext


def _check_zip(data: bytes) -> zipfile.ZipFile:
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise UploadError("content_mismatch", "file is not a valid DOCX container")
    zf = zipfile.ZipFile(io.BytesIO(data))
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise UploadError("archive_too_large", "document container has too many parts")
    total = sum(i.file_size for i in infos)
    packed = max(1, sum(i.compress_size for i in infos))
    if total > MAX_ZIP_UNCOMPRESSED or total / packed > MAX_ZIP_RATIO:
        raise UploadError("archive_too_large", "document container is too large or too highly compressed")
    names = [i.filename for i in infos]
    for n in names:
        if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/"):
            raise UploadError("unsafe_archive", "document container has an unsafe member path")
    low = [n.lower() for n in names]
    if any(n.endswith(("vbaproject.bin", "vbadata.xml")) or "/macros/" in n or n.endswith(".bin") and "vba" in n for n in low):
        raise UploadError("macros_not_supported", "documents containing macros are not accepted")
    if "word/document.xml" not in names:
        raise UploadError("content_mismatch", "file is not a Word document")
    return zf


def validate_content(ext: str, data: bytes) -> None:
    """The extension is a claim; the bytes decide."""
    if ext == "pdf":
        if b"%PDF-" not in data[:1024]:
            raise UploadError("content_mismatch", "file does not look like a PDF")
    elif ext == "docx":
        _check_zip(data).close()
    else:
        head = data[:8192]
        if b"\x00" in head:
            raise UploadError("not_text", "file contains binary data")
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise UploadError("not_text", "text files must be UTF-8")
        ctrl = sum(1 for b in head if b < 9 or (13 < b < 32))
        if head and ctrl / len(head) > 0.05:
            raise UploadError("not_text", "file contains too many control characters")


def _with_timeout(fn: Callable[[], T], seconds: float) -> T:
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn)
    try:
        return fut.result(timeout=seconds)
    except concurrent.futures.TimeoutError:
        raise UploadError("parse_timeout", f"the file took longer than {seconds:g}s to parse and was abandoned")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def _extract_pdf(data: bytes, max_pages: int, max_chars: int) -> List[Unit]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UploadError("encrypted", "encrypted PDFs are not accepted")
        n = len(reader.pages)
        if n > max_pages:
            raise UploadError("too_many_pages", f"PDF has {n} pages; the limit is {max_pages}")
        units, total = [], 0
        for i, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            total += len(text)
            if total > max_chars:
                raise UploadError("too_much_text", f"extracted text exceeds {max_chars} characters")
            if text:
                units.append(Unit(text, i))
        return units
    except UploadError:
        raise
    except Exception as exc:  # malformed input: class name only, never file content
        raise UploadError("unreadable", f"the PDF could not be read ({type(exc).__name__})")


def _extract_docx(data: bytes, max_chars: int) -> List[Unit]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        doc = Document(io.BytesIO(data))
        lines: List[str] = []
        for child in doc.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                p = Paragraph(child, doc)
                text = p.text.strip()
                if not text:
                    continue
                style = (p.style.name if p.style is not None else "") or ""
                m = re.match(r"Heading (\d)", style)
                if m:
                    lines.append("#" * min(int(m.group(1)), 6) + " " + text)
                elif style == "Title":
                    lines.append("# " + text)
                else:
                    lines.append(text)
            elif tag == "tbl":
                rows = [[c.text.strip().replace("\n", " ").replace("|", "/") for c in r.cells] for r in Table(child, doc).rows]
                if rows:
                    lines.append("")
                    lines.append("| " + " | ".join(rows[0]) + " |")
                    lines.append("|" + "|".join(["---"] * len(rows[0])) + "|")
                    lines.extend("| " + " | ".join(r) + " |" for r in rows[1:])
                    lines.append("")
            if sum(len(x) for x in lines) > max_chars:
                raise UploadError("too_much_text", f"extracted text exceeds {max_chars} characters")
        text = "\n\n".join(lines).strip()
        return [Unit(text)] if text else []
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError("unreadable", f"the document could not be read ({type(exc).__name__})")


def extract(ext: str, data: bytes, *, max_pages: int, max_chars: int, timeout_s: float = PARSE_TIMEOUT_S) -> ExtractedDoc:
    if ext == "pdf":
        units = _with_timeout(lambda: _extract_pdf(data, max_pages, max_chars), timeout_s)
    elif ext == "docx":
        units = _with_timeout(lambda: _extract_docx(data, max_chars), timeout_s)
    else:
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        if len(text) > max_chars:
            raise UploadError("too_much_text", f"text exceeds {max_chars} characters")
        text = text.strip()
        units = [Unit(text)] if text else []
    if not units:
        raise UploadError("no_extractable_text", "the file has no extractable text (scanned images are not supported)")
    return ExtractedDoc(ext, units)
