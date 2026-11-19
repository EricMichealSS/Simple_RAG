"""Resolve the caller's SDK/API version BEFORE any version-sensitive retrieval.

Precedence (first that yields a supported version wins; none of them guesses):

1. an explicit version passed by the caller;
2. a version stated in the question text;
3. an operator-configured default for the question's product (lower confidence);
4. otherwise ask the user to clarify. A silent guess is never made.

Hard rules, all covered by tests:
* A version the corpus has no docs for (``2024-01-01``, ``v4``) is **refused** (UNSUPPORTED),
  never mapped to the "nearest" version.
* Conflicting signals (explicit ``2022-11-28`` while the question names ``2025-06-01``; or two
  different products) with no comparison intent are AMBIGUOUS, not resolved.
* Two versions with explicit comparison intent ("migrate from X to Y") resolve to the *from*
  version and carry ``comparison_versions`` - the only way cross-version documents are authorised.

The known versions are loaded from the corpus metadata file, not hard-coded, so a corpus change
cannot silently drift from this module. The endpoint alias table below is a small hand-authored
heuristic used only to infer the PRODUCT; aliases for endpoints absent from the catalog are ignored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .schemas import Product, VersionResolution, VersionSource, VersionStatus

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NX_RE = re.compile(r"^(\d+)\.x$", re.IGNORECASE)
_DASHES = {ord(c): "-" for c in "‐‑‒–—―−﹘﹣－"}


def normalize_dashes(text: str) -> str:
    """Replace the dash-like characters that web pages, word processors and language models produce (U+2010..U+2015, U+2212, ...)
    with an ASCII hyphen, so a date written with a NON-BREAKING hyphen is the same version as the ASCII one. Without this a pasted
    date was 'unsupported', and the wrong-version-claim check could not see a date in an answer: found by the Phase 8 live run, where
    the model wrote its dates with U+2011."""
    return text.translate(_DASHES)


def product_for_version(version: Optional[str]) -> Optional[Product]:
    """Product implied by the SHAPE of a version string (date => GitHub REST, N.x => Swagger Codegen)."""
    if not version:
        return None
    if _DATE_RE.match(version):
        return Product.GITHUB_REST
    if _NX_RE.match(version):
        return Product.SWAGGER_CODEGEN
    return None


def _sort_key(version: str) -> Tuple[int, str]:
    m = _NX_RE.match(version)
    return (int(m.group(1)), "") if m else (0, version)


@dataclass(frozen=True)
class VersionCatalog:
    """The versions and endpoints the indexed corpus actually documents."""

    versions: Mapping[Product, Tuple[str, ...]]
    endpoint_product: Mapping[str, Product]

    @property
    def all_versions(self) -> Tuple[str, ...]:
        return tuple(v for p in self.versions for v in self.versions[p])

    def product_of(self, version: str) -> Optional[Product]:
        for product, vs in self.versions.items():
            if version in vs:
                return product
        return None

    @classmethod
    def from_page_metadata(cls, path: Path) -> "VersionCatalog":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        by_product: Dict[Product, set] = {}
        endpoint_product: Dict[str, Product] = {}
        for fname, pages in data.items():
            if fname.startswith("_") or fname.startswith("known_"):
                continue
            for page in pages.values():
                version, endpoint = page["api_version"], page["endpoint"]
                product = product_for_version(version)
                if product is None:
                    raise ValueError(f"page_metadata has a version of unrecognised shape: {version!r}")
                by_product.setdefault(product, set()).add(version)
                if endpoint_product.setdefault(endpoint, product) is not product:
                    raise ValueError(f"endpoint {endpoint!r} appears under two products")
        return cls(
            versions={p: tuple(sorted(vs, key=_sort_key)) for p, vs in by_product.items()},
            endpoint_product=endpoint_product,
        )


# Heuristic, hand-authored: natural-language aliases -> endpoint id. Used ONLY to infer product /
# version-sensitivity. Whole-word matching.
ENDPOINT_ALIASES: Mapping[str, Tuple[str, ...]] = {
    "render_markdown": ("render markdown", "markdown"),
    "create_issue": ("create an issue", "create issue", "issue", "issues", "assignee", "assignees"),
    "create_pull_request": ("pull request", "pull requests", "maintainer_can_modify", "pr"),
    "create_repository": ("create a repository", "create repository", "repository", "repo", "visibility"),
    "create_release": ("release", "releases", "make_latest"),
    "cli_general": ("cli", "command line", "command-line"),
    "java_generator": ("java generator", "java", "maven"),
    "python_generator": ("python generator", "python"),
    "ignore_file": ("ignore file", "swagger-codegen-ignore", ".swagger-codegen-ignore"),
    "config_file": ("config file", "config.json", "configuration file"),
}
_PRODUCT_HINTS: Mapping[Product, Tuple[str, ...]] = {
    Product.GITHUB_REST: ("github", "rest api"),
    Product.SWAGGER_CODEGEN: ("swagger", "codegen", "openapi generator"),
}


def _word_in(text: str, phrase: str) -> bool:
    return re.search(r"(?<![\w])" + re.escape(phrase) + r"(?![\w])", text) is not None


def detect_endpoints(question: str, catalog: VersionCatalog) -> List[str]:
    q = normalize_dashes(question).lower()
    found: List[str] = []
    for ep in catalog.endpoint_product:
        # The endpoint's own id (create_issue) and its spaced form always count, in addition to the
        # hand-authored aliases. Without the id, "create_issue" would not match the alias "issue"
        # because "_" is a word character.
        names = (ep, ep.replace("_", " "), *ENDPOINT_ALIASES.get(ep, ()))
        if any(_word_in(q, n) for n in names):
            found.append(ep)
    return found


def detect_products(question: str, catalog: VersionCatalog) -> List[Product]:
    """Products the question points at, via endpoint aliases and explicit product words."""
    question = normalize_dashes(question)
    q = question.lower()
    found: List[Product] = []
    for ep in detect_endpoints(question, catalog):
        p = catalog.endpoint_product[ep]
        if p not in found:
            found.append(p)
    for product, hints in _PRODUCT_HINTS.items():
        if any(_word_in(q, h) for h in hints) and product not in found:
            found.append(product)
    return found


# --------------------------------------------------------------------------------------------
# Version mention extraction
# --------------------------------------------------------------------------------------------
_DATE_FIND = re.compile(r"(?<![\d-])(\d{4}-\d{2}-\d{2})(?![\d-])")
_NX_FIND = re.compile(r"(?<![\w.])(\d+)\.x(?![\w])", re.IGNORECASE)
_V_FIND = re.compile(r"(?<![\w.\-])v\s?(\d+)(?:\.\d+){0,2}(?![\w\-])", re.IGNORECASE)
_VERSION_WORD_FIND = re.compile(r"\bversion\s+(\d+)(?:\.\d+){0,2}(?![\w\-])", re.IGNORECASE)
_CODEGEN_FIND = re.compile(r"\b(?:swagger[- ]?codegen|codegen)\s+v?(\d+)(?:\.\d+){0,2}(?![\w\-])", re.IGNORECASE)
# Comparison intent. Deliberately limited to STRONG phrases: generic words such as "new", "old",
# "than" or "while" would turn a genuine explicit-vs-question version conflict into a "comparison".
# The second group was added after real golden-set questions ("across both v2 and v3", "missing in
# v2", "introduced in v3 ... did not exist in v2", "replace the heuristic used in 2022-11-28") were
# wrongly sent to clarification.
_COMPARE_RE = re.compile(
    r"\b(from|upgrad\w*|migrat\w*|compar\w*|differences?|differ\w*|vs\.?|versus|between|changed?|moving|move"
    r"|both|replac\w*|introduc\w*|missing|instead of|rather than|whereas"
    r"|(?:not|n't|never)\s+exist\w*|(?:previous|prior)\s+version)\b",
    re.IGNORECASE,
)
_FROM_TO_RE = re.compile(r"\bfrom\s+(?:version\s+)?(\S+?)\s+to\s+(?:version\s+)?(\S+?)(?=[\s,.;?!)]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class _Mentions:
    known: Tuple[str, ...]        # supported versions mentioned, order of first appearance
    unsupported_dates: Tuple[str, ...]
    unsupported_semver: Tuple[str, ...]


def _extract_mentions(question: str, catalog: VersionCatalog) -> _Mentions:
    q = normalize_dashes(question)
    spans: List[Tuple[int, str, str]] = []  # (position, kind, normalized)
    for m in _DATE_FIND.finditer(q):
        spans.append((m.start(), "date", m.group(1)))
    masked = _DATE_FIND.sub(lambda m: " " * len(m.group(0)), q)  # dates must not feed the semver scans
    for rx in (_NX_FIND, _V_FIND, _VERSION_WORD_FIND, _CODEGEN_FIND):
        for m in rx.finditer(masked):
            spans.append((m.start(), "semver", f"{int(m.group(1))}.x"))
    spans.sort()
    known: List[str] = []
    bad_dates: List[str] = []
    bad_semver: List[str] = []
    all_known = set(catalog.all_versions)
    for _, kind, norm in spans:
        if norm in all_known:
            if norm not in known:
                known.append(norm)
        elif kind == "date":
            if norm not in bad_dates:
                bad_dates.append(norm)
        elif norm not in bad_semver:
            bad_semver.append(norm)
    return _Mentions(tuple(known), tuple(bad_dates), tuple(bad_semver))


def mentioned_versions(text: str, catalog: VersionCatalog) -> Tuple[str, ...]:
    """Every corpus-documented version named in `text` (used to police an answer's version claims)."""
    return _extract_mentions(text, catalog).known


def normalize_version(token: str, catalog: VersionCatalog) -> Tuple[Optional[str], str]:
    """Normalise a caller-supplied version token. Returns (supported_version | None, normalised_label)."""
    t = token.strip()
    if _DATE_RE.match(t):
        return (t if t in catalog.all_versions else None), t
    m = re.match(r"^(?:v|version\s*)?(\d+)(?:\.(?:x|\d+)){0,2}$", t, re.IGNORECASE)
    if m:
        label = f"{int(m.group(1))}.x"
        return (label if label in catalog.all_versions else None), label
    return None, t


def is_version_sensitive(question: str, catalog: VersionCatalog) -> bool:
    mentions = _extract_mentions(question, catalog)
    return bool(
        detect_endpoints(question, catalog)
        or detect_products(question, catalog)
        or mentions.known
        or mentions.unsupported_dates
        or mentions.unsupported_semver
    )


def _comparison_order(question: str, known: Sequence[str]) -> List[str]:
    """Order two named versions as (from, to). 'from X to Y' wins; otherwise lowest -> highest."""
    m = _FROM_TO_RE.search(question)
    if m:
        a, b = m.group(1).strip("'\"`"), m.group(2).strip("'\"`")
        pair = []
        for tok in (a, b):
            for v in known:
                if tok.lower().lstrip("v") == v.lower().replace(".x", "") or tok == v or tok.lower() == v.lower():
                    pair.append(v)
                    break
        if len(pair) == 2 and pair[0] != pair[1]:
            return pair
    return sorted(known, key=_sort_key)


def resolve_version(
    question: str,
    *,
    catalog: VersionCatalog,
    explicit: Optional[str] = None,
    config_defaults: Optional[Mapping[str, str]] = None,
) -> VersionResolution:
    config_defaults = config_defaults or {}
    available = list(catalog.all_versions)
    question = normalize_dashes(question)
    explicit = normalize_dashes(explicit) if explicit is not None else None
    mentions = _extract_mentions(question, catalog)
    products = detect_products(question, catalog)

    # 1. Explicit caller version.
    if explicit is not None and explicit.strip():
        supported, label = normalize_version(explicit, catalog)
        if supported is None:
            return VersionResolution(
                status=VersionStatus.UNSUPPORTED, source=VersionSource.EXPLICIT, candidates=available,
                needs_clarification=True,
                reason=f"explicit version {label!r} has no documentation in the corpus; refusing to substitute a nearby version",
            )
        extra = [v for v in mentions.known if v != supported]
        if extra:
            if _COMPARE_RE.search(question):
                versions = _comparison_order(question, [supported, *extra])
                return VersionResolution(
                    status=VersionStatus.RESOLVED, product=catalog.product_of(supported), version=supported,
                    source=VersionSource.EXPLICIT, comparison_versions=versions, candidates=available,
                    confidence=0.9, reason=f"caller is on {supported}; question compares {versions}",
                )
            return VersionResolution(
                status=VersionStatus.AMBIGUOUS, source=VersionSource.EXPLICIT, candidates=[supported, *extra],
                needs_clarification=True,
                reason=f"explicit version {supported!r} conflicts with {extra} named in the question; no comparison intent detected",
            )
        return VersionResolution(
            status=VersionStatus.RESOLVED, product=catalog.product_of(supported), version=supported,
            source=VersionSource.EXPLICIT, candidates=available, confidence=1.0, reason="caller supplied the version",
        )

    # 2. Version stated in the question.
    if mentions.unsupported_dates:
        return VersionResolution(
            status=VersionStatus.UNSUPPORTED, source=VersionSource.QUESTION, candidates=available,
            needs_clarification=True,
            reason=f"question names {list(mentions.unsupported_dates)}, which the corpus does not document; refusing to guess",
        )
    if mentions.known:
        prods = {catalog.product_of(v) for v in mentions.known}
        if len(prods) > 1:
            return VersionResolution(
                status=VersionStatus.AMBIGUOUS, source=VersionSource.QUESTION, candidates=list(mentions.known),
                needs_clarification=True, reason="question names versions of different products",
            )
        if len(mentions.known) == 1:
            v = mentions.known[0]
            return VersionResolution(
                status=VersionStatus.RESOLVED, product=catalog.product_of(v), version=v,
                source=VersionSource.QUESTION, candidates=available, confidence=0.95,
                reason="version stated in the question",
            )
        if _COMPARE_RE.search(question):
            order = _comparison_order(question, mentions.known)
            return VersionResolution(
                status=VersionStatus.RESOLVED, product=catalog.product_of(order[0]), version=order[0],
                source=VersionSource.QUESTION, comparison_versions=order, candidates=available, confidence=0.9,
                reason=f"comparison intent between {order}; resolved to the starting version",
            )
        return VersionResolution(
            status=VersionStatus.AMBIGUOUS, source=VersionSource.QUESTION, candidates=list(mentions.known),
            needs_clarification=True, reason="question names several versions with no comparison intent",
        )
    if mentions.unsupported_semver:
        return VersionResolution(
            status=VersionStatus.UNSUPPORTED, source=VersionSource.QUESTION, candidates=available,
            needs_clarification=True,
            reason=f"question names {list(mentions.unsupported_semver)}, which the corpus does not document; refusing to guess",
        )

    # 3. Operator-configured default, only for a product we can identify from the question.
    if len(products) == 1:
        configured = config_defaults.get(products[0].value)
        if configured:
            supported, label = normalize_version(configured, catalog)
            if supported is not None and catalog.product_of(supported) is products[0]:
                return VersionResolution(
                    status=VersionStatus.RESOLVED, product=products[0], version=supported,
                    source=VersionSource.CONFIG, candidates=available, confidence=0.6,
                    reason=f"no version in the request; using the operator-configured default for {products[0].value}",
                )
    if len(products) > 1:
        return VersionResolution(
            status=VersionStatus.AMBIGUOUS, candidates=available, needs_clarification=True,
            reason=f"question touches several products {[p.value for p in products]} and names no version",
        )

    # 4. Ask. Never guess.
    sensitive = is_version_sensitive(question, catalog)
    return VersionResolution(
        status=VersionStatus.UNKNOWN, candidates=available, needs_clarification=sensitive,
        reason="no version evidence; the answer depends on the version, so the user must say which one"
        if sensitive else "no version evidence, but the question does not appear version-sensitive",
    )
