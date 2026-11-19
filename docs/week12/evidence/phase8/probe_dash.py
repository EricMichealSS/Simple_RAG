import sys

sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
from sdk_engine import answering
from sdk_engine.config import EngineConfig
from sdk_engine.schemas import Passage
from sdk_engine.versioning import VersionCatalog, mentioned_versions, resolve_version

cfg = EngineConfig.from_env()
cat = VersionCatalog.from_page_metadata(cfg.page_metadata_path)
for name, ch in [("ASCII hyphen", "-"), ("U+2010 hyphen", "\u2010"), ("U+2011 non-breaking hyphen", "\u2011"), ("U+2012 figure dash", "\u2012"),
                 ("U+2013 en dash", "\u2013"), ("U+2212 minus", "\u2212")]:
    d = f"2025{ch}06{ch}01"
    m = mentioned_versions(f"In {d} use assignees", cat)
    r = resolve_version(f"On API version {d}, how do I create an issue?", catalog=cat, explicit=None, config_defaults={})
    p = [Passage(chunk_id="a:p1:spec:x:0", text="t", source_name="s", api_version="2022-11-28")]
    ok, fails, _ = answering.verify_answer(f"Use it [a:p1:spec:x:0]. In {d} the field is assignees.", p, ["2022-11-28"], cat)
    print(f"{name:28} mentioned={m!s:16} question resolves to: {r.status.value}/{r.version} | a 2022-11-28 answer claiming {d!r:14} -> verify ok={ok}")
