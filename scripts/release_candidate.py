"""Read-only scan of tracked content and every reachable Git blob; no publication.

Reports paths, object IDs and rule names, never matched credential contents.
Package allowlisting alone cannot audit Git history, so this is a separate gate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release import audit, content_findings, eligible


SECRET_RULES = (
    ("GITHUB_TOKEN", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("AWS_ACCESS_ID", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("URL_CREDENTIAL", re.compile(r"(?:mysql(?:\+pymysql)?|redis|rediss|https?)://[^\s/:<>]+:[^\s/@<>{}]+@")),
    ("ASSIGNED_API_SECRET", re.compile(
        r"(?i)(?:api[_-]?key|api[_-]?secret|client_secret)\s*[=:]\s*[\"']([A-Za-z0-9_\-]{24,})[\"']")),
)
PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".rdb", ".aof", ".pem", ".key", ".pfx", ".p12", ".gguf", ".safetensors"}
PRIVATE_PARTS = {".venv", "venv", "node_modules", "__pycache__", "docker-data", "docker-volumes", "volumes"}
COMMERCIAL_PARTS = {"mind-py", "mindbridge-java", "mindbridge"}
# Exact public, nonfunctional test/demo literals, also present in historical
# pytest parameter IDs. Never exempt an entire file or all credential URLs.
SYNTHETIC_LITERALS = {
    "URL_CREDENTIAL": ("mysql+pymysql://u:secret@localhost/production",
                       "mysql+pymysql://app:private-example@localhost/campusbridge",
                       "https://user:pass@example.test/v1"),
    "ASSIGNED_API_SECRET": ('api_key="offline-demonstration-not-a-real-key"',),
}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def path_findings(name):
    path = Path(name)
    parts = {p.lower() for p in path.parts}
    codes = []
    if (parts & PRIVATE_PARTS or path.suffix.lower() in PRIVATE_SUFFIXES
            or path.name.startswith(".env") and path.name != ".env.example"):
        codes.append("PRIVATE_OR_RUNTIME_FILE")
    if parts & COMMERCIAL_PARTS:
        codes.append("UNRELATED_SOURCE_NAMESPACE")
    return codes


def scan_bytes(data):
    # Every blob is searched, including files without a recognised text suffix.
    try:
        text = data.decode("utf-8")
        codes = content_findings(text)
    except UnicodeDecodeError:
        # Random compressed image bytes can resemble a drive prefix. Still scan
        # ASCII key/token signatures, but not local paths in binary pixel data.
        text = data.decode("utf-8", errors="replace")
        codes = [code for code in content_findings(text) if code != "LOCAL_ABSOLUTE_PATH"]
    for name, pattern in SECRET_RULES:
        for match in pattern.finditer(text):
            if any(text.startswith(literal, match.start()) for literal in SYNTHETIC_LITERALS.get(name, ())):
                continue
            codes.append(name)
    return sorted(set(codes))


def history(root):
    root = Path(root)
    objects = git(root, "rev-list", "--objects", "--all").decode("utf-8").splitlines()
    names = {}
    findings = []
    for line in objects:
        oid, _, path = line.partition(" ")
        names[oid] = path
        for code in path_findings(path):
            findings.append({"object": oid, "path": path, "codes": [code]})
    # Batch process without command-line limits and without logging any blob data.
    proc = subprocess.Popen(["git", "-C", str(root), "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    blob_count = total_bytes = 0
    try:
        for oid, path in names.items():
            proc.stdin.write((oid + "\n").encode("ascii")); proc.stdin.flush()
            header = proc.stdout.readline().decode("ascii").split()
            if len(header) != 3:
                raise ValueError("GIT_OBJECT_UNREADABLE")
            size = int(header[2])
            data = proc.stdout.read(size)
            if len(data) != size or proc.stdout.read(1) != b"\n":
                raise ValueError("GIT_OBJECT_TRUNCATED")
            if header[1] == "blob":
                blob_count += 1; total_bytes += size
                codes = scan_bytes(data)
                if codes:
                    findings.append({"object": oid, "path": path, "codes": codes})
    finally:
        proc.stdin.close(); proc.stdout.close()
        if proc.wait(timeout=30):
            raise ValueError("GIT_CAT_FILE_FAILED")
    return {"passed": not findings, "commits": int(git(root, "rev-list", "--count", "--all")),
            "objects": len(names), "blobs": blob_count, "blob_bytes": total_bytes,
            "scope": "all refs reachable through git rev-list --objects --all; not unreachable/reflog objects",
            "findings": findings}


def scan(root):
    root = Path(root).resolve()
    names = git(root, "ls-files", "-z").decode("utf-8").rstrip("\0").split("\0")
    findings = []
    files = {}
    for name in names:
        path = root / name
        codes = path_findings(name)
        if not path.is_file() or path.is_symlink():
            codes.append("MISSING_OR_SYMLINK")
        else:
            data = path.read_bytes()
            files[name] = hashlib.sha256(data).hexdigest()
            codes.extend(scan_bytes(data))
        if not eligible(Path(name)) and name != "RELEASE_SHA256SUMS.txt":
            codes.append("OUTSIDE_SOURCE_ALLOWLIST")
        if codes:
            findings.append({"path": name, "codes": sorted(set(codes))})
    historic = history(root)
    source = audit(root)
    return {"passed": source["passed"] and historic["passed"] and not findings,
            "commit": git(root, "rev-parse", "HEAD").decode().strip(),
            "tracked_count": len(names), "tracked_findings": findings,
            "package_audit": source, "git_history": historic,
            "tracked_sha256": files,
            "limitations": "Heuristic secret scan plus provenance/asset/dependency review; no claim of formal authorship proof."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = scan(args.root)
    if args.output:
        if args.output.exists():
            parser.error("Choose a new output file")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in result.items() if k != "tracked_sha256"}, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
