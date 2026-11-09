#!/usr/bin/env bash
set -uo pipefail

strict=false
if [[ "${1:-}" == "--strict" ]]; then
  strict=true
fi

failures=0
warnings=0
boundaryline_uv_cache="${UV_CACHE_DIR:-.uv-cache}"

pass() { printf 'PASS  %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; warnings=$((warnings + 1)); }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); }

require_command() {
  if command -v "$1" >/dev/null 2>&1; then
    pass "$1 is installed"
  else
    fail "$1 is not installed"
  fi
}

require_command docker
require_command uv
require_command node
require_command npm
require_command ollama

model_manifest="${HOME}/.ollama/models/manifests/registry.ollama.ai/cajina/gemma4_e2b-q4_k_s/v01"
if [[ -f "$model_manifest" ]]; then
  pass "Gemma 4 effective-2B manifest is cached"
else
  fail "Gemma 4 effective-2B manifest is missing"
fi

if docker info >/dev/null 2>&1; then
  pass "Docker daemon is running"
  required_images=(
    "python:3.12-slim"
    "boundaryline/service:local"
    "boundaryline/firewall-probe:local"
    "openpolicyagent/opa:1.21.4"
  )
  for image in "${required_images[@]}"; do
    if docker image inspect "$image" >/dev/null 2>&1; then
      pass "container image is cached: $image"
    elif $strict; then
      fail "container image is not cached: $image"
    else
      warn "container image is not cached yet: $image"
    fi
  done
elif $strict; then
  fail "Docker daemon is not running"
else
  warn "Docker daemon is not running; start Docker Desktop before service checks"
fi

if UV_CACHE_DIR="$boundaryline_uv_cache" uv lock --check >/dev/null 2>&1; then
  pass "uv.lock matches pyproject.toml"
else
  fail "uv.lock is stale"
fi

if UV_CACHE_DIR="$boundaryline_uv_cache" \
  uv export --frozen --no-dev --no-emit-project --no-header --format requirements-txt \
  | cmp --silent - requirements.lock; then
  pass "hashed container requirements match uv.lock"
else
  fail "requirements.lock does not match uv.lock"
fi

if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  pass "Apple-silicon host detected"
else
  fail "Expected an Apple-silicon macOS host"
fi

if [[ "$failures" -gt 0 ]]; then
  printf '\nPreflight failed: %d failure(s), %d warning(s).\n' "$failures" "$warnings"
  exit 1
fi

printf '\nPreflight passed with %d warning(s). No downloads were attempted.\n' "$warnings"

