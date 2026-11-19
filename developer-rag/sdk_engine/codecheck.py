"""Static validation of the code samples in an answer (Phase 6).

WHAT THIS IS. For every fenced code block: identify the language, parse it where a parser exists (Python `ast`, JSON, shell
tokenising), pull out the things the documentation can speak about (HTTP headers, request-body and query keys, CLI flags,
Python keyword arguments and dict keys, curl routes) and check each against EVIDENCE, strongest first:

    cited                  appears in a passage the model was actually shown
    elsewhere_in_version   documented elsewhere in the same version's docs (symbol index, via MCP `check_symbol` or in-process)
    deprecated             this version's own docs say it is deprecated (replacement + chunk id)
    absent_per_docs        a later version's docs state it did not exist in this version                  -> HARD
    other_version_only     not documented for this endpoint/version but documented for another version    -> HARD when the
                           endpoint is known and the same endpoint documents it elsewhere, else SOFT
    unverified             looked for, found nowhere                                                       -> SOFT

HARD findings keep the answer from being delivered. SOFT findings cause one correction retry and are shown as warnings if the
retry did not fix them. Everything else is reported, never blocked.

WHAT THIS IS NOT. It does not execute anything. `parsed` means well-formed text; "documented" is not "works". Imports and method
signatures are NOT checked because this corpus (REST reference pages and a code generator's CLI) has no SDK package or import
index; header/option values are not validated; routes and required parameters are checked against the retrieved passages only.
Every report lists what was not checked. "cited" means "in a retrieved passage": if a retrieved document is poisoned, it can
'cite' a bad identifier; the output-safety layer (not this module) is what limits what a poisoned document can do.
"""

from __future__ import annotations

import ast
import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qsl

from .answering import MAX_PASSAGE_CHARS
from .schemas import CodeBlockReport, CodeFinding, CodeValidationReport, Passage
from .symbols import SYMBOL_RE, SymbolFinding, appears_in, deprecations_in

MAX_BLOCKS = 6            # blocks analysed per answer; the rest are counted, not analysed
MAX_BLOCK_LINES = 150
MAX_SYMBOLS = 12          # distinct identifiers sent to the symbol lookup per answer
MAX_KEYS_PER_BLOCK = 40

# (version, symbol, endpoint-or-None) -> finding, or None when the lookup could not be performed.
Lookup = Callable[[str, str, Optional[str]], Optional[SymbolFinding]]

# Plumbing that is not a claim about the SDK/API under test. Never looked up, never reported.
_SCAFFOLD_HEADERS = {"accept", "authorization", "content-type", "content-length", "user-agent", "host", "cache-control",
                     "x-requested-with", "if-none-match", "if-modified-since"}
_SCAFFOLD_KWARGS = {"json", "data", "params", "headers", "timeout", "auth", "verify", "files", "cookies", "stream",
                    "allow_redirects", "proxies", "cert", "url", "method"}
_PARAM_KINDS = {"body_key", "query_param", "kwarg", "dict_key"}      # scoped to an endpoint when one is known

_LANG = {"py": "python", "python": "python", "python3": "python", "json": "json", "jsonc": "json",
         "sh": "shell", "bash": "shell", "shell": "shell", "zsh": "shell", "console": "shell", "terminal": "shell", "curl": "shell"}
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*([^\s`]*)[^\n]*$")
_SNIFF_SHELL_RE = re.compile(r"^(?:[$>]\s*)?(?:curl|swagger-codegen\S*|java\s+-jar)\b")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_JSON_KEY_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_.\-]*)"\s*:')
_FORM_KEY_RE = re.compile(r"(?:^|&)([A-Za-z_][A-Za-z0-9_.\-]*)=")
_URL_PATH_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.\-]*://[^/?#]*)?(/[^?#]*)?(?:\?([^#]*))?")
_ROUTE_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD)\s+(/[^\s\"'<>`]*)")
_PARAM_ROW_RE = re.compile(r"(?<![A-Za-z0-9_])([a-z][a-z0-9_]*)\s+(path|query|body|header)\s+")
_CODEGEN_SUBCOMMANDS = {"generate", "config-help", "langs", "meta", "validate", "version", "help", "batch"}
_CURL_OPTS = {
    "-X": "method", "--request": "method", "-H": "header", "--header": "header",
    "-d": "data", "--data": "data", "--data-raw": "data", "--data-binary": "data", "--data-urlencode": "data", "--json": "data",
    "--url": "url",
    "-u": "skip", "--user": "skip", "-o": "skip", "--output": "skip", "-A": "skip", "--user-agent": "skip", "-e": "skip",
    "--referer": "skip", "-b": "skip", "--cookie": "skip", "-F": "skip", "--form": "skip", "-m": "skip", "--max-time": "skip",
    "--connect-timeout": "skip", "-w": "skip", "-T": "skip", "--retry": "skip", "-x": "skip", "--proxy": "skip",
}
_IGNORED_COMMANDS = {"export", "set", "cd", "echo", "unset", "source", "sudo"}


def _clean(value: object) -> str:
    """Ids, versions and replacement names can come from another process (the MCP server). Before they can reach user-visible
    text they are reduced to a safe alphabet, so a hostile server cannot smuggle a link or an instruction through a finding."""
    return re.sub(r"[^A-Za-z0-9_.:\-]", "", str(value or ""))[:80]


# ------------------------------------------------------------------------------------------------ extraction
@dataclass
class RawBlock:
    info: str
    text: str
    closed: bool


def extract_blocks(answer: str) -> List[RawBlock]:
    """Fenced (``` or ~~~) blocks, in order. An unterminated fence runs to the end of the answer and is marked."""
    lines = answer.splitlines()
    blocks: List[RawBlock] = []
    i = 0
    while i < len(lines):
        m = _FENCE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        fence, info = m.group(1), m.group(2).lower()
        body: List[str] = []
        j, closed = i + 1, False
        while j < len(lines):
            s = lines[j].strip()
            if len(s) >= len(fence) and set(s) == {fence[0]}:
                closed = True
                break
            body.append(lines[j])
            j += 1
        blocks.append(RawBlock(info, "\n".join(body), closed))
        i = j + 1
    return blocks


def has_code(text: str) -> bool:
    """Cheap pre-check so an answer without code costs nothing (no span, no lookup)."""
    return bool(extract_blocks(text))


def _language(info: str, text: str) -> str:
    info = re.sub(r"[^a-z0-9_+#.\-]", "", info)[:20]
    if info:
        return _LANG.get(info, info)
    t = text.lstrip()
    if _SNIFF_SHELL_RE.match(t):
        return "shell"
    if t[:1] in ("{", "["):
        return "json"
    return "unknown"


# ------------------------------------------------------------------------------------------------ parsing
@dataclass
class _Route:
    method: str
    path: str
    body_keys: List[str]
    query_keys: List[str]


@dataclass
class _Parsed:
    language: str
    syntax: str = "not_checked"
    syntax_detail: str = ""
    idents: List[Tuple[str, str]] = field(default_factory=list)       # (symbol, kind)
    routes: List[_Route] = field(default_factory=list)
    unsupported: List[str] = field(default_factory=list)


def _parse_python(text: str) -> _Parsed:
    p = _Parsed("python")
    try:
        tree = ast.parse(text)          # builds a syntax tree only: nothing is executed or imported
    except SyntaxError as exc:
        p.syntax, p.syntax_detail = "error", f"line {exc.lineno}: {exc.msg}"[:100]
        return p
    except (ValueError, RecursionError, MemoryError):
        p.syntax, p.syntax_detail = "error", "could not be parsed"
        return p
    p.syntax = "parsed"
    imports: List[str] = []
    method_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or ".")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                method_calls += 1
            for kw in node.keywords:
                if kw.arg and kw.arg.lower() not in _SCAFFOLD_KWARGS:
                    p.idents.append((kw.arg, "kwarg"))
        elif isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value.lower() not in _SCAFFOLD_HEADERS:
                    p.idents.append((k.value, "dict_key"))
    p.unsupported.append("URLs and routes inside string literals: not checked")
    if imports:
        shown = ", ".join(sorted({re.sub(r"[^A-Za-z0-9_.]", "", i)[:30] for i in imports})[:4])
        p.unsupported.append(f"imports ({shown}): not checked, there is no SDK package or import index for this corpus")
    if method_calls:
        p.unsupported.append(f"{method_calls} method call(s) and their signatures: not checked, there is no SDK symbol index")
    return p


def _parse_json(text: str) -> _Parsed:
    p = _Parsed("json")
    try:
        json.loads(text)
        p.syntax = "parsed"
    except ValueError as exc:
        p.syntax, p.syntax_detail = "error", str(exc)[:80]
    p.idents += [(k, "body_key") for k in _JSON_KEY_RE.findall(text)]
    p.unsupported.append("values: not validated")
    return p


def _body_keys(data: str, p: _Parsed) -> List[str]:
    if data.startswith("@"):
        p.unsupported.append("a request body read from a file (@file): not inspected")
        return []
    return _JSON_KEY_RE.findall(data) if "{" in data or '"' in data else _FORM_KEY_RE.findall(data)


def _curl(args: Sequence[str], p: _Parsed) -> None:
    method: Optional[str] = None
    url: Optional[str] = None
    headers: List[str] = []
    datas: List[str] = []
    i = 0
    while i < len(args):
        a, val = args[i], None
        if a.startswith("--") and "=" in a:
            a, val = a.split("=", 1)
        kind = _CURL_OPTS.get(a)
        if kind is not None:
            if val is None:
                i += 1
                val = args[i] if i < len(args) else ""
            if kind == "method":
                method = val.upper()
            elif kind == "header":
                headers.append(val)
            elif kind == "data":
                datas.append(val)
            elif kind == "url":
                url = val
        elif a.startswith("-") and len(a) > 1:
            pass                                  # a bare flag: -L -s -i -v ...
        elif url is None:
            url = a
        i += 1
    for h in headers:
        name = h.split(":", 1)[0].strip() if ":" in h else ""
        if name and name.lower() not in _SCAFFOLD_HEADERS:
            p.idents.append((name, "header"))
    body_keys = [k for d in datas for k in _body_keys(d, p)]
    query_keys: List[str] = []
    if url:
        m = _URL_PATH_RE.match(url)
        path = (m.group(1) or "") if m else ""
        if m and m.group(2):
            query_keys = [k for k, _ in parse_qsl(m.group(2), keep_blank_values=True)]
        if path:
            p.routes.append(_Route(method or ("POST" if datas else "GET"), path.rstrip("/") or "/", body_keys, query_keys))
    p.idents += [(k, "body_key") for k in body_keys] + [(k, "query_param") for k in query_keys]


def _codegen(toks: Sequence[str], p: _Parsed) -> None:
    sub = next((n for n, t in enumerate(toks) if t in _CODEGEN_SUBCOMMANDS), None)
    if sub is None:
        p.unsupported.append("swagger-codegen command without a recognised subcommand: flags not checked")
        return
    for t in toks[sub + 1:]:
        if t.startswith("-") and not re.fullmatch(r"-\d+", t) and not t.startswith("-D"):
            p.idents.append((t.split("=", 1)[0], "flag"))


def _logical_lines(text: str) -> List[str]:
    """Split a shell block into commands: a newline ends a command only OUTSIDE quotes, and backslash-newline continues one.
    (A multi-line -d '{ ... }' body is one argument; splitting by physical line turned its JSON keys into 'commands'.)"""
    out: List[str] = []
    cur: List[str] = []
    quote: Optional[str] = None
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if quote:
            cur.append(ch)
            if ch == "\\" and quote == '"' and nxt:
                cur.append(nxt)
                i += 1
            elif ch == quote:
                quote = None
        elif ch == "\\" and nxt in ("\n", "\r"):
            i += 2 if text[i + 1:i + 3] != "\r\n" else 3
            cur.append(" ")
            continue
        elif ch == "\\" and nxt:
            cur += [ch, nxt]
            i += 1
        elif ch in ("'", '"'):
            quote = ch
            cur.append(ch)
        elif ch == "\n":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if cur:
        out.append("".join(cur))
    return out


def _parse_shell(text: str) -> _Parsed:
    p = _Parsed("shell")
    parsed_any = False
    for raw in _logical_lines(text):
        line = re.sub(r"^(?:\$|>)\s+", "", raw.strip())
        if not line or line.startswith("#"):
            continue
        try:
            toks = shlex.split(line, posix=True)
        except ValueError as exc:
            p.syntax, p.syntax_detail = "error", f"unbalanced quoting: {exc}"[:80]
            continue
        while toks and _ENV_ASSIGN_RE.match(toks[0]):
            toks = toks[1:]
        if not toks:
            continue
        parsed_any = True
        head = toks[0]
        if head == "curl":
            _curl(toks[1:], p)
        elif "swagger-codegen" in head or (head == "java" and any("swagger-codegen" in t for t in toks)):
            _codegen(toks, p)
        elif head not in _IGNORED_COMMANDS:
            p.unsupported.append(f"command `{re.sub(r'[^A-Za-z0-9_./-]', '', head)[:30]}`: no checks (only curl and the swagger-codegen CLI are understood)")
    if p.syntax != "error":
        p.syntax = "parsed" if parsed_any else "not_checked"
    p.unsupported.append("header and option values: not validated")
    return p


# ------------------------------------------------------------------------------------------------ evidence
def _template_matches(template: str, path: str) -> bool:
    t, s = template.rstrip("/").split("/"), path.rstrip("/").split("/")
    return len(t) == len(s) and all(("{" in a or a.startswith(":") or a == b) for a, b in zip(t, s))


def _find_route(route: _Route, passages: Sequence[Passage]) -> Optional[Passage]:
    for p in passages:
        for m in _ROUTE_RE.finditer(" ".join(p.text[:MAX_PASSAGE_CHARS].split())):
            if m.group(1) == route.method and _template_matches(m.group(2).rstrip(".,;)"), route.path):
                return p
    return None


def required_params(text: str) -> List[Tuple[str, str]]:
    """(name, where) of parameters a REST parameter table marks 'Required.' ("title body string or integer Required. ...")."""
    sp = " ".join(text.split())
    rows = list(_PARAM_ROW_RE.finditer(sp))
    out: List[Tuple[str, str]] = []
    for k, m in enumerate(rows):
        end = rows[k + 1].start() if k + 1 < len(rows) else len(sp)
        if re.match(r"[^.]{0,60}?Required\.", sp[m.end():end]):
            out.append((m.group(1), m.group(2)))
    return out


@dataclass
class _Ctx:
    version: str
    versions: List[str]
    passages: List[Passage]
    lookup: Optional[Lookup]
    question_endpoint: Optional[str]
    cache: Dict[tuple, Optional[SymbolFinding]] = field(default_factory=dict)
    distinct: Set[str] = field(default_factory=set)
    lookups: int = 0

    def at_limit(self, sym: str) -> bool:
        return sym not in self.distinct and len(self.distinct) >= MAX_SYMBOLS

    def ask(self, version: str, sym: str, endpoint: Optional[str]) -> Optional[SymbolFinding]:
        key = (version, sym, endpoint)
        if key in self.cache:
            return self.cache[key]
        if self.lookup is None:
            return None
        self.distinct.add(sym)
        self.lookups += 1
        try:
            found = self.lookup(version, sym, endpoint)
        except Exception:      # a lookup problem must never break an answer; it is reported as "not checked"
            found = None
        self.cache[key] = found
        return found


def _passage_support(sym: str, kind: str, ctx: _Ctx) -> Optional[CodeFinding]:
    for p in ctx.passages:
        window = p.text[:MAX_PASSAGE_CHARS]
        # strict whole-word matching here: a passage "supports" an identifier only if it really shows it. The lookup tier below
        # is the lenient one (it tolerates PDF spacing damage).
        if not (appears_in(window.lower(), sym.lower(), squash=False) if kind == "header" else appears_in(window, sym, squash=False)):
            continue
        dep = dict(deprecations_in(window))
        if sym in dep:
            return CodeFinding(symbol=sym, kind=kind, tier="deprecated", severity="warn", chunk_id=p.chunk_id, replacement=dep[sym],
                               detail=f"deprecated in {p.api_version or ctx.version}: use `{dep[sym]}`")
        return CodeFinding(symbol=sym, kind=kind, tier="cited", severity="info", chunk_id=p.chunk_id)
    return None


def _classify(sym: str, kind: str, ctx: _Ctx, endpoint: Optional[str]) -> CodeFinding:
    base = dict(symbol=sym, kind=kind)
    cited = _passage_support(sym, kind, ctx)
    if cited:
        return cited
    if ctx.lookup is None:
        return CodeFinding(**base, tier="not_checked", severity="warn", reason_code="no_symbol_index",
                           detail="not in the cited passages, and no symbol index is available to look further")
    if ctx.at_limit(sym):
        return CodeFinding(**base, tier="not_checked", severity="warn", reason_code="lookup_limit",
                           detail=f"per-answer lookup limit ({MAX_SYMBOLS}) reached")
    scoped = kind in _PARAM_KINDS and endpoint is not None and len(ctx.versions) == 1
    ep = endpoint if scoped else None
    found = [(v, ctx.ask(v, sym, ep)) for v in ctx.versions]
    answered = [(v, f) for v, f in found if f is not None]
    if not answered:
        return CodeFinding(**base, tier="not_checked", severity="warn", reason_code="lookup_unavailable",
                           detail="the symbol lookup was unavailable")

    def first(status: str) -> Optional[Tuple[str, SymbolFinding]]:
        return next(((v, f) for v, f in answered if f.status == status), None)

    hit = first("present_in_docs")
    if hit:
        v, f = hit
        return CodeFinding(**base, tier="elsewhere_in_version", severity="info", chunk_id=f.evidence[0].chunk_id if f.evidence else None,
                           detail=f"documented elsewhere in the {v} docs, not in the cited passages")
    hit = first("deprecated")
    if hit:
        v, f = hit
        return CodeFinding(**base, tier="deprecated", severity="warn", replacement=f.replacement,
                           chunk_id=f.evidence[0].chunk_id if f.evidence else None,
                           detail=f"deprecated in {v}" + (f": use `{f.replacement}`" if f.replacement else ""))
    hit = first("absent_per_docs")
    if hit and len(ctx.versions) == 1:
        v, f = hit
        ev = f.evidence[0] if f.evidence else None
        return CodeFinding(**base, tier="absent_per_docs", severity="hard", chunk_id=_clean(ev.chunk_id) if ev else None,
                           other_versions=[_clean(ev.api_version)] if ev else [],
                           detail=f"`{sym}` did not exist in {v}" + (f" (the {_clean(ev.api_version)} docs state this)" if ev else "")
                                  + f"; use only what the {v} documents show")
    others = sorted({_clean(o) for _, f in answered for o in f.present_in_other_versions if o not in ctx.versions})
    if others:
        severity = "hard" if scoped else "soft"
        return CodeFinding(**base, tier="other_version_only", severity=severity, other_versions=others,
                           detail=f"`{sym}` is not documented for {ctx.version}" + (" for this endpoint" if scoped else "")
                                  + f" but is documented for {', '.join(others)}; use only what the {ctx.version} documents show")
    if scoped:   # not found for the endpoint and not documented elsewhere for it: is it documented anywhere in this version?
        wide = ctx.ask(ctx.versions[0], sym, None)
        if wide is not None and wide.status == "present_in_docs":
            return CodeFinding(**base, tier="elsewhere_in_version", severity="info",
                               chunk_id=wide.evidence[0].chunk_id if wide.evidence else None,
                               detail=f"documented in the {ctx.version} docs, but not for this endpoint")
    return CodeFinding(**base, tier="unverified", severity="soft",
                       detail=f"`{sym}` is not found in the {ctx.version} documents; remove it or use only parameters shown in them")


# ------------------------------------------------------------------------------------------------ orchestration
def _safe_path(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-.{}/:<>~%]", "?", path)[:80]


def _validate_block(n: int, rb: RawBlock, ctx: _Ctx) -> CodeBlockReport:
    lang = _language(rb.info, rb.text)
    lines = rb.text.count("\n") + 1 if rb.text.strip() else 0
    rep = CodeBlockReport(index=n, language=lang, lines=lines, syntax="not_checked")
    rep.unsupported_checks.append("execution and runtime behaviour: not performed")
    if not rb.closed:
        rep.syntax_detail = "unterminated code fence"
        rep.unsupported_checks.append("the block was not closed: not analysed")
        return rep
    if lines > MAX_BLOCK_LINES:
        rep.syntax_detail = f"longer than {MAX_BLOCK_LINES} lines"
        rep.unsupported_checks.append(f"blocks longer than {MAX_BLOCK_LINES} lines: not analysed")
        return rep
    parser = {"python": _parse_python, "json": _parse_json, "shell": _parse_shell}.get(lang)
    if parser is None:
        rep.unsupported_checks.append(f"syntax and identifiers: no parser for `{lang}`")
        return rep
    parsed = parser(rb.text)
    rep.syntax, rep.syntax_detail = parsed.syntax, parsed.syntax_detail
    rep.unsupported_checks += parsed.unsupported
    if parsed.syntax == "error":
        hard = lang == "python"
        rep.findings.append(CodeFinding(
            symbol="syntax", kind="syntax", tier="syntax_error", severity="hard" if hard else "warn",
            detail=f"the {lang} code does not parse ({parsed.syntax_detail}); fix the syntax" if hard
            else f"the {lang} text does not parse ({parsed.syntax_detail}); elided or commented samples are common in docs"))

    endpoint = ctx.question_endpoint
    for r in parsed.routes:
        sym = f"{re.sub(r'[^A-Z]', '', r.method.upper())[:10] or 'REQUEST'} {_safe_path(r.path)}"
        hit = _find_route(r, ctx.passages)
        if hit:
            endpoint = hit.endpoint or endpoint
            rep.findings.append(CodeFinding(symbol=sym, kind="route", tier="cited", severity="info", chunk_id=hit.chunk_id))
            have = set(r.body_keys) | set(r.query_keys)
            for name, where in required_params(hit.text[:MAX_PASSAGE_CHARS]):
                if where in ("body", "query") and name not in have:
                    rep.findings.append(CodeFinding(
                        symbol=name, kind="required_param", tier="missing_required", severity="warn", chunk_id=hit.chunk_id,
                        detail=f"the docs mark `{name}` ({where}) as required and this sample does not set it"))
        else:
            rep.findings.append(CodeFinding(
                symbol=sym, kind="route", tier="unverified", severity="soft",
                detail=f"the route `{sym}` is not shown in the cited documents for {ctx.version}; use only routes the documents show"))

    seen: Set[Tuple[str, str]] = set()
    for sym, kind in parsed.idents:
        if (sym, kind) in seen or not SYMBOL_RE.match(sym):
            continue
        seen.add((sym, kind))
        if len(seen) > MAX_KEYS_PER_BLOCK:
            rep.unsupported_checks.append(f"more than {MAX_KEYS_PER_BLOCK} identifiers: the rest were not checked")
            break
        rep.findings.append(_classify(sym, kind, ctx, endpoint))
    return rep


def validate_code(answer: str, *, version: str, versions: Optional[Sequence[str]] = None, passages: Sequence[Passage] = (),
                  lookup: Optional[Lookup] = None, question_endpoints: Sequence[str] = ()) -> Optional[CodeValidationReport]:
    """Report for the answer's fenced code blocks, or None when it has none. `versions` is the caller's version, or every
    version of an explicit comparison (an identifier then needs support in only one of them)."""
    raw = extract_blocks(answer)
    if not raw:
        return None
    vs = list(versions) if versions else [version]
    eps = set(question_endpoints)
    ctx = _Ctx(version=version, versions=vs, passages=list(passages), lookup=lookup,
               question_endpoint=next(iter(eps)) if len(eps) == 1 else None)
    blocks = [_validate_block(n, rb, ctx) for n, rb in enumerate(raw[:MAX_BLOCKS], 1)]
    return CodeValidationReport(
        version=version, blocks=blocks, lookups=ctx.lookups, skipped_blocks=max(0, len(raw) - MAX_BLOCKS),
        unsupported_checks=["mandatory client-side arguments such as timeouts: not checked (the indexed docs state required "
                            "parameters only in REST parameter tables, which are checked for curl samples)"])


# ------------------------------------------------------------------------------------------------ using the report
def failures_for(report: CodeValidationReport, *, retries_left: bool) -> List[str]:
    """Reasons to send the model back (or, with no retry left, to withhold the answer). HARD always counts; SOFT only while a
    retry remains, so a soft finding can never turn an otherwise good answer into an abstention."""
    out: List[str] = []
    for b in report.blocks:
        for f in b.findings:
            if f.severity == "hard" or (f.severity == "soft" and retries_left):
                out.append(f"code block {b.index}: {f.detail}")
    return out[:6]


def has_hard(report: Optional[CodeValidationReport]) -> bool:
    return bool(report and report.hard)


def render_footer(report: CodeValidationReport) -> str:
    """A deterministic, model-free summary appended to a delivered answer. Built only from our own findings (identifiers are
    restricted to the symbol grammar), never from free text, so it cannot carry a link or an instruction."""
    lines = ["Code check (static, against the documents; nothing was executed):"]
    for b in report.blocks:
        ok = [f for f in b.findings if f.kind != "route" and f.tier in ("cited", "elsewhere_in_version")]
        cited = sum(f.tier == "cited" for f in ok)
        parts = [{"parsed": "syntax parsed", "error": "syntax error", "not_checked": "syntax not checked"}[b.syntax]]
        if b.syntax == "error" and b.language == "json":
            parts = ["not strict JSON (comments or elisions?)"]
        routes = [f for f in b.findings if f.kind == "route"]
        if routes:
            good = [f for f in routes if f.tier == "cited"]
            parts.append(f"route `{good[0].symbol}` found in the cited docs [{_clean(good[0].chunk_id)}]" if good
                         else f"route `{routes[0].symbol}` not found in the cited docs")
        if ok:
            parts.append(f"{len(ok)} identifier(s) documented ({cited} in cited passages, {len(ok) - cited} elsewhere in the {report.version} docs)")
        for f in b.findings:
            if f.tier == "deprecated":
                parts.append(f"deprecated: `{f.symbol}`" + (f" (use `{_clean(f.replacement)}`)" if f.replacement else "")
                             + (f" [{_clean(f.chunk_id)}]" if f.chunk_id else ""))
        unver = [f.symbol for f in b.findings if f.tier in ("unverified", "other_version_only") and f.kind != "route"]
        if unver:
            parts.append("unverified: " + ", ".join(f"`{s}`" for s in unver[:5]))
        # An identifier that could not be checked must never look like one that passed: say so, and say why (found by the Phase 7
        # incident drill: during an outage the engine shipped samples whose identifiers were unchecked and the footer was silent).
        unchecked = [f for f in b.findings if f.tier == "not_checked" and f.kind != "route"]
        if unchecked:
            why = {"no_symbol_index": "no symbol index available", "lookup_unavailable": "symbol lookup unavailable",
                   "lookup_limit": "per-answer lookup limit"}.get(unchecked[0].reason_code or "", "could not be checked")
            parts.append(f"{len(unchecked)} identifier(s) could NOT be checked ({why}): " + ", ".join(f"`{f.symbol}`" for f in unchecked[:5]))
        missing = [f.symbol for f in b.findings if f.tier == "missing_required"]
        if missing:
            parts.append("does not set required: " + ", ".join(f"`{s}`" for s in missing[:5]))
        if b.syntax == "error" and b.language == "shell":
            parts.append("(a docs-style elided sample is common; check it yourself)")
        lines.append(f"- block {b.index} ({b.language}): " + "; ".join(parts) + ".")
    not_checked: List[str] = []
    for b in report.blocks:
        for u in b.unsupported_checks:
            short = u.split(":")[0]
            if short not in not_checked and not short.startswith("execution"):
                not_checked.append(short)
    lines.append("Not checked: " + "; ".join(not_checked[:5] + ["runtime behaviour"]) + ".")
    return "\n".join(lines)


def explain_blocked(report: CodeValidationReport, label: str, version: str) -> str:
    """The abstention text when a code sample could not be validated: says plainly what, and why, from our own findings."""
    lines = [f"I could not give a code sample that is validated for {label} {version}."]
    for b in report.blocks:
        for f in b.findings:
            if f.severity != "hard":
                continue
            ref = f" [{_clean(f.chunk_id)}]" if f.chunk_id else ""
            if f.tier == "absent_per_docs":
                newer = f.other_versions[0] if f.other_versions else None
                lines.append(f"- `{f.symbol}` does not exist in {version}{ref}."
                             + (f" It is documented from {newer}: to use it, move to {newer} (the source above is that version's "
                                "'previous version' note), or ask me for a comparison." if newer else ""))
            elif f.tier == "other_version_only":
                lines.append(f"- `{f.symbol}` is not documented for {version} (it is documented for {', '.join(_clean(o) for o in f.other_versions)}). "
                             "Ask for that version, or ask for a comparison.")
            elif f.tier == "syntax_error":
                lines.append(f"- a {b.language} sample did not parse.")
    return "\n".join(lines)
