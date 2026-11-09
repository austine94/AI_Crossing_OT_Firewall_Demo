#!/usr/bin/env python3
"""Smoke-test the cached local model without permitting any real tool execution."""

import argparse
import json
import time

import httpx

MODEL = "cajina/gemma4_e2b-q4_k_s:v01"


def native_tool_test(client: httpx.Client, model: str) -> dict[str, object]:
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a synthetic patch-audit agent. Use only the supplied tools. "
                    "Never invent a tool result."
                ),
            },
            {
                "role": "user",
                "content": "Call list_assets for the monitoring zone now.",
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "list_assets",
                    "description": "List synthetic assets in an authorized zone.",
                    "parameters": {
                        "type": "object",
                        "properties": {"zone": {"type": "string", "enum": ["monitoring"]}},
                        "required": ["zone"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "options": {"temperature": 0, "num_predict": 512},
    }
    started = time.monotonic()
    response = client.post("/api/chat", json=payload)
    response.raise_for_status()
    body = response.json()
    message = body.get("message", {})
    calls = message.get("tool_calls") or []
    passed = bool(calls) and calls[0].get("function", {}).get("name") == "list_assets"
    return {
        "passed": passed,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "message": message,
    }


def structured_output_test(client: httpx.Client, model: str) -> dict[str, object]:
    schema = {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": ["list_assets"]},
            "arguments": {
                "type": "object",
                "properties": {"zone": {"type": "string", "enum": ["monitoring"]}},
                "required": ["zone"],
                "additionalProperties": False,
            },
        },
        "required": ["tool", "arguments"],
        "additionalProperties": False,
    }
    payload = {
        "model": model,
        "stream": False,
        "format": schema,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Return the JSON action needed to list synthetic assets in the monitoring "
                    "zone. Do not add prose."
                ),
            }
        ],
        "options": {"temperature": 0, "num_predict": 512},
    }
    started = time.monotonic()
    response = client.post("/api/chat", json=payload)
    response.raise_for_status()
    body = response.json()
    content = body.get("message", {}).get("content", "")
    try:
        action = json.loads(content)
    except json.JSONDecodeError:
        action = None
    passed = action == {"tool": "list_assets", "arguments": {"zone": "monitoring"}}
    return {
        "passed": passed,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "content": content,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=180.0) as client:
        native = native_tool_test(client, args.model)
        structured = structured_output_test(client, args.model)

    result = {
        "model": args.model,
        "native_tool_call": native,
        "structured_output": structured,
        "recommended_mode": (
            "native_tool_call"
            if native["passed"]
            else "structured_output"
            if structured["passed"]
            else "not_accepted"
        ),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if native["passed"] or structured["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
