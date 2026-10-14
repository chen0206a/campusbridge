import argparse
import os
from pathlib import Path

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="CampusBridge local mock backend")
    parser.add_argument("--port", type=int, default=8014)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    if args.database:
        os.environ["CAMPUSBRIDGE_DATABASE_PATH"] = str(args.database)
    uvicorn.run("campusbridge.api:create_app", factory=True, host="127.0.0.1", port=args.port,
                workers=1, log_level="info")


if __name__ == "__main__":
    main()
