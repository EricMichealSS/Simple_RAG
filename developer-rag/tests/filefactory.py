"""Build real (and deliberately hostile) uploaded files for tests, in memory."""

from __future__ import annotations

import io
import zipfile
from typing import List, Optional, Sequence


def make_pdf(pages: Sequence[str]) -> bytes:
    """A minimal but valid multi-page PDF with a Helvetica text layer (one text block per page)."""
    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs: List[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        content_no = 5 + 2 * i
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_no} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>".encode())
        lines = text.split("\n") if text else []
        parts = ["BT /F1 12 Tf 72 720 Td"] if lines else []
        for j, line in enumerate(lines):
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            parts.append(("0 -16 Td " if j else "") + f"({esc}) Tj")
        if lines:
            parts.append("ET")
        stream = "\n".join(parts).encode("latin-1", "replace")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def make_encrypted_pdf(pages: Sequence[str], password: str = "pw") -> bytes:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(make_pdf(pages)))
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    writer.encrypt(password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_docx(blocks: Sequence[tuple]) -> bytes:
    """blocks: ("h1", text) | ("h2", text) | ("p", text) | ("table", [[cell, ...], ...])"""
    import docx

    d = docx.Document()
    for kind, content in blocks:
        if kind in ("h1", "h2", "h3"):
            d.add_heading(content, int(kind[1]))
        elif kind == "p":
            d.add_paragraph(content)
        elif kind == "table":
            t = d.add_table(rows=len(content), cols=len(content[0]))
            for r, row in enumerate(content):
                for c, cell in enumerate(row):
                    t.cell(r, c).text = cell
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _rewrite_zip(data: bytes, extra: Sequence[tuple]) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        for name, payload in extra:
            dst.writestr(name, payload)
    return buf.getvalue()


def with_macro(docx_bytes: bytes) -> bytes:
    return _rewrite_zip(docx_bytes, [("word/vbaProject.bin", b"\xd0\xcf\x11\xe0 not really a macro")])


def with_zip_bomb(docx_bytes: bytes, megabytes: int = 60) -> bytes:
    return _rewrite_zip(docx_bytes, [("word/bomb.xml", b"\x00" * (megabytes * 1_000_000))])


def with_traversal_member(docx_bytes: bytes) -> bytes:
    return _rewrite_zip(docx_bytes, [("../../evil.txt", b"x")])


def zip_without_document_xml() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "just a zip")
    return buf.getvalue()
