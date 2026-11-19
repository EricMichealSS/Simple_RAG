import sys

sys.path.insert(0, r"C:/Projects/AI Learning/Simple_RAG/developer-rag")
from sdk_engine.answering import output_safety_failures
from sdk_engine.redaction import find_secrets
from sdk_engine.schemas import Passage

TOKEN = "ghp_" + "ExampleFakeTokenNotReal0123456789ab"
question = f"My token is {TOKEN}. How do I create an issue? Show a curl example."
p = [Passage(chunk_id="a:p1:spec:x:0", text="Create an issue", source_name="s", api_version="2022-11-28")]
for label, answer in [
    ("token verbatim in a header value", f'curl -H "Authorization: token {TOKEN}" https://api.github.com'),
    ("Bearer + the user's token", f'curl -H "Authorization: Bearer {TOKEN}" https://api.github.com'),
    ("bare token", f"Use {TOKEN} as the token."),
    ("different invented token", 'curl -H "Authorization: Bearer ghp_' + "Z" * 36 + '" https://api.github.com'),
]:
    print(f"{label:34} find_secrets={[s[:14] + '...' for s in find_secrets(answer)]} "
          f"failures={output_safety_failures(answer, passages=p, question=question, system_prompt='')}")
