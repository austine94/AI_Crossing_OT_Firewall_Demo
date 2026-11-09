"""Fixed Scenario A request adapter backed by kernel nftables evidence."""

import json
import os
import socket
import subprocess
from enum import StrEnum
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from boundaryline.event_client import emit_event_async

app = FastAPI(title="BoundaryLine Firewall Probe", version="0.1.0")

EVENT_URL = os.getenv("BOUNDARYLINE_EVENT_URL", "http://events:8000")
PLC_HOST = os.getenv("BOUNDARYLINE_FIREWALL_TARGET_HOST", "mock-plc")
PLC_PORT = int(os.getenv("BOUNDARYLINE_FIREWALL_TARGET_PORT", "8000"))
PLC_ASSET_ID = "PLC-CRIT-001"
TABLE_NAME = "boundaryline_a"
CHAIN_NAME = "fixed_output"


class FirewallMode(StrEnum):
    PERMISSIVE = "permissive"
    CORRECTED = "corrected"


RULES = {
    FirewallMode.PERMISSIVE: ("BL-A-PERMISSIVE-001", "accept"),
    FirewallMode.CORRECTED: ("BL-A-CORRECTED-001", "reject"),
}


class FirewallProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    correlation_id: UUID
    mode: FirewallMode


class FirewallProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: FirewallMode
    rule_id: str
    result: str
    target_asset: str
    packet_count: int
    byte_count: int
    response_status: int | None = None


def run_nft(arguments: list[str], *, input_text: str | None = None) -> str:
    completed = subprocess.run(
        ["nft", *arguments],
        input=input_text,
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "nft command failed")
    return completed.stdout


def remove_policy() -> None:
    listed = subprocess.run(
        ["nft", "list", "table", "inet", TABLE_NAME],
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    if listed.returncode == 0:
        run_nft(["delete", "table", "inet", TABLE_NAME])


def resolve_fixed_target() -> str:
    addresses = socket.getaddrinfo(PLC_HOST, PLC_PORT, type=socket.SOCK_STREAM)
    ipv4 = sorted({item[4][0] for item in addresses if item[0] == socket.AF_INET})
    if len(ipv4) != 1:
        raise RuntimeError("fixed mock PLC hostname did not resolve to exactly one IPv4 address")
    return ipv4[0]


def install_policy(mode: FirewallMode, target_ip: str) -> str:
    rule_id, verdict = RULES[mode]
    remove_policy()
    script = "\n".join(
        [
            f"add table inet {TABLE_NAME}",
            (
                f"add chain inet {TABLE_NAME} {CHAIN_NAME} "
                "{ type filter hook output priority 0; policy accept; }"
            ),
            (
                f'add rule inet {TABLE_NAME} {CHAIN_NAME} ip daddr {target_ip} '
                f'tcp dport {PLC_PORT} counter {verdict} comment "{rule_id}"'
            ),
        ]
    )
    run_nft(["-f", "-"], input_text=script)
    return rule_id


def read_rule_counter(rule_id: str) -> tuple[int, int]:
    payload = json.loads(run_nft(["-j", "list", "chain", "inet", TABLE_NAME, CHAIN_NAME]))
    for item in payload.get("nftables", []):
        rule = item.get("rule")
        if not isinstance(rule, dict) or rule.get("comment") != rule_id:
            continue
        for expression in rule.get("expr", []):
            counter = expression.get("counter") if isinstance(expression, dict) else None
            if isinstance(counter, dict):
                return int(counter.get("packets", 0)), int(counter.get("bytes", 0))
    raise RuntimeError("installed nftables rule counter was not found")


@app.get("/health")
def health() -> dict[str, str]:
    try:
        version = run_nft(["--version"]).strip()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=503, detail="nftables is unavailable") from exc
    return {"status": "ok", "firewall": version}


@app.post("/v1/probes/firewall", response_model=FirewallProbeResult)
async def firewall_probe(request: FirewallProbeRequest) -> FirewallProbeResult:
    target_ip = ""
    rule_id = RULES[request.mode][0]
    response_status: int | None = None
    request_allowed = False
    error_type: str | None = None
    try:
        target_ip = resolve_fixed_target()
        rule_id = install_policy(request.mode, target_ip)
        headers = {
            "X-Run-ID": str(request.run_id),
            "X-Correlation-ID": str(request.correlation_id),
            "X-Requesting-Identity": "fixed-firewall-probe",
            "X-BoundaryLine-Scenario": "A",
        }
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(
                    f"http://{target_ip}:{PLC_PORT}/status/patch", headers=headers
                )
                response_status = response.status_code
                response.raise_for_status()
            request_allowed = True
        except httpx.HTTPError as exc:
            error_type = type(exc).__name__

        packet_count, byte_count = read_rule_counter(rule_id)
        expected_allowed = request.mode is FirewallMode.PERMISSIVE
        if request_allowed is not expected_allowed or packet_count < 1:
            raise RuntimeError("firewall observation did not match the installed fixed rule")

        details: dict[str, Any] = {
            "policy_mode": request.mode.value,
            "rule_id": rule_id,
            "rule_family": "inet",
            "rule_table": TABLE_NAME,
            "rule_chain": CHAIN_NAME,
            "target_ip": target_ip,
            "target_port": PLC_PORT,
            "packet_count": packet_count,
            "byte_count": byte_count,
            "response_status": response_status,
        }
        if error_type:
            details["exception_type"] = error_type
        await emit_event_async(
            EVENT_URL,
            {
                "run_id": str(request.run_id),
                "correlation_id": str(request.correlation_id),
                "scenario": "A",
                "kind": "network.flow.allowed" if request_allowed else "network.flow.denied",
                "actor": "firewall-probe",
                "requesting_identity": "fixed-firewall-probe",
                "target_asset": PLC_ASSET_ID,
                "target_zone": "critical",
                "tool": "fixed_patch_status_request",
                "decision": "allow" if request_allowed else "deny",
                "reason_code": (
                    "PERMISSIVE_FIREWALL_RULE_ALLOWED"
                    if request_allowed
                    else "CORRECTED_FIREWALL_RULE_DENIED"
                ),
                "evidence_source": "network_probe",
                "origin": "manual",
                "details": details,
            },
        )
        return FirewallProbeResult(
            mode=request.mode,
            rule_id=rule_id,
            result="allowed" if request_allowed else "denied",
            target_asset=PLC_ASSET_ID,
            packet_count=packet_count,
            byte_count=byte_count,
            response_status=response_status,
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=503, detail="fixed firewall probe failed") from exc
    finally:
        try:
            remove_policy()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass
