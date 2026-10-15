"""Run D1 acceptance on a private loopback MySQL process; credentials stay in memory.

Requires an installed MySQL 8 server binary. Does not access the system service.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import tempfile
import hashlib
import xml.etree.ElementTree as ET
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def sanitized_xml(value, sanitize):
    tree = ET.fromstring(value)
    for element in tree.iter():
        element.attrib = {key:sanitize(text) for key,text in element.attrib.items()}
        if element.text: element.text = sanitize(element.text)
        if element.tail: element.tail = sanitize(element.tail)
    return ET.tostring(tree, encoding="unicode", xml_declaration=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mysqld", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.scratch.exists() or args.output.exists() or args.scratch.resolve().is_relative_to(ROOT):
        parser.error("Use new output and new external scratch directories")
    import pymysql
    args.scratch.mkdir(parents=True)
    args.output.mkdir(parents=True)
    bindings = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in (ROOT / "src", ROOT / "tests") for p in sorted(folder.rglob("*.py"))}
    bindings["src/campusbridge/resources/business-schema-v1.json"] = hashlib.sha256((ROOT / "src/campusbridge/resources/business-schema-v1.json").read_bytes()).hexdigest()
    (args.output / "source-bindings.json").write_text(json.dumps(bindings,indent=2)+"\n",encoding="utf-8",newline="\n")
    datadir = args.scratch / "data"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    init = [str(args.mysqld), "--no-defaults", "--initialize-insecure", "--basedir=" + str(args.mysqld.parent.parent),
        "--datadir=" + str(datadir), "--log-error=" + str(args.scratch / "initialize.log")]
    subprocess.run(init, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = subprocess.Popen([str(args.mysqld), "--no-defaults", "--basedir=" + str(args.mysqld.parent.parent),
        "--datadir=" + str(datadir), "--port=" + str(port), "--bind-address=127.0.0.1", "--mysqlx=OFF",
        "--skip-log-bin", "--max-connections=100", "--innodb-buffer-pool-size=128M",
        "--character-set-server=utf8mb4", "--collation-server=utf8mb4_0900_bin",
        "--log-error=" + str(args.scratch / "server.log")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    root_password, app_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    admin = None
    start = time.perf_counter()
    try:
        for _ in range(150):
            try:
                admin = pymysql.connect(host="127.0.0.1", port=port, user="root", password="", connect_timeout=1)
                break
            except pymysql.Error:
                if server.poll() is not None: raise RuntimeError("PRIVATE_MYSQL_START_FAILED") from None
                time.sleep(.2)
        if admin is None: raise RuntimeError("PRIVATE_MYSQL_START_TIMEOUT")
        with admin.cursor() as cursor:
            cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (root_password,))
            database = "cb_d1_test_" + secrets.token_hex(6)
            cursor.execute("CREATE DATABASE `" + database + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin")
            cursor.execute("CREATE USER 'cb_d1_app'@'127.0.0.1' IDENTIFIED BY %s", (app_password,))
            cursor.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON `" + database + "`.* TO 'cb_d1_app'@'127.0.0.1'")
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()[0]
        admin.commit()
        admin.close()
        admin = None
        env = {**os.environ, "PYTHONUTF8": "1", "CAMPUSBRIDGE_TEST_MYSQL_ALLOW": "1",
            "CAMPUSBRIDGE_TEST_MYSQL_URL": f"mysql+pymysql://cb_d1_app:{quote(app_password,safe='')}@127.0.0.1:{port}/{database}?charset=utf8mb4",
            "CAMPUSBRIDGE_TEST_MYSQL_MIGRATION_URL": f"mysql+pymysql://root:{quote(root_password,safe='')}@127.0.0.1:{port}/{database}?charset=utf8mb4"}
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "tests/test_database_d1.py", "tests/test_mysql_d1.py",
            "-v", "--junitxml=" + str(args.scratch / "pytest.xml")], cwd=ROOT, env=env, capture_output=True, encoding="utf-8")
        def sanitized(value):
            for secret in (root_password, app_password, quote(root_password, safe=""), quote(app_password, safe="")):
                value = value.replace(secret, "<redacted>")
            for path, label in ((str(args.scratch), "<scratch>"), (str(ROOT), "<project>"), (str(Path(sys.executable).parent.parent), "<environment>"), (tempfile.gettempdir(), "<temp>")):
                value = value.replace(path, label).replace(path.replace("\\", "/"), label)
            return value
        output = sanitized(result.stdout + result.stderr)
        (args.output / "pytest.txt").write_text(output, encoding="utf-8", newline="\n")
        if (args.scratch / "pytest.xml").exists():
            (args.output / "pytest.xml").write_text(sanitized_xml((args.scratch / "pytest.xml").read_text(encoding="utf-8"), sanitized), encoding="utf-8", newline="\n")
        summary = {"mysql_version": version, "actual_mysql_server": True, "binding": "loopback_only_private_process",
            "app_role": "SELECT INSERT UPDATE DELETE only", "migrator_role": "separate root of private test server",
            "pytest_exit_code": result.returncode, "elapsed_seconds": time.perf_counter()-start, "external_llm_calls": 0,
            "credentials_written": False, "system_mysql_service_modified": False}
        summary["source_hashes_unchanged"] = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected for name,expected in bindings.items())
        if not summary["source_hashes_unchanged"]: summary["pytest_exit_code"] = 1
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8", newline="\n")
        print(output)
        print(json.dumps(summary))
        if summary["pytest_exit_code"]: raise SystemExit(summary["pytest_exit_code"])
    finally:
        if admin is not None: admin.close()
        try:
            with pymysql.connect(host="127.0.0.1", port=port, user="root", password=root_password, connect_timeout=2) as connection:
                connection.cursor().execute("SHUTDOWN")
        except pymysql.Error: pass
        try: server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.terminate()  # Only the private process created above.
            server.wait(timeout=10)


if __name__ == "__main__": main()
