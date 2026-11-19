"""The SQLite vector store must behave like the (small) part of Chroma the upload code relies on, and fail closed."""

import threading

import numpy as np
import pytest

from sdk_engine.sqlite_store import SqliteUploadClient


@pytest.fixture()
def client(tmp_path):
    return SqliteUploadClient(tmp_path / "s" / "u.sqlite3")


def vec(*xs):
    v = np.asarray(xs, dtype=np.float32)
    return (v / np.linalg.norm(v)).tolist()


def seed(col):
    col.add(ids=["a", "b", "c"], documents=["alpha", "beta", "gamma"],
            metadatas=[{"owner_id": "o1", "doc_id": "d1", "n": 1}, {"owner_id": "o1", "doc_id": "d2"}, {"owner_id": "o2", "doc_id": "d3"}],
            embeddings=[vec(1, 0, 0), vec(0, 1, 0), vec(1, 1, 0)])


def test_add_get_count_and_metadata_round_trip(client):
    col = client.get_or_create_collection("c1", {"hnsw:space": "cosine"})
    seed(col)
    assert col.count() == 3
    got = col.get(include=["documents", "metadatas", "embeddings"])
    assert got["ids"] == ["a", "b", "c"] and got["documents"] == ["alpha", "beta", "gamma"]
    assert got["metadatas"][0] == {"owner_id": "o1", "doc_id": "d1", "n": 1}
    assert np.allclose(got["embeddings"][2], vec(1, 1, 0), atol=1e-6)
    assert col.get(ids=["b"])["documents"] == ["beta"] and col.get(limit=2)["ids"] == ["a", "b"]


def test_where_filters_are_equality_on_owner_and_doc_only(client):
    col = client.get_or_create_collection("c1")
    seed(col)
    assert col.get(where={"owner_id": "o1"})["ids"] == ["a", "b"]
    assert col.get(where={"doc_id": "d3"})["ids"] == ["c"]
    assert col.get(where={"$and": [{"owner_id": "o1"}, {"doc_id": "d2"}]})["ids"] == ["b"]
    assert col.get(where={"owner_id": "nobody"})["ids"] == []


@pytest.mark.parametrize("bad", [
    {"api_version": "2022-11-28"},                 # unknown key: must not be silently ignored
    {"owner_id": {"$in": ["o1"]}},                 # operators are not supported
    {"owner_id": "o1", "doc_id": "d1"},            # two keys without $and
    {"$or": [{"owner_id": "o1"}]}, {"owner_id": 5},
])
def test_unsupported_filters_raise_instead_of_being_ignored(client, bad):
    col = client.get_or_create_collection("c1")
    seed(col)
    with pytest.raises(ValueError):
        col.get(where=bad)
    with pytest.raises(ValueError):
        col.query(query_embeddings=[vec(1, 0, 0)], n_results=2, where=bad)


def test_query_returns_cosine_distance_like_chroma_sorted_ascending(client):
    col = client.get_or_create_collection("c1")
    seed(col)
    r = col.query(query_embeddings=[vec(1, 0, 0)], n_results=3)
    assert r["ids"][0] == ["a", "c", "b"]
    d = r["distances"][0]
    assert d[0] == pytest.approx(0.0, abs=1e-6) and d[1] == pytest.approx(1 - 2 ** -0.5, abs=1e-5) and d[2] == pytest.approx(1.0, abs=1e-6)
    assert r["documents"][0] == ["alpha", "gamma", "beta"] and r["metadatas"][0][0]["doc_id"] == "d1"


def test_query_respects_where_and_n_results_and_empty_pools(client):
    col = client.get_or_create_collection("c1")
    seed(col)
    assert col.query(query_embeddings=[vec(1, 0, 0)], n_results=5, where={"owner_id": "o2"})["ids"][0] == ["c"]
    assert col.query(query_embeddings=[vec(1, 0, 0)], n_results=1)["ids"][0] == ["a"]
    empty = col.query(query_embeddings=[vec(1, 0, 0)], n_results=3, where={"owner_id": "nobody"})
    assert empty["ids"] == [[]] and empty["distances"] == [[]]


def test_delete_by_ids_and_by_where_and_the_guard_against_deleting_everything(client):
    col = client.get_or_create_collection("c1")
    seed(col)
    col.delete(ids=["a"])
    assert col.count() == 2
    col.delete(where={"owner_id": "o2"})
    assert col.get()["ids"] == ["b"]
    col.delete(ids=[])                       # no-op, not "delete all"
    assert col.count() == 1
    with pytest.raises(ValueError):
        col.delete()                         # must name something to delete


def test_collections_are_isolated_and_can_be_dropped(client):
    a, b = client.get_or_create_collection("a"), client.get_or_create_collection("b")
    a.add(ids=["x"], documents=["in a"], metadatas=[{}], embeddings=[vec(1, 0)])
    b.add(ids=["x"], documents=["in b"], metadatas=[{}], embeddings=[vec(1, 0)])      # same id, different collection: allowed
    assert a.get()["documents"] == ["in a"] and b.get()["documents"] == ["in b"]
    client.delete_collection("a")
    assert b.count() == 1 and [c.name for c in client.list_collections()] == ["b"]
    with pytest.raises(ValueError):
        client.get_collection("a")


@pytest.mark.parametrize("name", ["", "a b", "../x", "a;drop table chunks", "x" * 101, "ü"])
def test_collection_names_are_validated(client, name):
    with pytest.raises(ValueError):
        client.get_or_create_collection(name)


def test_sql_metacharacters_in_data_are_stored_as_data(client):
    col = client.get_or_create_collection("c1")
    nasty = "x'); DROP TABLE chunks; --"
    col.add(ids=[nasty], documents=[nasty], metadatas=[{"owner_id": nasty, "doc_id": "d"}], embeddings=[vec(1, 0)])
    assert col.get(where={"owner_id": nasty})["ids"] == [nasty] and col.count() == 1
    col.delete(ids=[nasty])
    assert col.count() == 0 and [c.name for c in client.list_collections()] == ["c1"]


def test_data_persists_across_client_instances_and_is_visible_to_a_second_client(client, tmp_path):
    col = client.get_or_create_collection("c1")
    seed(col)
    other = SqliteUploadClient(tmp_path / "s" / "u.sqlite3")        # a second client on the same file (e.g. another process)
    assert other.get_collection("c1").count() == 3
    other.get_collection("c1").delete(ids=["a"])
    assert col.count() == 2


def test_mismatched_lengths_are_rejected(client):
    col = client.get_or_create_collection("c1")
    with pytest.raises(ValueError):
        col.add(ids=["a", "b"], documents=["x"], metadatas=[{}], embeddings=[vec(1, 0)])


def test_concurrent_writers_do_not_corrupt_the_store(client):
    col = client.get_or_create_collection("c1")
    errors = []

    def work(k):
        try:
            for j in range(10):
                col.add(ids=[f"{k}-{j}"], documents=["t"], metadatas=[{"owner_id": str(k)}], embeddings=[vec(1, 0)])
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    ts = [threading.Thread(target=work, args=(k,)) for k in range(6)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert not errors and col.count() == 60


def test_compact_runs_and_reports_success(client):
    col = client.get_or_create_collection("c1")
    seed(col)
    col.delete(ids=["a"])
    assert client.compact() is True and col.count() == 2
