"""Deleting an upload must remove its TEXT from the files on disk, not only from search results.

Why this is its own file: measured against Chroma's persisted store (docs/week12/phase4.md), deleted text stayed in
chroma.sqlite3 after `delete`, and stale text residue showed up in its HNSW index files in 6 of 40 realistic
upload -> delete cycles (2 of 40 even after a later sweep). So uploads use a plain SQLite file with secure_delete + VACUUM,
and these tests scan EVERY byte of the store directory, repeatedly, for the deleted text.
"""

import uuid
from pathlib import Path

import pytest

from conftest import hash_embed
from sdk_engine.config import EngineConfig
from sdk_engine.sqlite_store import SqliteUploadClient
from sdk_engine.uploads import UploadStore

ALICE, BOB = "alice", "bob"


def many(texts):
    return [hash_embed(t) for t in texts]


def make(tmp_path: Path):
    d = tmp_path / "u"
    cfg = EngineConfig(uploads_path=d, upload_collection_prefix=f"t{uuid.uuid4().hex[:8]}_")
    return UploadStore(SqliteUploadClient(d / "uploads.sqlite3"), hash_embed, many, cfg), d


def where(store_dir: Path, marker: str):
    needle = marker.lower().encode()
    return [f.name for f in store_dir.rglob("*") if f.is_file() and needle in f.read_bytes().lower()]


def secret_doc(marker):
    return f"# Notes\n\nThe launch code is {marker}. Keep it private.\n".encode()


def test_control_the_scan_does_see_the_text_while_it_is_stored(tmp_path):
    """If the scan could not see stored text, every 'gone' assertion below would prove nothing."""
    store, d = make(tmp_path)
    store.ingest(ALICE, "secret.md", secret_doc("QWXZCONTROL1"))
    assert where(d, "QWXZCONTROL1") == ["uploads.sqlite3"]


def test_deleting_one_document_physically_removes_its_text_and_keeps_the_rest(tmp_path):
    store, d = make(tmp_path)
    secret = store.ingest(ALICE, "secret.md", secret_doc("QWXZDOC2"))
    keep = store.ingest(ALICE, "keep.md", b"# Keep\n\nOtters nest near the weir QWXZSURVIVOR.\n")
    res = store.delete(ALICE, secret.doc_id)
    assert res.chunks == secret.chunks and res.purged is True
    assert where(d, "QWXZDOC2") == []                                               # gone from every file
    assert where(d, "QWXZSURVIVOR") == ["uploads.sqlite3"]                          # the surviving document is untouched
    assert [x.doc_id for x in store.list(ALICE)] == [keep.doc_id]
    hit = store.search(ALICE, "otters weir", 3).passages[0]
    assert hit.source_name == "keep.md" and hit.owner_id and hit.dense_distance is not None   # searchable, scope stamp intact


def test_it_is_gone_in_every_one_of_many_independent_cycles(tmp_path):
    """The Chroma-backed version left the deleted text on disk in a share of cycles; the bar here is ZERO."""
    leaks = []
    for i in range(40):
        store, d = make(tmp_path / f"c{i}")
        dead = f"QWXZDEAD{i:03d}"
        s = store.ingest(ALICE, "secret.md", secret_doc(dead))
        store.ingest(ALICE, "keep.md", f"# Keep\n\nOtters nest near the weir QWXZLIVE{i:03d}.\n".encode())
        store.delete(ALICE, s.doc_id)
        if where(d, dead):
            leaks.append((i, where(d, dead)))
    assert leaks == []


def test_a_reopened_store_still_has_nothing(tmp_path):
    store, d = make(tmp_path)
    r = store.ingest(ALICE, "secret.md", secret_doc("QWXZREOPEN3"))
    store.delete(ALICE, r.doc_id)
    again = UploadStore(SqliteUploadClient(d / "uploads.sqlite3"), hash_embed, many, store._cfg)
    assert where(d, "QWXZREOPEN3") == [] and again.list(ALICE) == []


def test_delete_all_physically_removes_the_owners_text(tmp_path):
    store, d = make(tmp_path)
    store.ingest(ALICE, "a.md", secret_doc("QWXZALL4"))
    store.ingest(ALICE, "b.md", secret_doc("QWXZALL4B"))
    res = store.delete_all(ALICE)
    assert res.chunks >= 2 and res.purged is True
    assert where(d, "QWXZALL4") == [] and where(d, "QWXZALL4B") == []


def test_one_owners_delete_does_not_disturb_another_owner(tmp_path):
    store, d = make(tmp_path)
    store.ingest(ALICE, "a.md", secret_doc("QWXZALICE5"))
    bob = store.ingest(BOB, "b.md", secret_doc("QWXZBOB5"))
    store.delete_all(ALICE)
    assert where(d, "QWXZALICE5") == [] and where(d, "QWXZBOB5") == ["uploads.sqlite3"]
    assert [x.doc_id for x in store.list(BOB)] == [bob.doc_id] and store.search(BOB, "launch code", 2).passages


def test_no_journal_wal_or_temp_files_are_left_beside_the_database(tmp_path):
    store, d = make(tmp_path)
    r = store.ingest(ALICE, "secret.md", secret_doc("QWXZFILES6"))
    store.delete(ALICE, r.doc_id)
    assert sorted(f.name for f in d.rglob("*") if f.is_file()) == ["uploads.sqlite3"]    # nothing that could keep a copy


def test_a_client_that_cannot_compact_says_so_instead_of_claiming_a_purge(tmp_path):
    import chromadb

    store = UploadStore(chromadb.EphemeralClient(), hash_embed, many, EngineConfig(upload_collection_prefix=f"t{uuid.uuid4().hex[:8]}_"))
    r = store.ingest(ALICE, "a.md", secret_doc("QWXZMEM7"))
    res = store.delete(ALICE, r.doc_id)
    assert res.chunks >= 1 and res.purged is False            # logical delete only; the result must not pretend otherwise


def test_pdf_provenance_survives_other_documents_being_deleted(tmp_path):
    import filefactory as ff

    store, d = make(tmp_path)
    pdf = store.ingest(ALICE, "guide.pdf", ff.make_pdf(["page one lions QWXZP1", "page two zebras QWXZP2"]))
    gone = store.ingest(ALICE, "gone.md", secret_doc("QWXZGONE8"))
    store.delete(ALICE, gone.doc_id)
    top = store.search(ALICE, "zebras", 3).passages[0]
    assert top.source_name == "guide.pdf" and top.page == 2 and top.chunk_id.startswith(f"upl:{pdf.doc_id}:p2:")
    assert store.list(ALICE)[0].pages == 2 and where(d, "QWXZGONE8") == []
