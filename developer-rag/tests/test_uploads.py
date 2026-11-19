"""User uploads: validation, extraction provenance, dedup, private per-owner scope, list/delete, limits."""

import hashlib
import json
import time
import uuid

import chromadb
import pytest

import filefactory as ff
from conftest import LeakyCollection, hash_embed
from sdk_engine.config import EngineConfig
from sdk_engine.extract import UploadError, _with_timeout, extract, file_extension, sanitize_filename, validate_content
from sdk_engine.retrieval import ChromaRetriever, RetrievalScope, ScopeViolation, owner_key
from sdk_engine.uploads import UploadStore

ALICE, BOB = "alice@example.com", "bob"


def many(texts):
    return [hash_embed(t) for t in texts]


def make_store(**cfg):
    # chromadb's ephemeral client is process-wide and upload collection names are deterministic per owner, so each
    # test store gets its own prefix; otherwise tests would see each other's documents.
    client = chromadb.EphemeralClient()
    cfg.setdefault("upload_collection_prefix", f"t{uuid.uuid4().hex[:8]}_")
    return UploadStore(client, hash_embed, many, EngineConfig(**cfg)), client


@pytest.fixture()
def store():
    return make_store()[0]


def category(fn, *a, **k):
    with pytest.raises(UploadError) as e:
        fn(*a, **k)
    return e.value.category


ZEBRA = b"# Zoo notes\n\nThe zebra enclosure was repainted in March. Zebras prefer shade.\n"
OTTER = b"# River notes\n\nOtters nest near the weir and eat crayfish.\n"


# ------------------------------------------------------------------ filenames are display-only and reduced to a basename
@pytest.mark.parametrize("raw, clean", [
    ("../../etc/passwd.md", "passwd.md"), ("C:\\Users\\x\\evil.pdf", "evil.pdf"), ("a/b\\c.txt", "c.txt"),
    ("..hidden.md", "hidden.md"), ("a\x00b.md", "ab.md"), ("we!rd$name.md", "we_rd_name.md"),
    ("my file (1).MD", "my file (1).md"), ("tab\tname.md", "tabname.md"), ("x" * 300 + ".txt", "x" * 100 + ".txt"),
    ("invoice\u202efdp.docx", "invoicefdp.docx"),   # bidi override removed: the displayed name matches the real extension
])
def test_filenames_are_sanitised(raw, clean):
    assert sanitize_filename(raw) == clean


@pytest.mark.parametrize("raw", [None, "", "   ", ".md", "noextension", 123, "...", "/"])
def test_unusable_filenames_are_rejected(raw):
    assert category(sanitize_filename, raw) == "bad_filename"


@pytest.mark.parametrize("name", ["a.exe", "a.docm", "a.doc", "a.rtf", "a.html", "a.zip", "a.py", "a.js", "a.sh", "a.svg"])
def test_unsupported_types_are_rejected_by_extension(name):
    assert category(file_extension, sanitize_filename(name)) == "unsupported_type"


# ------------------------------------------------------------------ the bytes decide, not the extension
def test_content_must_match_the_claimed_type(store):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    assert category(store.ingest, ALICE, "fake.pdf", png) == "content_mismatch"
    assert category(store.ingest, ALICE, "fake.docx", b"plain text pretending to be word") == "content_mismatch"
    assert category(store.ingest, ALICE, "fake.docx", ff.zip_without_document_xml()) == "content_mismatch"
    assert category(store.ingest, ALICE, "fake.md", b"abc\x00def") == "not_text"
    assert category(store.ingest, ALICE, "fake.txt", "caf\xe9".encode("latin-1")) == "not_text"
    assert category(store.ingest, ALICE, "fake.md", bytes(range(1, 9)) * 100) == "not_text"


def test_empty_and_oversized_files_are_rejected():
    s, _ = make_store(max_upload_bytes=1000)
    assert category(s.ingest, ALICE, "a.md", b"") == "empty_file"
    assert category(s.ingest, ALICE, "a.md", b"x" * 1001) == "file_too_large"


def test_docx_with_macros_is_rejected(store):
    assert category(store.ingest, ALICE, "m.docx", ff.with_macro(ff.make_docx([("p", "hello")]))) == "macros_not_supported"


def test_zip_bomb_docx_is_rejected_before_it_is_parsed():
    s, _ = make_store(max_upload_bytes=50_000_000)
    bomb = ff.with_zip_bomb(ff.make_docx([("p", "hello")]), megabytes=60)
    assert len(bomb) < 1_000_000                       # tiny on the wire ...
    assert category(s.ingest, ALICE, "b.docx", bomb) == "archive_too_large"   # ... huge once expanded


def test_docx_with_traversal_member_names_is_rejected(store):
    assert category(store.ingest, ALICE, "t.docx", ff.with_traversal_member(ff.make_docx([("p", "hello")]))) == "unsafe_archive"


def test_pdf_limits_and_failure_modes():
    s, _ = make_store(max_pdf_pages=2)
    assert category(s.ingest, ALICE, "e.pdf", ff.make_encrypted_pdf(["secret page"])) == "encrypted"
    assert category(s.ingest, ALICE, "many.pdf", ff.make_pdf(["one", "two", "three"])) == "too_many_pages"
    assert category(s.ingest, ALICE, "junk.pdf", b"%PDF-1.4\n" + b"not a real pdf body" * 20) == "unreadable"
    assert category(s.ingest, ALICE, "blank.pdf", ff.make_pdf(["", ""])) == "no_extractable_text"


def test_too_much_text_is_rejected():
    s, _ = make_store(max_extracted_chars=1000)
    assert category(s.ingest, ALICE, "big.md", b"word " * 500) == "too_much_text"


def test_a_parser_that_hangs_is_abandoned_with_a_timeout():
    assert category(_with_timeout, lambda: time.sleep(2), 0.05) == "parse_timeout"


# ------------------------------------------------------------------ extraction keeps provenance
def test_pdf_pages_are_kept_so_citations_can_name_the_page(store):
    data = ff.make_pdf(["alpha page one about zebras and shade", "beta page two about otters and the weir"])
    res = store.ingest(ALICE, "Field Guide.pdf", data)
    assert res.status == "indexed" and res.pages == 2 and res.file_type == "pdf" and res.filename == "Field Guide.pdf"
    top = store.search(ALICE, "otters weir", 3).passages[0]
    assert top.page == 2 and top.source_name == "Field Guide.pdf" and top.chunk_id.startswith(f"upl:{res.doc_id}:p2:")
    assert top.location.startswith("Field Guide.pdf p.2") and top.corpus == "upload" and top.api_version is None
    assert store.search(ALICE, "zebras shade", 3).passages[0].page == 1


def test_docx_headings_become_sections_and_tables_stay_whole(store):
    data = ff.make_docx([("h1", "Pricing"), ("p", "The starter plan is cheap."), ("table", [["plan", "price"], ["starter", "5"], ["pro", "50"]]),
                         ("h1", "Support"), ("p", "Email us any time.")])
    res = store.ingest(ALICE, "plans.docx", data)
    assert res.status == "indexed" and res.pages is None
    passages = store.search(ALICE, "starter plan price", 5).passages
    pricing = next(p for p in passages if p.anchor == "pricing")
    assert "| starter | 5 |" in pricing.text and "| pro | 50 |" in pricing.text   # the table was not split
    assert pricing.page is None and pricing.location == "plans.docx #pricing"


def test_markdown_and_text_are_supported_including_bom_and_crlf(store):
    r1 = store.ingest(ALICE, "notes.md", b"\xef\xbb\xbf# Title\r\n\r\nzebra text here\r\n")
    r2 = store.ingest(ALICE, "plain.txt", b"otter text here\r\nsecond line\r\n")
    assert r1.status == r2.status == "indexed"
    assert "\r" not in store.search(ALICE, "zebra", 1).passages[0].text and "\ufeff" not in store.search(ALICE, "zebra", 1).passages[0].text


# ------------------------------------------------------------------ content-hash dedup (per owner)
def test_identical_content_is_a_noop_regardless_of_filename(store):
    first = store.ingest(ALICE, "zoo.md", ZEBRA)
    again = store.ingest(ALICE, "renamed.md", ZEBRA)
    assert (first.status, again.status) == ("indexed", "duplicate")
    assert again.doc_id == first.doc_id == hashlib.sha256(ZEBRA).hexdigest()[:16] and again.filename == "zoo.md"
    assert len(store.list(ALICE)) == 1 and store.list(ALICE)[0].chunks == first.chunks


def test_same_name_different_content_is_a_new_document(store):
    store.ingest(ALICE, "zoo.md", ZEBRA)
    assert store.ingest(ALICE, "zoo.md", ZEBRA + b"\nMore.\n").status == "indexed"
    assert len(store.list(ALICE)) == 2


# ------------------------------------------------------------------ private scope: nobody sees anybody else's upload
def test_owners_cannot_see_each_others_documents(store):
    store.ingest(ALICE, "zoo.md", ZEBRA)
    assert store.search(BOB, "zebra enclosure", 5).passages == []                 # Bob has no uploads at all
    store.ingest(BOB, "river.md", OTTER)
    assert store.search(BOB, "zebra enclosure", 5).passages == [] or all("zebra" not in p.text.lower() for p in store.search(BOB, "zebra enclosure", 5).passages)
    assert {p.source_name for p in store.search(BOB, "otters crayfish zebra", 5).passages} == {"river.md"}
    assert {p.source_name for p in store.search(ALICE, "otters crayfish zebra", 5).passages} == {"zoo.md"}


def test_identical_files_from_two_owners_do_not_reveal_each_other(store):
    a = store.ingest(ALICE, "zoo.md", ZEBRA)
    b = store.ingest(BOB, "zoo.md", ZEBRA)
    assert a.status == "indexed" and b.status == "indexed"        # NOT "duplicate": dedup never looks across owners
    assert [d.doc_id for d in store.list(BOB)] == [b.doc_id]


def test_the_raw_owner_id_is_never_stored(store):
    store.ingest(ALICE, "zoo.md", ZEBRA)
    client = store._client
    # chromadb's ephemeral client is process-wide: look ONLY at this store's collections (other tests upload files with their own names)
    mine = [c.name for c in client.list_collections() if c.name.startswith(store._cfg.upload_collection_prefix)]
    assert mine, "the store should have created at least one collection"
    blob = json.dumps({"names": mine,
                       "meta": [m for n in mine for m in client.get_collection(n).get(include=["metadatas"])["metadatas"]]})
    assert "alice" not in blob and "example.com" not in blob and owner_key(ALICE) in blob


def test_uploads_and_the_official_corpus_never_mix(synth_collection):
    client = chromadb.EphemeralClient()   # process-wide: it sees the official fixture collection as well
    s = UploadStore(client, hash_embed, many, EngineConfig(official_collection=synth_collection.name,
                                                           upload_collection_prefix=f"t{uuid.uuid4().hex[:8]}_"))
    before = synth_collection.count()
    s.ingest(ALICE, "zoo.md", ZEBRA)
    assert synth_collection.count() == before                                                       # official corpus untouched
    official = ChromaRetriever(synth_collection, hash_embed).search("zebra enclosure", RetrievalScope(versions=("2022-11-28",)), 9)
    assert all(p.corpus == "official" and "zebra" not in p.text.lower() for p in official.passages)
    mine = s.search(ALICE, "create an issue assignee", 9).passages
    assert all(p.corpus == "upload" for p in mine)                                                  # and never official text
    assert s._name(ALICE) != synth_collection.name


@pytest.mark.parametrize("owner", ["", "../x", "a b", "x" * 65, "a;b", "o'brien", None, 5])
def test_owner_ids_are_validated(store, owner):
    assert category(store.ingest, owner, "a.md", ZEBRA) == "bad_owner"
    with pytest.raises(UploadError):
        store.search(owner, "zebra")


def test_a_store_with_a_broken_owner_filter_is_caught_by_the_post_check():
    s, client = make_store()
    s.ingest(ALICE, "zoo.md", ZEBRA)
    s.ingest(BOB, "river.md", OTTER)
    # simulate a mixed collection whose filter is broken: Bob's chunks sitting where Alice's query runs
    col = client.get_collection(s._name(ALICE))
    bob_col = client.get_collection(s._name(BOB))
    got = bob_col.get(include=["documents", "metadatas"])
    col.add(ids=["x:" + i for i in got["ids"]], documents=got["documents"], metadatas=got["metadatas"], embeddings=many(got["documents"]))
    with pytest.raises(ScopeViolation):
        ChromaRetriever(LeakyCollection(col), hash_embed).search("otters", RetrievalScope(corpus="upload", owner_id=ALICE), 5)


# ------------------------------------------------------------------ list / delete
def test_list_reports_provenance(store):
    store.ingest(ALICE, "zoo.md", ZEBRA)
    store.ingest(ALICE, "guide.pdf", ff.make_pdf(["page one text", "page two text"]))
    docs = {d.filename: d for d in store.list(ALICE)}
    assert set(docs) == {"zoo.md", "guide.pdf"} and docs["guide.pdf"].pages == 2 and docs["guide.pdf"].file_type == "pdf"
    assert all(len(d.doc_id) == 16 and len(d.content_hash) == 64 and d.uploaded_at and d.chunks >= 1 for d in docs.values())
    assert store.list(BOB) == []


def test_delete_removes_the_documents_chunks_and_nothing_else(store):
    z = store.ingest(ALICE, "zoo.md", ZEBRA)
    o = store.ingest(ALICE, "river.md", OTTER)
    removed = store.delete(ALICE, z.doc_id)
    assert removed.chunks == z.chunks and removed.purged is False and [d.doc_id for d in store.list(ALICE)] == [o.doc_id]
    assert all("zebra" not in p.text.lower() for p in store.search(ALICE, "zebra enclosure", 9).passages)
    assert store.search(ALICE, "otters", 3).passages


def test_one_owner_cannot_delete_another_owners_document(store):
    a = store.ingest(ALICE, "zoo.md", ZEBRA)
    store.ingest(BOB, "river.md", OTTER)
    assert category(store.delete, BOB, a.doc_id) == "not_found"          # Bob's collection has no such doc
    assert [d.doc_id for d in store.list(ALICE)] == [a.doc_id]            # Alice's is intact
    assert category(store.delete, BOB, "../../etc") == "bad_doc_id"
    assert category(store.delete, "nobody", a.doc_id) == "not_found"


def test_delete_all_drops_the_owners_collection(store):
    store.ingest(ALICE, "zoo.md", ZEBRA)
    store.ingest(ALICE, "river.md", OTTER)
    assert store.delete_all(ALICE).chunks >= 2 and store.list(ALICE) == [] and store.search(ALICE, "zebra").passages == []
    assert store.delete_all(ALICE).chunks == 0


# ------------------------------------------------------------------ quotas, atomicity
def test_per_owner_document_quota():
    s, _ = make_store(max_docs_per_owner=2)
    s.ingest(ALICE, "a.md", b"first doc " * 5)
    s.ingest(ALICE, "b.md", b"second doc " * 5)
    assert category(s.ingest, ALICE, "c.md", b"third doc " * 5) == "quota_exceeded"
    assert s.ingest(BOB, "c.md", b"third doc " * 5).status == "indexed"          # quota is per owner


def test_a_rejected_document_leaves_nothing_behind():
    s, client = make_store(max_chunks_per_doc=2)
    s.ingest(ALICE, "ok.md", b"# One\n\nshort\n")
    before = client.get_collection(s._name(ALICE)).count()
    many_sections = "".join(f"# S{i}\n\nbody {i}\n\n" for i in range(10)).encode()
    assert category(s.ingest, ALICE, "big.md", many_sections) == "too_many_chunks"
    assert client.get_collection(s._name(ALICE)).count() == before


def test_search_with_no_uploads_is_empty_not_an_error(store):
    res = store.search("brand-new-owner", "anything")
    assert res.passages == [] and res.stats["no_uploads"]
