import sys

sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
from sdk_engine import answering
from sdk_engine.config import EngineConfig
from sdk_engine.schemas import Passage
from sdk_engine.versioning import VersionCatalog, mentioned_versions

cfg = EngineConfig.from_env()
cat = VersionCatalog.from_page_metadata(cfg.page_metadata_path)
cid = "github_combined_reference:p12:spec:top:15"
pas = [Passage(chunk_id=cid, text="Create a release tag_name make_latest", source_name="s", api_version="2025-06-01")]
for label, body in [
    ("release example tag v2.0.0", '```json\n{"tag_name": "v2.0.0", "make_latest": "true"}\n```'),
    ("release example tag v3.1.4", '```json\n{"tag_name": "3.1.4"}\n```'),
    ("release example tag v1.0.0", '```json\n{"tag_name": "v1.0.0"}\n```'),
    ("prose: unlike Swagger 2.x", "Unlike Swagger Codegen 2.x this is REST."),
    ("header with the OTHER github date", '```bash\ncurl -H "X-GitHub-Api-Version: 2022-11-28" https://api.github.com\n```'),
]:
    ans = f"Use it [{cid}].\n{body}"
    ok, failures, _ = answering.verify_answer(ans, pas, ["2025-06-01"], cat)
    print(f"{label:38} mentioned={mentioned_versions(ans, cat)} ok={ok} failures={failures}")
