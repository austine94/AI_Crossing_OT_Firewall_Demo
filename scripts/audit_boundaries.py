#!/usr/bin/env python3
"""Audit the static Compose trust boundaries and local-only service endpoints."""

import json
import subprocess
from urllib.parse import urlparse

ALLOWED_RUNTIME_HOSTS = {
    "agent-controller",
    "agent-probe",
    "broker",
    "events",
    "firewall-probe",
    "host.docker.internal",
    "inventory",
    "mock-plc",
    "policy",
}

EXPECTED_NETWORKS = {
    "agent-controller": {"management"},
    "agent-probe": {"management"},
    "broker": {"management", "critical"},
    "firewall-probe": {"management", "critical"},
    "inventory": {"management", "monitoring"},
    "mock-plc": {"critical"},
    "orchestration": {"management", "critical"},
    "policy": {"management"},
}


def fail(message: str) -> None:
    raise AssertionError(message)


def main() -> int:
    completed = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        capture_output=True,
        check=True,
        text=True,
    )
    config = json.loads(completed.stdout)
    services = config["services"]

    for network_name in ("critical", "monitoring"):
        if not config["networks"][network_name].get("internal"):
            fail(f"{network_name} network must be internal")

    for service_name, expected in EXPECTED_NETWORKS.items():
        actual = set(services[service_name].get("networks", {}))
        if actual != expected:
            fail(f"{service_name} networks are {sorted(actual)}, expected {sorted(expected)}")

    for service_name, service in services.items():
        if service.get("network_mode") == "host":
            fail(f"{service_name} must not use host networking")
        if service.get("privileged"):
            fail(f"{service_name} must not be privileged")
        for port in service.get("ports", []):
            host_ip = port.get("host_ip")
            if host_ip != "127.0.0.1":
                fail(f"{service_name} publishes a non-loopback port: {port}")
        for value in service.get("environment", {}).values():
            if not isinstance(value, str) or not value.startswith(("http://", "https://")):
                continue
            parsed = urlparse(value)
            if parsed.scheme != "http" or parsed.hostname not in ALLOWED_RUNTIME_HOSTS:
                fail(f"{service_name} has a non-lab runtime URL: {value}")

    if services["mock-plc"].get("ports"):
        fail("mock-plc must not publish a host port")
    if services["policy"].get("ports"):
        fail("policy must not publish a host port")
    if services["agent-controller"].get("ports"):
        fail("agent-controller must not publish a host port")

    firewall_caps = set(services["firewall-probe"].get("cap_add", []))
    if firewall_caps != {"NET_ADMIN"}:
        fail("firewall-probe must add only NET_ADMIN")
    for service_name, service in services.items():
        if service_name != "firewall-probe" and service.get("cap_add"):
            fail(f"{service_name} unexpectedly adds Linux capabilities")

    print("PASS  critical and monitoring networks are internal")
    print("PASS  service network memberships match the trust-boundary matrix")
    print("PASS  every published endpoint is bound to 127.0.0.1")
    print("PASS  PLC, OPA and model controller have no published host ports")
    print("PASS  configured runtime URLs resolve only to enumerated lab or host-model services")
    print("PASS  firewall probe adds only NET_ADMIN; no service is privileged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
