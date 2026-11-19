"""Single documented entry point:  python -m sdk_engine <command>   (run from developer-rag/).

Commands implemented so far:
    query        answer one question for the caller's SDK version, or about the caller's own uploads (--owner)
    versions     list the versions and endpoints the indexed corpus documents
    health       check the effective settings, index, catalog and optional LLM key (never prints the key); --deep also
                 checks index integrity and starts the MCP server, discovers its tools and runs one probe search
    index        check | rebuild --yes   verify every chunk's version tag against page_metadata.json / re-run the ingest
    mcp-serve    run the MCP tool server on stdio (what an MCP client spawns; the engine starts its own copy)
    upload       upload a PDF / DOCX / Markdown / text file into YOUR private collection (--owner)
    uploads      list | delete DOC_ID | purge   your uploaded documents (--owner)
    trace        find | show TRACE_ID | stats   find a request by time, version, tool, status, code finding, cost; inspect one trace
    eval         list | run | judge | label-sheet | agreement | regress | golden | trajectory | load | history   the integrated evaluation suite (sdk_eval/)

A bad setting never produces a traceback: the message names the environment variable and the command exits 2.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

from .config import ENGINE_ROOT, ConfigError, EngineConfig, get_api_key
from .extract import UploadError


def _load_dotenv() -> None:
    env_file = ENGINE_ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file, override=False)
        except ImportError:
            pass


def _cmd_query(args: argparse.Namespace) -> int:
    from .factory import build_engine

    engine = build_engine(use_llm=not args.no_llm, use_mcp=not args.no_mcp)
    with engine:
        result = engine.ask(args.question, explicit_version=args.version, owner_id=args.owner, scope=args.scope)
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(result.answer)
        if result.deprecation_facts:
            print("\nDeprecations stated by the official docs:")
            for f in result.deprecation_facts:
                tag = " (newer than your version)" if f.get("cross_version") else ""
                print(f"  - {f.get('deprecated')} is deprecated in favor of {f.get('replacement')} in {f.get('version')}{tag} [{f.get('chunk_id')}]")
        if result.deprecation_notes:
            print("\nDeprecation / migration notes (from the official docs):")
            for n in result.deprecation_notes:
                scope = f"applies to {n.applies_to_version}" + (" (newer than your version)" if n.cross_version else "")
                print(f"  - {scope} [{n.citation.chunk_id}] {n.excerpt[:260]}")
        if result.citations:
            print("\nSources:")
            for c in result.citations:
                ver = f"version {c.api_version}" if c.api_version else "your upload"
                print(f"  - {c.source_name} | {ver} | {c.location} | {c.chunk_id}")
        u = result.usage
        cost = f"${u.cost_usd:.6f}" if u.cost_status == "calculated" and u.cost_usd is not None else u.cost_status
        print(f"\n[{result.outcome}] trace_id={result.trace_id} retrieval={result.retrieval_via or 'n/a'} llm_calls={u.llm_calls} cost={cost} {result.latency_ms:.0f} ms"
              + (f"\nreason: {result.reason}" if result.reason else ""))
    return 1 if result.outcome == "error" else 0


def _cmd_versions(_: argparse.Namespace) -> int:
    from .versioning import VersionCatalog

    cfg = EngineConfig.from_env()
    cat = VersionCatalog.from_page_metadata(cfg.page_metadata_path)
    for product, versions in cat.versions.items():
        eps = sorted(e for e, p in cat.endpoint_product.items() if p is product)
        print(f"{product.value}: versions={list(versions)} endpoints={eps}")
    return 0


def _deep_health(cfg: EngineConfig, report: dict) -> bool:
    """Heavier checks, off by default: index integrity, then the MCP server end to end (start, discover, one real search). No model call, no cost."""
    ok = True
    try:
        from .indexing import check_index

        chk = check_index(cfg)
        report["index_check"] = {k: chk[k] for k in ("ok", "chunks", "by_version", "pages_expected", "pages_indexed", "problems")}
        ok = ok and chk["ok"]
    except Exception as exc:
        ok = False
        report["index_check"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        from .mcp_client import McpToolClient, McpUnavailable, default_server_config
        from .versioning import VersionCatalog

        version = VersionCatalog.from_page_metadata(cfg.page_metadata_path).all_versions[0]
        t0 = time.perf_counter()
        with McpToolClient([default_server_config(cfg)], call_timeout_s=cfg.tool_timeout_s, max_output_chars=cfg.max_tool_output_chars) as client:
            startup_ms = (time.perf_counter() - t0) * 1000
            specs = client.tools()
            probe = client.call("search_docs", {"query": "authentication", "version": version, "top_k": 1})
        report["mcp"] = {"startup_ms": round(startup_ms), "tools": {s.name: ("authorized" if s.authorized else f"rejected: {s.rejected_reason}") for s in specs},
                         "probe": {"tool": "search_docs", "version": version, "ok": probe.ok, "latency_ms": round(probe.latency_ms, 1),
                                   **({} if probe.ok else {"error_category": probe.error_category})}}
        ok = ok and probe.ok and any(s.authorized for s in specs)
    except Exception as exc:                          # McpUnavailable or anything the start-up raised
        ok = False
        report["mcp"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return ok


def _cmd_health(args: argparse.Namespace) -> int:
    cfg = EngineConfig.from_env()
    report = {"config": cfg.public_view()}
    ok = True
    try:
        from .retrieval import open_collection

        report["indexed_chunks"] = open_collection(cfg).count()
    except Exception as exc:
        ok = False
        report["index_error"] = f"{type(exc).__name__}: {exc}"
    try:
        from .versioning import VersionCatalog

        cat = VersionCatalog.from_page_metadata(cfg.page_metadata_path)
        report["versions"] = list(cat.all_versions)
    except Exception as exc:
        ok = False
        report["catalog_error"] = f"{type(exc).__name__}: {exc}"
    report["llm_key_present"] = bool(get_api_key())  # boolean only; the value is never read into output
    if getattr(args, "deep", False):
        ok = _deep_health(cfg, report) and ok
    report["status"] = "ok" if ok else "degraded"
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


def _cmd_index(args: argparse.Namespace) -> int:
    from . import indexing

    cfg = EngineConfig.from_env()
    if args.action == "rebuild":
        if not args.yes:
            print(f"error: rebuild DELETES and re-creates the collection {cfg.official_collection!r} under {cfg.chroma_path} (re-embeds every page, "
                  "needs the three PDFs and the embedding model); re-run with --yes to confirm", file=sys.stderr)
            return 2
        try:
            indexing.rebuild(cfg)
        except (indexing.RebuildRefused, indexing.RebuildFailed) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print("rebuilt; checking the result:")
    try:
        chk = indexing.check_index(cfg)
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(chk, indent=2))
    return 0 if chk["ok"] else 1


def _cmd_mcp_serve(_: argparse.Namespace) -> int:
    from .mcp_server import main as serve

    serve()                  # stdout belongs to the JSON-RPC stream: print nothing here
    return 0


def _uploads():
    from .factory import LazyUploadStore

    return LazyUploadStore(EngineConfig.from_env())


def _cmd_upload(args: argparse.Namespace) -> int:
    cfg = EngineConfig.from_env()
    path = Path(args.file)
    if not path.is_file():
        print(f"error: not a file: {path.name}", file=sys.stderr)
        return 2
    if path.stat().st_size > cfg.max_upload_bytes:     # refuse BEFORE reading it into memory
        print(f"error: file_too_large: the limit is {cfg.max_upload_bytes} bytes", file=sys.stderr)
        return 2
    try:
        res = _uploads().ingest(args.owner, path.name, path.read_bytes())
    except UploadError as exc:
        print(f"error: {exc.category}: {exc}", file=sys.stderr)
        return 2
    print(f"{res.status}: {res.filename} ({res.file_type}) doc_id={res.doc_id} chunks={res.chunks}"
          + (f" pages={res.pages}" if res.pages else "") + f" sha256={res.content_hash[:12]}...")
    if res.flagged_chunks:
        print(f"warning: {res.flagged_chunks} chunk(s) contain instruction-like text ({', '.join(res.flag_patterns)}). "
              "They are kept as data and shown to the model as untrusted; they cannot change what the engine does.")
    return 0


def _cmd_uploads(args: argparse.Namespace) -> int:
    store = _uploads()
    try:
        if args.action == "list":
            docs = store.list(args.owner)
            if not docs:
                print("no uploaded documents")
            for d in docs:
                print(f"{d.doc_id}  {d.filename}  type={d.file_type} chunks={d.chunks}" + (f" pages={d.pages}" if d.pages else "") + f"  uploaded={d.uploaded_at}")
        elif args.action == "delete":
            if not args.doc_id:
                print("error: delete needs a DOC_ID", file=sys.stderr)
                return 2
            r = store.delete(args.owner, args.doc_id)
            print(f"deleted {r.chunks} chunks of {args.doc_id} (physically purged from disk: {'yes' if r.purged else 'NO - best effort failed'})")
        elif args.action == "purge":
            if not args.yes:
                print("error: purge removes ALL of this owner's uploads; re-run with --yes to confirm", file=sys.stderr)
                return 2
            r = store.delete_all(args.owner)
            print(f"purged {r.chunks} chunks (physically purged from disk: {'yes' if r.purged else 'NO - best effort failed'})")
    except UploadError as exc:
        print(f"error: {exc.category}: {exc}", file=sys.stderr)
        return 2
    return 0


def _query_from(args: argparse.Namespace):
    from . import tracequery as tq

    now = tq.parse_when(args.as_of) if args.as_of else None        # relative times (7d, 12h) are measured from here; default: the real clock
    return tq.Query(
        since=tq.parse_when(args.since, now=now) if args.since else None, until=tq.parse_when(args.until, now=now) if args.until else None,
        version=args.version, tool=args.tool, status=args.status, corpus=args.corpus, endpoint=args.endpoint, code_tier=args.code_tier,
        security=args.security, fallback=args.fallback, has_code=args.has_code, unchecked=args.unchecked,
        min_cost_usd=args.min_cost, min_ms=args.min_ms,
        trace_id=getattr(args, "trace_prefix", None))


def _cmd_trace(args: argparse.Namespace) -> int:
    from . import tracequery as tq

    directory = Path(args.dir) if args.dir else EngineConfig.from_env().trace_dir
    try:
        if args.action == "show":
            spans = tq.load_trace(directory, args.trace_id)
            header, stages = tq.trace_header(spans), tq.stage_costs(spans)
            if args.json:
                print(json.dumps({"header": header, "stages": stages, "spans": spans}, indent=2, default=str))
                return 0
            print(f"trace {header['trace_id']}  started {header['started']}  outcome={header['outcome']}  "
                  f"{header['spans']} spans across {', '.join(header['services'])}"
                  + ("" if header["summarised"] else "  (legacy root: no request summary)"))
            print()
            print(tq.render_tree(spans))
            print("\nStages (times are inclusive; nested stages overlap):")
            for s in stages["stages"]:
                cost = "-" if s["api_cost_usd"] is None else f"${s['api_cost_usd']:.6f}"
                print(f"  {s['stage']:<30} {s['service']:<11} calls={s['calls']:<3} {s['total_ms']:>9.1f} ms  api_cost={cost:<11} {s['basis']}")
            total = "unavailable (an unpriced model was used)" if stages["api_cost_usd"] is None else f"${stages['api_cost_usd']:.6f}"
            print(f"API cost of this request: {total}")
            return 0
        scan = tq.scan_requests(directory)
        if args.action == "stats":
            chosen, notes = tq.select(scan.requests, _query_from(args))
            out = {"scanned": len(scan.requests), "selected": len(chosen), **tq.stats(chosen)}
            if args.bucket:
                out["timeline"] = tq.timeline(chosen, args.bucket)
            print(json.dumps(out, indent=2))
            return 0
        chosen, notes = tq.select(scan.requests, _query_from(args), costliest=args.costliest, slowest=args.slowest,
                                  outliers=args.outliers, limit=args.limit)
        if args.json:
            print(json.dumps({"scanned": len(scan.requests), "matched": len(chosen), "unreadable_lines": scan.unreadable_lines,
                              "legacy_roots": scan.legacy_roots, "notes": notes, "requests": tq.as_dicts(chosen)}, indent=2, default=str))
            return 0
        print(f"{len(chosen)} request(s) shown of {len(scan.requests)} scanned in {scan.path}"
              + (f"; {scan.unreadable_lines} unreadable line(s) skipped" if scan.unreadable_lines else "")
              + (f"; {scan.legacy_roots} legacy request(s) without a summary" if scan.legacy_roots else ""))
        for n in notes:
            print(f"note: {n}")
        print(tq.format_requests(chosen) if chosen else "no matching requests")
        if chosen and any(r.mcp_fallbacks or r.code_tiers or r.security_events or not r.summarised for r in chosen):
            print(tq.FLAG_LEGEND)
        return 0
    except tq.TraceQueryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _add_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--since", help="7d | 12h | 30m (relative to now, UTC) or 2026-11-03 / 2026-11-03T14:30")
    p.add_argument("--until", help="same forms as --since")
    p.add_argument("--version", help="the caller's SDK/API version, e.g. 2022-11-28")
    p.add_argument("--tool", help="an MCP tool the request called, e.g. check_symbol")
    p.add_argument("--status", help="outcome: answered | abstain | degraded | clarify | error")
    p.add_argument("--corpus", choices=["official", "upload"])
    p.add_argument("--endpoint", help="a catalog endpoint id the question was about, e.g. create_issue (see `versions`)")
    p.add_argument("--code-tier", dest="code_tier", help="a code-validation tier seen in the request, e.g. deprecated | not_checked | absent_per_docs")
    p.add_argument("--security", action="store_true", help="only requests with at least one security event")
    p.add_argument("--fallback", action="store_true", help="only requests where an MCP tool call failed and the in-process path served it")
    p.add_argument("--has-code", dest="has_code", action="store_true", help="only requests whose answer contained a code block")
    p.add_argument("--unchecked", action="store_true",
                   help="only requests whose code could NOT be checked (different from --code-tier deprecated, which means a deprecation was found and warned)")
    p.add_argument("--as-of", dest="as_of", help="measure relative times (7d, 12h) from this instant instead of now, so a query can be re-run exactly")
    p.add_argument("--min-cost", dest="min_cost", type=float, help="API cost at least this many USD (priced requests only)")
    p.add_argument("--min-ms", dest="min_ms", type=float, help="took at least this many milliseconds")


def _harden_streams() -> None:
    """Answers contain characters outside the Windows legacy code page (narrow no-break space, curly quotes, arrows). With stdout redirected to a
    file or pipe Python uses that code page and `print` raised UnicodeEncodeError (found by review). Replace what cannot be encoded instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass                                              # a stream that cannot be reconfigured (already closed, or a test double)


def main(argv: Optional[List[str]] = None) -> int:
    _load_dotenv()
    _harden_streams()
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        if argv and argv[0] == "eval":          # the integrated evaluation suite has its own sub-commands (sdk_eval/cli.py)
            from sdk_eval.cli import main as eval_main

            return eval_main(argv[1:])
        args = build_parser().parse_args(argv)
        return args.func(args)
    except ConfigError as exc:                  # a bad setting: say which one, exit 2, no traceback (the message never echoes a credential)
        print(f"error: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m sdk_engine", description="SDK documentation answer engine")
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("query", help="answer one question")
    q.add_argument("question")
    q.add_argument("--version", help="your SDK/API version, e.g. 2022-11-28 or 3.x (otherwise parsed from the question)")
    q.add_argument("--owner", help="your owner id: required to search YOUR uploaded documents (they are private)")
    q.add_argument("--scope", choices=["auto", "official", "upload"], default="auto",
                   help="official SDK docs, your uploads, or auto (decided from the wording; uploads need --owner)")
    q.add_argument("--no-llm", action="store_true", help="skip generation; return cited excerpts only")
    q.add_argument("--no-mcp", action="store_true", help="use the in-process retriever instead of the MCP tool server")
    q.add_argument("--json", action="store_true", help="print the full structured result")
    q.set_defaults(func=_cmd_query)

    v = sub.add_parser("versions", help="list documented versions and endpoints")
    v.set_defaults(func=_cmd_versions)

    h = sub.add_parser("health", help="check settings, index, catalog and LLM key presence")
    h.add_argument("--deep", action="store_true", help="also verify index integrity and the MCP server end to end (starts the server; no model call)")
    h.set_defaults(func=_cmd_health)

    ix = sub.add_parser("index", help="verify the official index against page_metadata.json, or rebuild it")
    ix.add_argument("action", choices=["check", "rebuild"], nargs="?", default="check")
    ix.add_argument("--yes", action="store_true", help="confirm a rebuild (it deletes and re-creates the collection)")
    ix.set_defaults(func=_cmd_index)

    ms = sub.add_parser("mcp-serve", help="run the MCP tool server on stdio")
    ms.set_defaults(func=_cmd_mcp_serve)

    ev = sub.add_parser("eval", help="the integrated evaluation suite: list | run | judge | label-sheet | agreement | regress | golden | trajectory | load | history "
                                     "(run `eval --help`)", add_help=False)
    ev.add_argument("rest", nargs=argparse.REMAINDER)
    ev.set_defaults(func=lambda a: __import__("sdk_eval.cli", fromlist=["main"]).main(a.rest))      # `main()` normally hands `eval` over before parsing; this keeps it in --help

    u = sub.add_parser("upload", help="upload a PDF/DOCX/MD/TXT file into your private collection")
    u.add_argument("file")
    u.add_argument("--owner", required=True)
    u.set_defaults(func=_cmd_upload)

    us = sub.add_parser("uploads", help="list / delete / purge your uploaded documents")
    us.add_argument("action", choices=["list", "delete", "purge"])
    us.add_argument("doc_id", nargs="?")
    us.add_argument("--owner", required=True)
    us.add_argument("--yes", action="store_true", help="confirm a purge")
    us.set_defaults(func=_cmd_uploads)

    t = sub.add_parser("trace", help="find a request in the span logs / inspect one trace")
    t.add_argument("--dir", help="trace directory (default: SDK_ENGINE_TRACE_DIR or developer-rag/data/traces)")
    tsub = t.add_subparsers(dest="action", required=True)
    tf = tsub.add_parser("find", help="list requests matching the filters (newest first)")
    _add_filters(tf)
    tf.add_argument("--costliest", type=int, metavar="N", help="the N most expensive matching requests")
    tf.add_argument("--slowest", type=int, metavar="N", help="the N slowest matching requests")
    tf.add_argument("--outliers", action="store_true", help="requests costing more than 3x the median priced request (needs >= 10 priced)")
    tf.add_argument("--limit", type=int, default=20, help="show at most this many (default 20)")
    tf.add_argument("--json", action="store_true")
    ts = tsub.add_parser("show", help="the span tree of one request, across the MCP hop, with a per-stage cost table")
    ts.add_argument("trace_id", help="a trace id, or a unique prefix of it")
    ts.add_argument("--json", action="store_true")
    tt = tsub.add_parser("stats", help="counts, latency and cost percentiles over the matching requests")
    _add_filters(tt)
    tt.add_argument("--bucket", choices=["hour", "day"], help="also print per-hour or per-day counts (UTC): how an outage or a spike shows up")
    for sp_ in (tf, ts, tt):
        sp_.set_defaults(func=_cmd_trace)
    # `trace_dir` may be given before or after the action
    for sp_ in (tf, ts, tt):
        sp_.add_argument("--dir", dest="dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    return p


if __name__ == "__main__":
    raise SystemExit(main())
