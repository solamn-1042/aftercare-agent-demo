"""Offline administrative job; stop API before changing this embedded index."""
import json
from vector_store import sync_index

if __name__ == "__main__":
    print(json.dumps(sync_index(), ensure_ascii=False, indent=2))
