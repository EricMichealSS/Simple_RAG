"""Chunking strategies for the developer docs RAG app.

Strategies:
1. ``chunk_text`` -- fixed-size sliding window (baseline).
2. ``chunk_structured`` -- structure-aware: detects headers, tables, code blocks
   in extracted text (PDF or markdown) and keeps them intact.
"""

import re

from config import (
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    STRUCTURED_CHUNK_SIZE,
)


# Patterns for detecting structure in extracted text
HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# Also detect ALL-CAPS headers common in PDF extraction
CAPS_HEADER_RE = re.compile(r"^[A-Z][A-Z\s]{2,}:?\s*$")

FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})")

# Table detection: lines with | separators
TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")

# Code block detection: indented blocks or fenced
INDENTED_CODE_RE = re.compile(r"^(\s{4,}|\t).+")


# ============================================================
# Strategy 1: fixed-size sliding window (baseline)
# ============================================================

def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split text into fixed-size overlapping character windows."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


# ============================================================
# PDF text normalization -> markdown-like
# ============================================================

def normalize_pdf_text(text):
    """Convert PDF-extracted text to markdown-like format for structure parsing."""
    lines = text.split("\n")
    normalized = []

    for line in lines:
        # Detect ALL-CAPS headers (common in PDF extraction)
        if CAPS_HEADER_RE.match(line.strip()):
            normalized.append(f"## {line.strip().rstrip(':')}")
            continue

        # Detect numbered sections like "1. Introduction"
        if re.match(r"^\d+\.\s+[A-Z]", line.strip()):
            normalized.append(f"## {line.strip()}")
            continue

        # Detect "In this article" as a sub-header
        if line.strip().lower().startswith("in this article"):
            normalized.append(f"### {line.strip()}")
            continue

        normalized.append(line)

    return "\n".join(normalized)


# ============================================================
# Structure-aware parsing (works on normalized text)
# ============================================================

def parse_structured_blocks(text):
    """Parse text into atomic blocks: header, table, code, prose."""
    lines = text.split("\n")
    blocks = []
    current_section = None
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # Markdown header
        header = HEADER_RE.match(line)
        if header:
            current_section = header.group(0)
            blocks.append({"kind": "header", "text": line, "section": current_section})
            i += 1
            continue

        # Fenced code block
        fence = FENCE_RE.match(line)
        if fence:
            code_lines = [line]
            i += 1
            while i < n:
                code_lines.append(lines[i])
                if lines[i].strip().startswith("```") or lines[i].strip().startswith("~~~"):
                    break
                i += 1
            i += 1
            blocks.append({"kind": "code", "text": "\n".join(code_lines), "section": current_section})
            continue

        # Table detection (markdown-style |...|)
        if TABLE_ROW_RE.match(line) and (i + 1) < n and TABLE_SEP_RE.match(lines[i + 1]):
            table_lines = [line]
            i += 1
            while i < n and TABLE_ROW_RE.match(lines[i]):
                table_lines.append(lines[i])
                i += 1
            blocks.append({"kind": "table", "text": "\n".join(table_lines), "section": current_section})
            continue

        # Indented code block (common in PDF extraction)
        if INDENTED_CODE_RE.match(line):
            code_lines = [line]
            i += 1
            while i < n and (INDENTED_CODE_RE.match(lines[i]) or lines[i].strip() == ""):
                code_lines.append(lines[i])
                i += 1
            blocks.append({"kind": "code", "text": "\n".join(code_lines), "section": current_section})
            continue

        # Prose run
        prose_lines = [line]
        i += 1
        while i < n:
            if HEADER_RE.match(lines[i]) or CAPS_HEADER_RE.match(lines[i].strip()):
                break
            if FENCE_RE.match(lines[i]):
                break
            if TABLE_ROW_RE.match(lines[i]) and (i + 1) < n and TABLE_SEP_RE.match(lines[i + 1]):
                break
            if INDENTED_CODE_RE.match(lines[i]):
                break
            prose_lines.append(lines[i])
            i += 1
        blocks.append({"kind": "prose", "text": "\n".join(prose_lines).strip(), "section": current_section})

    return [b for b in blocks if b["text"].strip()]


# ============================================================
# Strategy 2: structure-aware chunking (works on any text)
# ============================================================

def chunk_structured(text, chunk_size=STRUCTURED_CHUNK_SIZE, normalize=True):
    """Split text into chunks that never break a table or code block.

    - Headers start new chunks
    - Tables stay whole (header row + data rows)
    - Code blocks stay whole
    - Long prose split at paragraph boundaries
    """
    if normalize:
        text = normalize_pdf_text(text)

    blocks = parse_structured_blocks(text)

    chunks = []
    current = []
    current_size = 0

    def flush():
        nonlocal current, current_size
        if not current:
            return
        section = None
        for block in current:
            if block["kind"] == "header":
                section = block["text"]
                break
        if section is None:
            section = current[0].get("section")
        chunks.append({
            "text": "\n\n".join(b["text"] for b in current).strip(),
            "section": section,
        })
        current = []
        current_size = 0

    for block in blocks:
        block_size = len(block["text"]) + 2

        if block["kind"] == "header":
            flush()
            current.append(block)
            current_size = block_size
            continue

        if block["kind"] in ("table", "code"):
            if current_size + block_size > chunk_size and current:
                flush()
            if len(block["text"]) > chunk_size:
                flush()
                current.append(block)
                current_size = block_size
                flush()
            else:
                current.append(block)
                current_size += block_size
            continue

        paragraphs = [p.strip() for p in block["text"].split("\n\n") if p.strip()]
        for para in paragraphs:
            para_size = len(para) + 2
            if para_size > chunk_size and not current:
                chunks.append({"text": para, "section": block["section"]})
                continue
            if current_size + para_size > chunk_size and current:
                flush()
            current.append({"kind": "prose", "text": para, "section": block["section"]})
            current_size += para_size

    flush()
    return chunks


# ============================================================
# Helpers
# ============================================================

def anchor_for_section(section):
    """Convert a header into a stable anchor slug."""
    if not section:
        return "top"
    text = re.sub(r"^#+\s*", "", section)
    text = re.sub(r"[^a-zA-Z0-9\- ]", "", text)
    return "-".join(text.lower().split())


def section_short(section):
    """Short readable label for a section."""
    if not section:
        return "top"
    return re.sub(r"^#+\s*", "", section)