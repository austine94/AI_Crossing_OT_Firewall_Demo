.PHONY: preflight test format lint audit-boundaries verify-live release export-evidence agent-smoke reset-run

preflight:
	./scripts/preflight.sh

test:
	UV_CACHE_DIR=.uv-cache uv run pytest

format:
	UV_CACHE_DIR=.uv-cache uv run ruff format .

lint:
	UV_CACHE_DIR=.uv-cache uv run ruff check .

audit-boundaries:
	UV_CACHE_DIR=.uv-cache uv run python scripts/audit_boundaries.py

verify-live:
	UV_CACHE_DIR=.uv-cache uv run python scripts/verify_live_demo.py --repeats 2

release:
	UV_CACHE_DIR=.uv-cache uv run python scripts/package_release.py

export-evidence:
	@test -n "$(RUN_ID)" || (echo "Usage: make export-evidence RUN_ID=<uuid> [FORMAT=json|csv]" && exit 2)
	curl --fail --silent --show-error --output "evidence/$(RUN_ID).$(or $(FORMAT),json)" "http://127.0.0.1:8004/v1/runs/$(RUN_ID)/export?format=$(or $(FORMAT),json)"

agent-smoke:
	UV_CACHE_DIR=.uv-cache uv run python scripts/run_agent.py --one-tool-smoke

reset-run:
	@test -n "$(RUN_ID)" || (echo "Usage: make reset-run RUN_ID=<uuid> [SCENARIO=B]" && exit 2)
	UV_CACHE_DIR=.uv-cache uv run python scripts/reset_demo.py "$(RUN_ID)" --scenario "$(or $(SCENARIO),B)"

