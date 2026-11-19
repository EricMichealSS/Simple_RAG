"""False-positive scan: run the injection scanner over REAL, benign documentation. Every flag here is a false positive."""
import sys
from pathlib import Path

ROOT = Path(r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
sys.path.insert(0, str(ROOT))
from pypdf import PdfReader
from sdk_engine.config import EngineConfig
from sdk_engine.injection import scan_text
from sdk_engine.retrieval import open_collection

texts = []
col = open_collection(EngineConfig.from_env())
got = col.get(include=["documents"])
texts += [("race_docs chunk " + i, d) for i, d in zip(got["ids"], got["documents"])]
for md in sorted((ROOT / "documents/v3-sdk").glob("*.md")):
    texts.append((f"v3-sdk/{md.name}", md.read_text(encoding="utf-8")))
for pdf in sorted((ROOT / "documents").glob("*.pdf")):
    r = PdfReader(str(pdf))
    for n, page in enumerate(r.pages, 1):
        texts.append((f"{pdf.name} p{n}", page.extract_text() or ""))

total_chars = sum(len(t) for _, t in texts)
hits = []
for name, t in texts:
    # scan in 3000-char windows (as chunks would be) so the cap does not hide anything in long pages
    for i in range(0, max(len(t), 1), 3000):
        for f in scan_text(t[i:i + 3000]):
            hits.append((name, f.pattern, f.severity, f.excerpt))
print(f"scanned {len(texts)} text units, {total_chars:,} characters")
print(f"flags (all false positives): {len(hits)}")
for h in hits[:40]:
    print("  ", h)
