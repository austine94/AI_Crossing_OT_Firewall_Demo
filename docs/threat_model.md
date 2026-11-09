# Threat model and security claims

## Assets and security objectives

The protected asset is a synthetic, read-only mock PLC. The objectives are to prevent unintended critical-zone requests, preserve attributable evidence, and keep every executable destination and operation inside the fixed lab boundary.

The evidence ledger, scenario manifest, identity and asset fixtures, Rego policy, local model adapter and presenter-facing labels are also security-relevant. A misleading event or label can invalidate the teaching claim even when the network behaves correctly.

## Trust boundaries

The model is untrusted proposal generation. The controller is trusted to validate tool names and arguments. The OT application gateway is a privileged intermediary. OPA is the independent authorisation decision point for C. Docker network namespaces and `nftables` are the network enforcement points. The mock PLC audit is the authoritative receipt signal.

The browser is not trusted to supply commands, destinations, identities, policy text or scenario capabilities. It may select only server-owned scenario IDs and lifecycle operations.

## Threats covered

- A model proposes an out-of-scope but schema-valid asset request.
- An authenticated caller asks an overprivileged gateway to reach a forbidden resource.
- The policy service denies or becomes unavailable before downstream I/O.
- A permissive network rule allows a fixed direct request that a corrected rule blocks.
- Model tool-call formatting is invalid, unknown or contains extra arguments.
- Evidence is confused across runs or a reset rewrites historical events.

## Controls

- Strict Pydantic schemas and a fixed tool allowlist.
- Hard-coded synthetic asset and destination allowlists; no generic shell or URL-fetch tool.
- Signed, run-bound identity tokens for B and C.
- Trusted inventory zone resolution and default-deny OPA enforcement for C.
- Internal critical and monitoring networks, loopback-only published ports and no host networking.
- Named Scenario A kernel rules with counters and a fixed target.
- Monotonic per-run event sequences and an independent durable PLC receipt audit.
- Bounded model turns, tool calls and network timeouts.

## Residual risks and limitations

- This is an educational synthetic lab, not an industrial firewall, PLC protocol stack or production safety system.
- Docker Desktop and the macOS host remain trusted. A compromised Docker daemon or host can bypass container boundaries and alter evidence.
- The Compose management network has ordinary Docker egress so the controller can reach the native host model. Application configuration restricts runtime URLs to enumerated local services, but this is not a kernel-enforced general egress allowlist.
- Loopback publication prevents ordinary remote access but is not authentication. Local processes can reach published APIs; privileged mutations additionally require fixed local headers/secrets.
- The default development secrets are not suitable for shared or hostile hosts.
- Event configuration hashes reveal changes but do not sign the host, images or exported archive. Release archives include separate SHA-256 checksums, not a public-key signature.
- Model behavior is nondeterministic even at temperature zero. A refusal or no-request result is valid and does not prove the vulnerable path is absent; deterministic tests establish the control outcome.
- Scenario A is a real Linux kernel rule in a container namespace, but it does not emulate a specific vendor firewall or production routing design.
- The lab must never be connected to real OT interfaces, credentials, addresses or devices.

## Out of scope

Real equipment, write operations, malware, credential theft, arbitrary SSRF, generic command execution, production firewall changes, Internet APIs and claims that the language model “hacked” a firewall are out of scope.
