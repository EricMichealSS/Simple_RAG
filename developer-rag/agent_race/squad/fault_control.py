"""Test-only helper to flip fault injection for the MCP-based squad.

workers.py (the original, in-process squad) kept fault state in a plain
Python dict (`FAULT` in workers.py) because the manager and the workers
ran inside the SAME process -- a shared global was enough.

squad_tools_server.py runs as its OWN operating-system process (spawned by
orchestrator_mcp.py over stdio), so an in-memory dict in this file would
NOT be visible to it -- two separate processes never share Python memory.
The fix follows the same pattern already used elsewhere in this project for
anything that must survive or cross a process boundary (race_runs.json,
traces.jsonl): a small flat JSON file on disk. squad_tools_server.py reads
this file fresh on every worker call.
"""

import json
from pathlib import Path

FAULT_FILE = Path(__file__).parent / "fault_state.json"


def set_fault(worker: str = None, mode: str = None):
    """mode: 'persistent' = every call to `worker` returns 500;
             'transient'  = only the first call returns 500.
    Call with no arguments (or worker=None) to clear the fault."""
    if worker is None:
        if FAULT_FILE.exists():
            FAULT_FILE.unlink()
        return
    FAULT_FILE.write_text(json.dumps({"worker": worker, "mode": mode}), encoding="utf-8")


def clear_fault():
    set_fault(None)
