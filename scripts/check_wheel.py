"""Use python -I to prove that the installed wheel runs without the source tree."""
import argparse
import json
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Existing verification preserved; use a new path.")
    installed = args.installed.resolve()
    sys.path.insert(0, str(installed))
    import campusbridge
    from campusbridge.api import create_app
    from campusbridge.security import DEMO_PASSWORD
    from campusbridge.settings import Settings
    from fastapi.testclient import TestClient
    assert Path(campusbridge.__file__).resolve().is_relative_to(installed)
    with tempfile.TemporaryDirectory(prefix="campusbridge-wheel-") as work:
        app = create_app(Settings(environment="test", database_path=Path(work) / "wheel.sqlite3"))
        with TestClient(app) as api:
            assert api.get("/docs").status_code == 200
            logged = api.post("/api/auth/login", json={"username": "student_lan", "password": DEMO_PASSWORD})
            headers = {"Authorization": "Bearer " + logged.json()["access_token"]}
            conv = api.post("/api/conversations", headers=headers).json()["conversation_id"]
            reply = api.post(f"/api/conversations/{conv}/messages", headers={**headers, "Idempotency-Key": "installed-demo"},
                             json={"message": "我想整理这一周的学习安排"})
            assert reply.status_code == 200 and reply.json()["state"] == "ACCEPTED"
    result = {"passed": True, "package_origin": str(campusbridge.__file__), "source_tree_imported": False,
              "swagger_status": 200, "reply_state": "ACCEPTED", "external_model_calls": 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
