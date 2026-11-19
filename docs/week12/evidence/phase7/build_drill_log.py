"""Builds the SYNTHETIC rehearsal log for the incident drill: 400 background requests over 14 days + a planted MCP-outage window.
The ground truth (which trace is the planted one) is written to a SEPARATE file that the investigator is not given."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/docs/week12/evidence/phase7")
from make_synthetic_log import build, plant_incident

log_dir, truth_file = Path(sys.argv[1]), Path(sys.argv[2])
shutil.rmtree(log_dir, ignore_errors=True)
build(log_dir, 400, days=14, seed=11)
truth = plant_incident(log_dir, seed=11)
truth_file.write_text(json.dumps(truth, indent=1), encoding="utf-8")
print("built", log_dir, "| ground truth written to", truth_file.name, "(not shown to the investigator)")
