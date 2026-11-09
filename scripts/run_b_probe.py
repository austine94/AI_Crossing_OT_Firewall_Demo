#!/usr/bin/env python3
"""Run the fixed, deterministic Experiment B probe against local services."""

import argparse
import json
import os
from uuid import UUID, uuid4

import httpx

from boundaryline.identity import issue_token


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=UUID, default=None)
    parser.add_argument("--agent-url", default="http://127.0.0.1:8003")
    parser.add_argument("--event-url", default="http://127.0.0.1:8004")
    args = parser.parse_args()

    run_id = args.run_id or uuid4()
    direct_correlation_id = uuid4()
    broker_correlation_id = uuid4()
    secret = os.getenv("BOUNDARYLINE_TOKEN_SECRET", "local-development-only")
    token = issue_token(
        subject="patch-agent-lab",
        roles=("monitoring-reader",),
        audience="boundaryline-broker",
        run_id=run_id,
        secret=secret,
    )

    run_correlation_id = uuid4()
    run_event_base = {
        "run_id": str(run_id),
        "correlation_id": str(run_correlation_id),
        "scenario": "B",
        "actor": "controlled-test-runner",
        "decision": "not_evaluated",
        "evidence_source": "orchestrator",
        "origin": "manual",
    }
    with httpx.Client(base_url=args.event_url, timeout=10.0) as event_client:
        started = event_client.post(
            "/v1/events",
            json={
                **run_event_base,
                "kind": "run.started",
                "reason_code": "CONTROLLED_TEST_STARTED",
            },
        )
        started.raise_for_status()

    with httpx.Client(base_url=args.agent_url, timeout=10.0) as client:
        direct = client.post(
            "/v1/probes/direct",
            json={
                "run_id": str(run_id),
                "correlation_id": str(direct_correlation_id),
            },
        )
        direct.raise_for_status()
        broker = client.post(
            "/v1/probes/broker",
            json={
                "run_id": str(run_id),
                "correlation_id": str(broker_correlation_id),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        broker.raise_for_status()

    with httpx.Client(base_url=args.event_url, timeout=10.0) as event_client:
        finished = event_client.post(
            "/v1/events",
            json={
                **run_event_base,
                "kind": "run.finished",
                "reason_code": "CONTROLLED_TEST_FINISHED",
            },
        )
        finished.raise_for_status()
        events_response = event_client.get(f"/v1/runs/{run_id}/events")
        events_response.raise_for_status()
        export_response = event_client.get(f"/v1/runs/{run_id}/export", params={"format": "json"})
        export_response.raise_for_status()

    result = {
        "experiment": "B",
        "source": "CONTROLLED_TEST",
        "run_id": str(run_id),
        "direct_correlation_id": str(direct_correlation_id),
        "broker_correlation_id": str(broker_correlation_id),
        "direct_probe": direct.json(),
        "broker_probe": broker.json(),
        "events": events_response.json(),
        "evidence_metadata": export_response.json()["metadata"],
    }
    print(json.dumps(result, indent=2, sort_keys=True))

    direct_denied = direct.json().get("result") == "denied"
    broker_allowed = broker.json().get("status_code") == 200
    return 0 if direct_denied and broker_allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
