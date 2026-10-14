"""Local web-enabled backend; defaults to existing MockModel configuration."""
import argparse
import os
from pathlib import Path

import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8015)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    if args.database:
        os.environ["CAMPUSBRIDGE_DATABASE_PATH"] = str(args.database)
    uvicorn.run("campusbridge.web_api:create_web_app", factory=True, host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
