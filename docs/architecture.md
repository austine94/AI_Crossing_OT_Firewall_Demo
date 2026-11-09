# BoundaryLine architecture baseline

## Host and runtime

- Host: Apple M4 Pro MacBook Pro with 48 GB unified memory.
- Model serving: Ollama 0.40.6 as a native macOS process with Metal acceleration, accessed through a provider-neutral adapter.
- Initial model: locally cached `cajina/gemma4_e2b-q4_k_s:v01`, local model ID `1bdc284d356f`.
- Synthetic services: Docker Compose on the same physical machine.
- Delivered operating mode: fully air-gapped, with no runtime downloads or external API calls.
- Experiment A: a dedicated Linux container network namespace with `NET_ADMIN` as its sole added capability and an ephemeral, fixed `nftables` output table.

## Trust boundaries

1. The model proposes actions but has no direct operating-system or network authority.
2. The agent validates every tool name, argument and fixture asset ID.
3. The OT application gateway authenticates a locally signed identity token.
4. Scenario B intentionally omits resource authorisation after authentication.
5. Scenario C evaluates the same verified identity and target through OPA before downstream I/O.
6. Docker network membership restricts the autonomous agent controller to the management network; it never joins the critical network in B and C.
7. Only a mock PLC receive event proves that a critical asset was touched.
8. Scenario A's fixed adapter resolves only the configured mock-PLC service and accepts no caller-supplied destination, port, rule text or command.

## Identity baseline

The orchestration service issues a short-lived, locally signed token containing:

- subject: `patch-agent-lab`;
- role: `monitoring-reader`;
- audience: `boundaryline-broker`;
- scenario: signed B or C execution mode;
- issued-at and expiry times; and
- the current run ID.

The OT application gateway derives caller identity and scenario mode from the verified token, never from request-body fields. Scenario B authenticates the token and deliberately skips the caller-to-resource entitlement check. Scenario C resolves the asset from trusted inventory and sends the verified claims, action, asset ID and server-resolved zone to OPA.

## OPA enforcement baseline

The management-only `openpolicyagent/opa:1.21.4` service loads the read-only Rego policy in `policy/boundaryline.rego`. Policy version 1.0.0 denies by default. A `monitoring-reader` may read patch status only for monitoring-zone assets; critical-zone access requires `critical-maintainer`, which is never assigned to the AI agent. Unknown inputs and policy-service errors fail closed.

The gateway records the policy decision and version before recording its own resource decision. It initiates critical-zone I/O only after an explicit allow. OPA does not proxy or originate PLC traffic.

## Network baseline for B and C

| Component | Management | Monitoring | Critical |
|---|:---:|:---:|:---:|
| Agent executor | Yes | No | No |
| Inventory | Yes | Yes | No |
| OT application gateway | Yes | No | Yes |
| Mock PLC | No | No | Yes |
| OPA | Yes | No | No |

The native model endpoint is not part of the simulated OT routing claim. The management-only controller reaches it through Docker Desktop's host gateway; Ollama remains a native Metal-accelerated process and is not published by Compose.

## Network-policy baseline for A

Scenario A runs in the dedicated `firewall-probe` Linux network namespace. The container is attached to the lab management and critical networks so the controlled path physically exists, but the evidence comes from actual kernel `nftables` rules rather than network membership alone. The service drops all capabilities and adds back only `NET_ADMIN`; it is read-only and has no generic shell or dynamic request API exposed to the model.

For each run the service resolves the fixed `mock-plc:8000` target, installs exactly one rule, sends one fixed read-only request, reads the named rule's packet and byte counters, emits the observed allow or deny event, and removes the table. `BL-A-PERMISSIVE-001` explicitly accepts the destination. `BL-A-CORRECTED-001` rejects the same destination and port. The orchestration acceptance invariant requires one PLC audit row after the permissive request and no increase after the corrected request.

## Model smoke-test baseline

On 23 October 2026, the cached Gemma 4 effective-2B model passed both safe, non-executing tests:

- native Ollama tool calling emitted `list_assets({"zone": "monitoring"})`; and
- schema-constrained output emitted the equivalent validated JSON action.

Both tests used temperature zero and a 512-token output cap. The shorter 128-token trial ended before the model completed its visible reasoning, so the controller must budget enough output tokens or configure the model's thinking behaviour explicitly. Native tool calling is the selected initial mode; schema-constrained output remains the fallback.

The Python agent controller runs in a management-only Compose service and reaches native Ollama through `host.docker.internal:11434`. It reaches inventory, the OT application gateway and events only on the Compose management network and has no critical-network attachment. The mock PLC has no host-published port, so the controller cannot contact it directly; only the dual-network gateway can do so. The separate Docker-resident agent probe remains the independent source for the Experiment B direct-path network test.

The provider adapter converts Gemma/Ollama responses into provider-neutral visible content and declared tool calls; it deliberately discards provider-specific reasoning fields. Pydantic remains the authoritative runtime argument validator. A small schema-compatibility normalization represents single-value literals as `enum` for the pinned Gemma/Ollama template without broadening accepted values.

## Evidence baseline

The events service is a local SQLite evidence ledger, not a policy-decision component. It assigns monotonic per-run sequence numbers and stores proposals, validated actions, gateway decisions, network-probe results and mock-PLC receipts as distinct correlated events. The same ordered stream is available over WebSocket and as JSON or CSV exports.

`broker` remains the internal Compose service name and event namespace so existing APIs and evidence exports remain compatible. In the dashboard and explanatory material, the component is called the **OT application gateway**.

Each export includes run metadata and a SHA-256 hash over the exact bytes of the asset, identity and scenario fixtures plus the Rego policy. This makes configuration changes visible without suggesting that the hash authenticates the host or container environment. Critical-asset access is considered proven only when the event sequence reconciles with the mock PLC's separate durable receive audit.

## Deterministic reset boundary

The orchestration service is a privileged local test harness attached to the management and synthetic critical Docker networks and published only on host loopback port 8005. Its authenticated reset operation accepts an explicit run ID, deletes only that run's mutable mock-PLC audit rows, and appends a `run.reset` event with the deletion count. Inventory fixtures, configuration and historical SQLite events are not modified. This supports repeatable demonstrations without rewriting prior evidence.

## Air-gap definition

The demonstration is ready for air-gapped use only when:

- model weights and tokenizer/template assets are present locally;
- every container image is present locally;
- Python and UI dependencies are locked and cached locally;
- the application performs no automatic update or artifact download;
- external network interfaces can be disabled without affecting health checks; and
- strict preflight succeeds after disconnection.

