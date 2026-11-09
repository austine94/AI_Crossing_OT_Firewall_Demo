# BoundaryLine

BoundaryLine is a fully local, air-gapped AI/OT trust-boundary lab. It demonstrates the difference between:

- a network-policy failure;
- an authenticated but overprivileged OT application gateway; and
- independent resource-level authorisation that blocks the same request.

All assets and actions are synthetic. The lab must never connect to a real OT network or device.

## Delivery order

1. Experiment B: direct path blocked, OT application gateway path allowed.
2. Experiment C: the same gateway request denied by policy before asset access.
3. Experiment A: an isolated Linux firewall lab showing a permissive rule and its correction.

The authoritative implementation plan is [Build_Plan/ot_ai_agent_security_lab_build_plan.md](Build_Plan/ot_ai_agent_security_lab_build_plan.md).

## Current status

Phases 1–6 are complete for supported Experiments A, B and C. The final acceptance run passed 48 automated tests, strict offline preflight with zero warnings, a boundary audit, both pinned-model smoke paths and two deterministic repetitions of every supported experiment after an offline-style cold restart. Experiment A runs two fixed requests in a dedicated Linux container network namespace: kernel `nftables` rule `BL-A-PERMISSIVE-001` permits the first request and `BL-A-CORRECTED-001` rejects the identical second request. Rule counters and the mock PLC's independent durable audit prove that only the permissive request reached the asset. The deterministic Experiment B service path proves that its fixed direct probe is denied while the authenticated OT application gateway reaches the mock PLC. Experiment C replays the same gateway request, resolves the target zone from trusted inventory, and uses OPA 1.21.4 to deny the monitoring reader before the gateway contacts the PLC. The management-only Python agent controller uses the locally cached `cajina/gemma4_e2b-q4_k_s:v01` model through native Ollama 0.40.6 with Metal acceleration and pinned adapter `ollama-native-tools-v1`, validates a strict typed tool registry and records proposed, validated and executed actions separately. A centralized SQLite event service assigns monotonic per-run sequence numbers, streams events over WebSocket, and exports correlated evidence in JSON or CSV with run metadata and a SHA-256 configuration hash. The authenticated orchestration service provides deterministic per-run reset without deleting historical evidence.

The user-facing name **OT application gateway** describes the component's operational role. Internal service names, environment variables and evidence event kinds retain `broker` for compatibility with existing runs and exports.

The shared dashboard provides a server-owned, versioned A/B/C/D scenario registry, live dependency preflight for B and C, guarded scenario selection and lifecycle, autonomous local-model execution for B, deterministic B/C comparison, reset and JSON evidence export. Its topology, decision cards, visible model output, tool proposal and ordered timeline are computed from recorded service events; raw events and correlation IDs are inspectable. With Ollama and the Compose stack running, open [http://127.0.0.1:3000](http://127.0.0.1:3000).

The pinned `openpolicyagent/opa:1.21.4` image is approximately 94.2 MB locally. Its Rego policy is mounted read-only and included in the exported configuration hash.

The current containers are intentionally left running after setup. Stop them without deleting the audit volume using:

```sh
docker compose down
```

## Clean setup

The first connected build may populate the local image and Python caches. An air-gapped restart must use only those cached artifacts:

```sh
UV_CACHE_DIR=.uv-cache uv sync --frozen
docker compose build
./scripts/preflight.sh --strict
docker compose up -d --pull never --no-build
```

All Python container dependencies are installed from `requirements.lock` with hashes. The OPA and model identifiers are pinned. After startup, open `http://127.0.0.1:3000` and wait for the selected scenario preflight to report ready.

## Local preflight

```sh
make preflight
```

Use strict mode before an air-gapped demonstration:

```sh
./scripts/preflight.sh --strict
```

Strict mode requires the Docker daemon, the cached model manifest, and all baseline commands to be available locally. It performs no download.

Audit the declared networks, capabilities, local URLs and published bindings with:

```sh
make audit-boundaries
```

## Evidence export

Export a completed run from the local event ledger:

```sh
make export-evidence RUN_ID=<run-uuid> FORMAT=json
make export-evidence RUN_ID=<run-uuid> FORMAT=csv
```

Exports are written beneath `evidence/`, which is excluded from version control apart from its placeholder.

## Local agent smoke test

With Ollama and the Docker services running, execute one safe monitoring-inventory tool call:

```sh
make agent-smoke
```

The command runs the controller natively, uses only the `list_assets` tool and records the model proposal, validation and execution in the event ledger.

## Acceptance and release

Run every deterministic control outcome twice and save reconciled evidence exports:

```sh
make verify-live
```

Create deterministic source and evidence archives plus `SHA256SUMS`:

```sh
make release
```

Presenter scripts are in [docs/presenter_runbook.md](docs/presenter_runbook.md). The security boundary, residual risks and unsupported claims are documented in [docs/threat_model.md](docs/threat_model.md).

## Reset one run

Clear the selected run's mutable mock-PLC audit counter while retaining its event history:

```sh
make reset-run RUN_ID=<run-uuid> SCENARIO=B
```

The orchestration service is published only on `127.0.0.1:8005`, requires the local admin secret and appends a `run.reset` event containing the number of audit rows removed.

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md) for the complete shutdown checklist and for diagnosing background processes that repeatedly reload an Ollama model.

