"""`eval trajectory` (re-score from a saved run, no model) and `eval history` (one line per saved run)."""

import json

import pytest

from sdk_eval import cli, trajectory as T
from sdk_eval.trajectory import Call, SPECS, score


def _row(qid, calls, outcome="answered", passed=True):
    return {"id": qid, "outcome": outcome, "score": score(SPECS[qid], calls, outcome=outcome, outcome_pass=passed)}


def test_trajectory_rescoring_prints_a_row_per_question_and_writes_the_summary(tmp_path, capsys):
    rows = [_row("easy_01", [Call("search_docs", True, T.V25), Call("lookup_changes", True, T.V25)]),
            _row("easy_02", [Call("search_docs", True, T.S3), Call("lookup_changes", True, T.S3)]),
            {"id": "easy_03", "outcome": "answered"}]                                  # not a trajectory question: ignored
    path = tmp_path / "golden_results.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert cli.main(["trajectory", "--results", str(path)]) == 0
    out = capsys.readouterr().out
    assert "easy_01" in out and "easy_02" in out and "easy_03" not in out
    written = json.loads((tmp_path / "trajectory_summary.json").read_text(encoding="utf-8"))
    assert written["summary"]["questions"] == 2 and written["summary"]["unnecessary_calls"] == 1      # the changelog call for easy_02
    assert "hard_01" in written["missing"]                                                           # the figures say which spec'd questions were NOT run


def test_trajectory_rescoring_refuses_a_file_without_scored_rows(tmp_path):
    path = tmp_path / "g.json"
    path.write_text(json.dumps([{"id": "easy_03", "outcome": "answered"}]), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        cli.main(["trajectory", "--results", str(path)])
    assert "no row" in str(e.value)


def test_history_lists_each_kind_of_run_oldest_first_and_tolerates_an_unreadable_one(tmp_path, capsys):
    suite = tmp_path / "run_20261101T000000Z"
    suite.mkdir()
    (suite / "summary.json").write_text(json.dumps({"cases": 40, "executed": 40, "status": {"pass": 40}, "pass_rate_executed": 1.0}), encoding="utf-8")
    (suite / "results.json").write_text(json.dumps({"meta": {"started": "2026-11-01T00:00:00+00:00", "live": True}, "results": []}), encoding="utf-8")
    gold = tmp_path / "run_20261102T000000Z"
    gold.mkdir()
    (gold / "golden_results.json").write_text(json.dumps([{"outcome": "answered"}, {"outcome": "abstain"}, {"outcome": "answered"}]), encoding="utf-8")
    load = tmp_path / "run_20261103T000000Z"
    load.mkdir()
    (load / "load_results.json").write_text(json.dumps([{"concurrency": 1, "failures": 0}, {"concurrency": 4, "failures": 0}]), encoding="utf-8")
    bad = tmp_path / "run_20261104T000000Z"
    bad.mkdir()
    (bad / "golden_results.json").write_text("not json", encoding="utf-8")
    assert cli.main(["history", "--dir", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [ln.split()[0] for ln in lines] == sorted(ln.split()[0] for ln in lines) and len(lines) == 4
    assert "suite" in lines[0] and "pass_rate=1.0" in lines[0] and "golden" in lines[1] and "'answered': 2" in lines[1]
    assert "load" in lines[2] and "unreadable" in lines[3]


def test_history_of_a_missing_directory_is_a_message_not_a_crash(tmp_path, capsys):
    assert cli.main(["history", "--dir", str(tmp_path / "nope")]) == 0
    assert "no runs yet" in capsys.readouterr().out
