import subprocess

from scripts.release_candidate import history, path_findings, scan_bytes


def test_additional_secret_shapes_and_url_passwords():
    for text in ("ghp_" + "x" * 36, "AKIA" + "X" * 16,
                 "mysql://demo:" + "synthetic-secret" + "@host/db",
                 'api_key="' + "z" * 32 + '"'):
        assert scan_bytes(text.encode())
    assert not scan_bytes(b"CAMPUSBRIDGE_API_KEY=\nModelPort; use environment credentials")


def test_runtime_and_unrelated_source_paths_rejected():
    for name in ("src/.env.local", "frontend/node_modules/x.js", "data/live.sqlite3",
                 "docker-volumes/mysql/a", "mind-py/app/main.py"):
        assert path_findings(name)
    assert not path_findings(".env.example")
    assert not path_findings("src/campusbridge/runtime.py")


def test_only_exact_public_fixture_values_are_exempt():
    assert not scan_bytes(b"https://user:pass@example.test/v1")
    assert scan_bytes(b"https://user:pass@real-host.invalid/v1")
    value = b"https://user:" + b"pass@real-host.invalid/v1"
    assert not scan_bytes(value, source_path="tests/test_release_candidate.py")
    assert scan_bytes(value, source_path="src/campusbridge/settings.py")
    assert not scan_bytes(b'api_key="offline-demonstration-not-a-real-key"')
    assert scan_bytes(('api_key="' + 'y' * 32 + '"').encode())


def test_binary_random_drive_bytes_are_not_path_metadata():
    assert not scan_bytes(b'\x89PNG\r\n\x1a\n' + b'C' + b':/random-compressed-bytes')
    assert scan_bytes(b'\x89PNG\r\n\x1a\n' + ('sk-' + 'x' * 32).encode())


def test_history_detects_deleted_credentials_without_echoing_value(tmp_path):
    def run(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    run("init")
    run("config", "user.name", "Synthetic Fixture")
    run("config", "user.email", "fixture@example.invalid")
    value = "sk-" + "x" * 32
    source = tmp_path / "config.txt"
    source.write_text(value)
    run("add", "."); run("commit", "-m", "synthetic fixture")
    run("rm", "config.txt"); run("commit", "-m", "delete fixture")
    result = history(tmp_path)
    assert not result["passed"] and result["commits"] == 2
    assert result["findings"][0]["codes"] == ["API_KEY_SHAPE"]
    assert value not in str(result)


def test_history_accepts_clean_fixture(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "README.md").write_text("Original synthetic example")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-m", "fixture"], check=True, capture_output=True)
    assert history(tmp_path)["passed"]
    secret = "sk-" + "z" * 32
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", secret],
                   check=True, capture_output=True)
    result = history(tmp_path)
    assert not result["passed"]
    assert result["findings"][0]["path"] == "<git-commit>"
    assert secret not in str(result)
