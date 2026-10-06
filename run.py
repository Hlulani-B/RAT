#!/usr/bin/env python3
"""Entry point for the Repo Analysis Tool (RAT).

Usage:
    python run.py [--host 127.0.0.1] [--port 8000] [--data ./data]
"""

import argparse

from rat.store import Store
from rat.app import create_app


def main():
    ap = argparse.ArgumentParser(description="Repo Analysis Tool (RAT)")
    ap.add_argument("--host", default="127.0.0.1", help="bind address")
    ap.add_argument("--port", type=int, default=8000, help="port")
    ap.add_argument("--data", default="data", help="data directory")
    args = ap.parse_args()

    store = Store(args.data)
    app = create_app(store)
    print(f"RAT listening on http://{args.host}:{args.port}  (data: {store.data_dir})")
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
