#!/usr/bin/env python3
"""Reset mutable synthetic state for one BoundaryLine run."""

import argparse
import json
import os
from uuid import UUID

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", type=UUID)
    parser.add_argument("--scenario", choices=("A", "B", "C"), default="B")
    parser.add_argument("--orchestration-url", default="http://127.0.0.1:8005")
    args = parser.parse_args()

    secret = os.getenv("BOUNDARYLINE_ADMIN_SECRET", "local-development-only")
    with httpx.Client(base_url=args.orchestration_url, timeout=10.0) as client:
        response = client.post(
            f"/v1/runs/{args.run_id}/reset",
            json={"scenario": args.scenario},
            headers={"X-Admin-Secret": secret},
        )
        response.raise_for_status()
    print(json.dumps(response.json(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
