"""Explicit download of a pinned public MIT embedding snapshot; never called by API."""
import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from campusbridge.dense_retrieval import MODEL_ID, MODEL_REVISION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    names = ("config.json", "tokenizer.json", "tokenizer_config.json", "vocab.txt", "special_tokens_map.json", "model.safetensors", "README.md")
    api = f"https://huggingface.co/api/models/{MODEL_ID}/revision/{MODEL_REVISION}?blobs=true"
    metadata = json.load(urllib.request.urlopen(api, timeout=60))
    if metadata["sha"] != MODEL_REVISION:
        raise ValueError("MODEL_REVISION_MISMATCH")
    entries = {row["rfilename"]: row for row in metadata["siblings"]}
    args.output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in names:
        row = entries[name]
        target = args.output / name
        if not target.exists():
            temporary = target.with_suffix(target.suffix + ".part")
            with urllib.request.urlopen(f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{name}", timeout=180) as source, temporary.open("wb") as out:
                while block := source.read(1024 * 1024):
                    out.write(block)
            temporary.replace(target)
        data = target.read_bytes()
        fingerprint = hashlib.sha256(data).hexdigest()
        expected = row.get("lfs", {}).get("sha256")
        if (len(data) != row["size"] or (expected and expected != fingerprint)
                or (not expected and hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != row["blobId"])):
            raise ValueError("MODEL_FILE_HASH_MISMATCH:" + name)
        hashes[name] = {"bytes": len(data), "sha256": fingerprint}
        print(name, len(data), fingerprint, flush=True)
    (args.output / "snapshot.json").write_text(json.dumps({"model": MODEL_ID, "revision": MODEL_REVISION,
        "license": "MIT", "files": hashes}, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
