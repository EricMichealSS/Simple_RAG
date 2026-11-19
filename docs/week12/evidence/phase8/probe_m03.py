import sys
from pathlib import Path

sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
from sdk_engine.retrieval import RetrievalScope
from sdk_eval.harness import Env

env = Env(Path(r"C:/Users/ERICMI~1/AppData/Local/Temp/claude/C--Projects-AI-Learning-Simple-RAG/e3881073-bc5d-46d8-a353-4c8e72ce7215/scratchpad/probe_traces"), live=False)
q = "On API version 2022-11-28, show the JSON body to create an issue."
out = env.retriever().search(q, RetrievalScope(versions=("2022-11-28",)), top_k=5)
print("retrieved:", [(p.chunk_id.split(":", 1)[1], p.endpoint) for p in out.passages])
text = " ".join(p.text for p in out.passages)
for sym, ep in [("target_commitish", "create_issue"), ("prerelease", "create_issue"), ("tag_name", "create_issue"), ("has_issues", "create_issue"),
                ("sortParamsByRequiredFlag", None), ("maintainer_can_modify", "create_issue"), ("draft", "create_issue"), ("homepage", "create_issue")]:
    f = env.symbols.check("2022-11-28", sym, ep)
    f2 = env.symbols.check("2022-11-28", sym, None)
    print(f"{sym:26} in retrieved text: {sym in text!s:5} | scoped: {f.status:15} | version-wide: {f2.status}")
env.close()
