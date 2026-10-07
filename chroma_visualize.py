import chromadb

client = chromadb.PersistentClient(path="./data/chroma")

collections = client.list_collections()

print("Collections:")

for collection in collections:
    print("-", collection.name)

if not collections:
    print("No collections found!")
    exit()

collection = client.get_collection(collections[0].name)

data = collection.get(
    include=["documents", "metadatas", "embeddings"]
)

print("\nCollection:", collection.name)
print("Number of records:", len(data["ids"]))

for i in range(min(10, len(data["ids"]))):
    print("\n--- Record", i, "---")
    print("ID:", data["ids"][i])
    print("Document:", data["documents"][i])
    print("Metadata:", data["metadatas"][i])