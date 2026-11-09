#!/usr/bin/env python3
"""Run repeatable A/B/C acceptance checks through the dashboard API."""

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

UI_HEADERS = {"Accept": "application/json", "X-BoundaryLine-UI": "dashboard-v1"}


def api(base_url: str, path: str, *, method: str = "GET") -> Any:
    request = Request(f"{base_url.rstrip('/')}{path}", method=method, headers=UI_HEADERS)
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        raise RuntimeError(f"{method} {path} returned {exc.code}: {exc.read().decode()}") from exc


def reset_if_needed(base_url: str) -> None:
    registry = api(base_url, "/v1/scenarios")
    if registry["current_run_id"]:
        api(base_url, "/v1/dashboard/runs/current/reset", method="POST")


def wait_for_completion(base_url: str, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        registry = api(base_url, "/v1/scenarios")
        if registry["current_run_id"] == run_id and registry["run_state"] in {
            "completed",
            "failed",
        }:
            return registry
        time.sleep(0.2)
    raise TimeoutError(f"run {run_id} did not complete within 30 seconds")


def assert_outcome(scenario: str, events: list[dict[str, Any]]) -> None:
    kinds = [event["kind"] for event in events]
    finished = events[-1]
    if finished["kind"] != "run.finished" or finished["decision"] != "allow":
        raise AssertionError(f"scenario {scenario} did not finish successfully")
    if scenario == "A":
        if kinds.count("network.flow.allowed") != 1 or kinds.count("network.flow.denied") != 1:
            raise AssertionError("A did not record one kernel allow and one kernel deny")
        if kinds.count("mock_plc.request.received") != 1:
            raise AssertionError("A must produce exactly one permissive-mode PLC receipt")
        if finished["details"].get("corrected_added_plc_hits") != 0:
            raise AssertionError("A corrected rule added a PLC receipt")
    elif scenario == "B":
        required = {
            "network.flow.denied",
            "broker.asset_request.allowed",
            "mock_plc.request.received",
        }
        if not required.issubset(kinds):
            raise AssertionError("B did not prove direct deny and delegated PLC access")
    elif scenario == "C":
        required = {"network.flow.denied", "policy.decision.deny", "broker.asset_request.denied"}
        if not required.issubset(kinds) or "mock_plc.request.received" in kinds:
            raise AssertionError("C did not deny before PLC receipt")


def run_once(base_url: str, scenario: str, evidence_dir: Path) -> str:
    api(base_url, f"/v1/scenarios/{scenario}/select", method="POST")
    started = api(base_url, "/v1/dashboard/runs/deterministic", method="POST")
    run_id = started["run_id"]
    registry = wait_for_completion(base_url, run_id)
    if registry["run_state"] != "completed":
        raise AssertionError(f"scenario {scenario} entered {registry['run_state']}")
    events = api(base_url, f"/v1/dashboard/runs/{run_id}/events")
    assert_outcome(scenario, events)
    export = api(base_url, f"/v1/dashboard/runs/{run_id}/export")
    if export["metadata"]["event_count"] != len(events):
        raise AssertionError("export event count does not reconcile")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    output = evidence_dir / f"scenario-{scenario.lower()}-{run_id}.json"
    output.write_text(json.dumps(export, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reset = api(base_url, "/v1/dashboard/runs/current/reset", method="POST")
    if not reset["historical_events_retained"]:
        raise AssertionError("reset did not retain historical evidence")
    print(f"PASS  scenario {scenario} run {run_id} ({len(events)} ordered events)")
    return run_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--evidence-dir", type=Path, default=Path("evidence/phase6"))
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    reset_if_needed(args.base_url)
    outcomes: dict[str, list[str]] = {scenario: [] for scenario in "ABC"}
    for _ in range(args.repeats):
        for scenario in "ABC":
            outcomes[scenario].append(run_once(args.base_url, scenario, args.evidence_dir))
    print(json.dumps({"repeats": args.repeats, "runs": outcomes}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
