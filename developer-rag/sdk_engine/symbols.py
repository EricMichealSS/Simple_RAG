"""Version-specific symbol checks backed ONLY by evidence in the indexed corpus.

A "symbol" here is whatever the corpus documents for an endpoint: a request parameter, a response
field, a CLI flag, a generator option, a config key. The real SDK package is not available, so this is
a corpus-backed check and it says so. Statuses are deliberately conservative:

    deprecated        the version's own docs say it is deprecated (replacement given when stated)
    present_in_docs   the symbol appears in that version's spec text (table row, flag list, code, prose)
    absent_per_docs   the NEXT version's "what changed" text states it did not exist before
                      ("The --generate-markdown flag did not exist in 2.x", "no make_latest control")
    not_found         not in that version's docs - weak evidence only: absence from an indexed excerpt is
                      NOT proof the symbol does not exist

Nothing is ever reported as "removed": the corpus never says that, so neither do we.

The corpus text is messy (PDF extraction drops spaces: "didnotexistin2.x"), so long identifiers are
matched on a whitespace-squashed copy; short ones (<6 chars) only match as a line-initial table token or
a whole word, to avoid false positives like `-i` matching everywhere. Matching is case-sensitive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from .versioning import VersionCatalog

SYMBOL_RE = re.compile(r"^-{0,2}[A-Za-z_][A-Za-z0-9_.\-/]{0,63}$")
_SQUASH_MIN_LEN = 6

_DEPRECATION_PATTERNS = [
    re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s+is\s+deprecated\s+in\s+favor\s+of\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*?deprecated\s+in\s+favor\s+of\s+([A-Za-z_][A-Za-z0-9_]*)[^)]*\)"),
]
# Markers (in squashed lowercase text) that, near a symbol, say it did not exist in the PREVIOUS version.
_ABSENCE_AFTER = ("didnotexist", "doesnotexist", "wasnotavailable", "didnothave", "wasnotdocumented", "notavailablein")
_ABSENCE_BEFORE = ("hadno", "therewasno", "withoutany", "hadnodocumented")


class Evidence(BaseModel):
    kind: Literal["spec", "deprecation_statement", "absence_statement"]
    chunk_id: str
    source_name: str
    page: Optional[int] = None
    api_version: str
    excerpt: str = Field(max_length=300)


class UpgradeNote(BaseModel):
    """A fact about a NEWER version, always labelled; never presented as the caller's version behaviour."""

    version: str
    replacement: Optional[str] = None
    chunk_id: str
    excerpt: str = Field(max_length=300)


class SymbolFinding(BaseModel):
    version: str
    symbol: str
    endpoint: Optional[str] = None
    status: Literal["present_in_docs", "deprecated", "absent_per_docs", "not_found"]
    replacement: Optional[str] = None
    evidence: List[Evidence] = Field(default_factory=list)
    present_in_other_versions: List[str] = Field(default_factory=list)   # version labels only
    deprecated_in_newer_versions: List[UpgradeNote] = Field(default_factory=list)
    note: str = ""


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    text: str
    version: str
    endpoint: str
    content_type: str
    source_name: str
    page: Optional[int]

    @property
    def spaced(self) -> str:
        return " ".join(self.text.split())

    @property
    def squashed(self) -> str:
        return re.sub(r"\s+", "", self.text)


def _present(chunk: IndexedChunk, symbol: str, squash: bool = True) -> bool:
    bare = symbol.lstrip("-")
    if re.search(r"(?m)^\s*" + re.escape(symbol) + r"(?=[\s=,:(]|$)", chunk.text):
        return True
    if re.search(r"(?<![A-Za-z0-9_])" + re.escape(symbol) + r"(?![A-Za-z0-9_])", chunk.spaced):
        return True
    return squash and len(bare) >= _SQUASH_MIN_LEN and symbol in chunk.squashed


def _says_absent(before: str, after: str) -> bool:
    """`before`/`after` are the text windows around a symbol occurrence, squashed and lower-cased.

    A marker only counts inside the symbol's OWN sentence. Squashing removes spaces but keeps full stops, so the windows are cut at
    the sentence boundary: "...deprecated in favor of assignees. The type field did not exist in 2022-11-28" says nothing about
    `assignees` (found when a code check cited that chunk as proof that `assignees` did not exist)."""
    before = before.rsplit(".", 1)[-1]
    after = after.split(".", 1)[0]
    return any(m in before for m in _ABSENCE_BEFORE) or before.endswith("no") or any(m in after for m in _ABSENCE_AFTER)


def _absence_statement(chunk: IndexedChunk, symbol: str) -> bool:
    """Same matching discipline as _present: short symbols only as whole words in the spaced text (so '-g'
    can never match inside '--generate-markdown'); long identifiers on the squashed text, with a dash-prefixed
    symbol not allowed to start in the middle of a longer flag."""
    bare = symbol.lstrip("-")
    if len(bare) < _SQUASH_MIN_LEN:
        sp = chunk.spaced
        for m in re.finditer(r"(?<![A-Za-z0-9_\-])" + re.escape(symbol) + r"(?![A-Za-z0-9_\-])", sp):
            before = re.sub(r"\s+", "", sp[max(0, m.start() - 60): m.start()]).lower()
            after = re.sub(r"\s+", "", sp[m.end(): m.end() + 80]).lower()
            if _says_absent(before, after):
                return True
        return False
    low = chunk.squashed.lower()
    needle = symbol.lower()
    start = 0
    while True:
        i = low.find(needle, start)
        if i < 0:
            return False
        glued_into_longer_flag = symbol.startswith("-") and not symbol.startswith("--") and i > 0 and low[i - 1] == "-"
        if not glued_into_longer_flag and _says_absent(low[max(0, i - 30):i], low[i + len(needle): i + len(needle) + 40]):
            return True
        start = i + 1


def _deprecations(chunk: IndexedChunk) -> List[Tuple[str, str]]:
    found: List[Tuple[str, str]] = []
    for rx in _DEPRECATION_PATTERNS:
        for m in rx.finditer(chunk.spaced):
            pair = (m.group(1), m.group(2))
            if pair not in found:
                found.append(pair)
    return found


def _excerpt(chunk: IndexedChunk, symbol: str, width: int = 140) -> str:
    sp = chunk.spaced
    i = sp.find(symbol)
    if i < 0:
        return sp[: width * 2][:300]
    return sp[max(0, i - width): i + len(symbol) + width][:300]


class SymbolIndex:
    def __init__(self, chunks: Sequence[IndexedChunk], catalog: VersionCatalog):
        self._chunks = list(chunks)
        self._catalog = catalog

    @classmethod
    def from_collection(cls, collection: Any, catalog: VersionCatalog, limit: int = 5000) -> "SymbolIndex":
        got = collection.get(include=["documents", "metadatas"], limit=limit)
        chunks = []
        for cid, doc, meta in zip(got["ids"], got["documents"], got["metadatas"]):
            meta = meta or {}
            version = meta.get("api_version")
            if not isinstance(version, str) or version not in catalog.all_versions:
                continue  # unknown/missing version metadata never participates in a version-specific check
            page = meta.get("page")
            chunks.append(IndexedChunk(cid, doc, version, str(meta.get("endpoint", "")), str(meta.get("content_type", "")),
                                       str(meta.get("source_file", "")), page if isinstance(page, int) else None))
        return cls(chunks, catalog)

    # -- helpers ---------------------------------------------------------------------------------
    def _ordered(self, version: str) -> List[str]:
        product = self._catalog.product_of(version)
        return list(self._catalog.versions[product]) if product else [version]

    def _in(self, version: str, content_type: str, endpoint: Optional[str]) -> List[IndexedChunk]:
        return [c for c in self._chunks if c.version == version and c.content_type == content_type
                and (endpoint is None or c.endpoint == endpoint)]

    def deprecations_for(self, version: str, endpoint: Optional[str] = None) -> List[Dict[str, str]]:
        out = []
        for ct in ("spec", "diff"):
            for c in self._in(version, ct, endpoint):
                for dep, rep in _deprecations(c):
                    out.append({"version": version, "deprecated": dep, "replacement": rep, "chunk_id": c.chunk_id,
                                "endpoint": c.endpoint})
        return out

    # -- the check -------------------------------------------------------------------------------
    def check(self, version: str, symbol: str, endpoint: Optional[str] = None) -> SymbolFinding:
        if not SYMBOL_RE.match(symbol):
            raise ValueError("symbol must look like an identifier, flag or config key (max 64 chars)")
        order = self._ordered(version)
        spec = self._in(version, "spec", endpoint)
        base = dict(version=version, symbol=symbol, endpoint=endpoint)

        # 1. deprecated in this very version
        for ct in ("spec", "diff"):
            for c in self._in(version, ct, endpoint):
                for dep, rep in _deprecations(c):
                    if dep == symbol:
                        return SymbolFinding(**base, status="deprecated", replacement=rep,
                                             evidence=[Evidence(kind="deprecation_statement", chunk_id=c.chunk_id, source_name=c.source_name,
                                                                page=c.page, api_version=c.version, excerpt=_excerpt(c, symbol))],
                                             present_in_other_versions=self._other_versions(symbol, version, endpoint),
                                             note="The official docs for this version state the symbol is deprecated.")

        newer_notes = self._deprecated_in_newer(symbol, order, version, endpoint)
        hits = [c for c in spec if _present(c, symbol)]
        # 2. documented in this version's spec text
        if hits:
            return SymbolFinding(**base, status="present_in_docs",
                                 evidence=[Evidence(kind="spec", chunk_id=c.chunk_id, source_name=c.source_name, page=c.page,
                                                    api_version=c.version, excerpt=_excerpt(c, symbol)) for c in hits[:2]],
                                 present_in_other_versions=self._other_versions(symbol, version, endpoint),
                                 deprecated_in_newer_versions=newer_notes,
                                 note="Appears in the indexed official docs for this version (presence, not a proof of runtime behaviour).")

        # 3. the next version's "what changed" text says it did not exist before
        idx = order.index(version) if version in order else -1
        if 0 <= idx < len(order) - 1:
            nxt = order[idx + 1]
            for c in self._in(nxt, "diff", endpoint):
                if _absence_statement(c, symbol):
                    return SymbolFinding(**base, status="absent_per_docs",
                                         evidence=[Evidence(kind="absence_statement", chunk_id=c.chunk_id, source_name=c.source_name,
                                                            page=c.page, api_version=c.version, excerpt=_excerpt(c, symbol))],
                                         present_in_other_versions=self._other_versions(symbol, version, endpoint),
                                         note=f"The official {nxt} docs state this did not exist in {version}. Evidence is from {nxt}, labelled as such.")

        return SymbolFinding(**base, status="not_found", present_in_other_versions=self._other_versions(symbol, version, endpoint),
                             note="Not found in the indexed docs for this version. Absence from an indexed excerpt is NOT proof that the symbol does not exist.")

    def _other_versions(self, symbol: str, version: str, endpoint: Optional[str]) -> List[str]:
        product_versions = self._ordered(version)
        return [v for v in product_versions if v != version and any(_present(c, symbol) for c in self._in(v, "spec", endpoint))]

    def _deprecated_in_newer(self, symbol: str, order: List[str], version: str, endpoint: Optional[str]) -> List[UpgradeNote]:
        notes: List[UpgradeNote] = []
        for v in order[order.index(version) + 1:] if version in order else []:
            for ct in ("spec", "diff"):
                for c in self._in(v, ct, endpoint):
                    for dep, rep in _deprecations(c):
                        if dep == symbol:
                            notes.append(UpgradeNote(version=v, replacement=rep, chunk_id=c.chunk_id, excerpt=_excerpt(c, symbol)))
        return notes


def appears_in(text: str, symbol: str, *, squash: bool = True) -> bool:
    """Does `symbol` appear in `text`? Case-sensitive. With `squash` (the index's own discipline) long symbols also match in
    whitespace-squashed text, which tolerates PDF spacing damage but can match inside a longer run ('assignees' inside
    'assignee string' -> 'assigneestring'); with `squash=False` only whole-word matches count."""
    return _present(IndexedChunk("", text, "", "", "", "", None), symbol, squash)


def deprecations_in(text: str) -> List[Tuple[str, str]]:
    """(deprecated, replacement) pairs that `text` itself states, using the index's patterns."""
    return _deprecations(IndexedChunk("", text, "", "", "", "", None))
