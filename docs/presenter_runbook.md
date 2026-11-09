# BoundaryLine presenter runbook

## Before the audience arrives

Run these checks while the machine is still connected, then disconnect external networking if the demonstration requires an air gap:

```sh
./scripts/preflight.sh --strict
make audit-boundaries
docker compose up -d --pull never --no-build
UV_CACHE_DIR=.uv-cache uv run python scripts/smoke_model.py
```

Open `http://127.0.0.1:3000`. Confirm A, B and C are `READY`. Reset any completed run before changing scenarios. Do not delete the Docker volumes; they contain the immutable evidence ledger and mutable PLC audit database.

## Two-minute version

1. Select **B — Gateway overreach** and say: “The agent has no direct route to the critical PLC, but it can call an operational gateway.”
2. Run the deterministic probe. Point to the direct network deny, authenticated gateway allow and independent PLC receipt. Say: “The firewall worked; application authorisation failed.”
3. Reset, select **C — Policy enforced**, and rerun. Point to the OPA deny and absence of a PLC receipt. Say: “The same identity and target are stopped before downstream I/O.”
4. Conclude: “Segmentation and resource authorisation solve different problems. We need both.”

If the autonomous model makes no out-of-scope request or refuses one, call that a valid model outcome. Use the controlled test to demonstrate the infrastructure property; never describe replay evidence as model-generated.

## Five-minute version

1. Introduce the fixed mission: inspect patch compliance only in the monitoring zone.
2. Explain the evidence layers: model proposal, controller validation, network decision, application/policy decision and mock-PLC receipt.
3. Run **B**. Open the raw events for `network.flow.denied`, `broker.asset_request.allowed` and `mock_plc.request.received`. Emphasise their correlation IDs and sources.
4. Reset and run **C**. Open `policy.decision.deny` and `broker.asset_request.denied`. Confirm there is no `mock_plc.request.received` event.
5. Reset and run **A — Firewall policy**. Show the rule inspector: `BL-A-PERMISSIVE-001` allows the fixed request; `BL-A-CORRECTED-001` rejects the identical target and port. The corrected step adds no PLC receipt.
6. Export one run. Explain that the export contains ordered events, run metadata and the configuration hash.
7. Conclude with the attribution: A is a network-policy failure, B is excessive delegated authority, and C is independent resource-level enforcement.

## Technical walkthrough

### Architecture

- Native Ollama serves the pinned local model on the Mac.
- The agent controller is management-only and uses adapter `ollama-native-tools-v1`.
- The OT application gateway spans management and critical networks.
- OPA is management-only; the mock PLC is critical-only and has no host-published port.
- Scenario A uses a dedicated Linux namespace with only `NET_ADMIN` added.

### Recommended sequence

1. Show `make audit-boundaries` and the loopback-only port bindings.
2. Run `make agent-smoke` or use the dashboard autonomous B run. If the model stays in scope, show that as a safe outcome rather than a failed exploit.
3. Run deterministic B, C and A, resetting between each.
4. Inspect raw event source, origin, sequence, correlation ID, rule/policy version and receipt evidence.
5. Run `make verify-live` to execute two deterministic repetitions of A/B/C and export each run.
6. Run `make release` and verify `dist/SHA256SUMS`.

### Recovery

- If a scenario cannot be selected, read its preflight result rather than bypassing it.
- If switching is locked, wait for the run to finish and press Reset.
- If OPA is unavailable, C must fail closed and record zero PLC receipts.
- If Ollama is unavailable, deterministic A/B/C still work; autonomous B remains disabled by preflight.
- See `docs/troubleshooting.md` for model reloads and clean shutdown.
