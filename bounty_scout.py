#!/usr/bin/env python3
"""Bounty Scout: dependency-free MCP client for Agent Bounty Feed."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

MCP_URL = "https://agent-bounty-feed.dailydollar.workers.dev/mcp"
USER_AGENT = "BountyScout/1.0 (minimal public MCP client)"
PROTOCOL_VERSION = "2025-03-26"


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, url: str = MCP_URL, timeout: int = 20) -> None:
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self.request_id = 0

    def _post(self, payload: dict[str, Any], *, notification: bool = False) -> Any:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self.session_id = session_id
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:600]
            raise MCPError(f"HTTP {exc.code} from MCP endpoint: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise MCPError(f"Could not reach MCP endpoint: {exc}. Check your internet connection and try again.") from exc

        if notification or not raw.strip():
            return None
        # Streamable HTTP may return a JSON object or Server-Sent Events.
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                candidate = line[5:].strip()
                if candidate and candidate != "[DONE]":
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        continue
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MCPError(f"Unexpected response (not JSON or parseable SSE): {raw[:300]}") from exc

    def rpc(self, method: str, params: dict[str, Any] | None = None, *, notification: bool = False) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notification:
            self.request_id += 1
            payload["id"] = self.request_id
        if params is not None:
            payload["params"] = params
        result = self._post(payload, notification=notification)
        if notification:
            return None
        if not isinstance(result, dict):
            raise MCPError(f"MCP response was not a JSON object: {result!r}")
        if "error" in result:
            raise MCPError(f"MCP error for {method}: {json.dumps(result['error'], ensure_ascii=False)}")
        return result

    def connect(self) -> dict[str, Any]:
        response = self.rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "bounty-scout", "version": "1.0.0"},
        })
        assert response is not None
        result = response.get("result", response)
        self.rpc("notifications/initialized", notification=True)
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        response = self.rpc("tools/list", {})
        assert response is not None
        result = response.get("result", response)
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        response = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        assert response is not None
        result = response.get("result", response)
        if result.get("isError"):
            text_parts = [part.get("text", "") for part in result.get("content", []) if isinstance(part, dict)]
            raise MCPError("Tool returned an error: " + "\n".join(text_parts))
        # MCP commonly returns JSON serialized in a text content block.
        for part in result.get("content", []) if isinstance(result, dict) else []:
            if isinstance(part, dict) and part.get("type") == "text":
                value = part.get("text", "")
                try:
                    return json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    return value
        return result


def _extract_items(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Handle common tool result shapes without assuming one feed schema."""
    meta: dict[str, Any] = {}
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)], meta
    if isinstance(payload, dict):
        meta = {k: v for k, v in payload.items() if k not in {"items", "bounties", "results"}}
        for key in ("items", "bounties", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)], meta
    return [], meta


def _money(item: dict[str, Any]) -> str:
    for key in ("reward", "amount", "reward_amount", "est_usd", "usd", "value"):
        value = item.get(key)
        if value is not None:
            currency = item.get("currency") or item.get("reward_currency") or ("USD equiv." if key in {"est_usd", "usd"} else "")
            return f"{value} {currency}".strip()
    return "reward not specified"


def print_bounties(payload: Any, limit: int = 10) -> None:
    items, meta = _extract_items(payload)
    if meta:
        for key in ("generated_at", "total_matching", "count"):
            if key in meta:
                print(f"{key}: {meta[key]}")
    if not items:
        print("No bounty items found in the response.")
        print(json.dumps(payload, indent=2, ensure_ascii=False)[:4000])
        return
    print(f"\n{len(items)} bounty item(s) returned:")
    for item in items[:limit]:
        title = item.get("title") or item.get("name") or "Untitled bounty"
        source = item.get("source") or item.get("platform") or "unknown source"
        url = item.get("url") or item.get("link") or "(no source URL provided)"
        bounty_id = item.get("id")
        print(f"- [{source}] {_money(item)} — {title}")
        if bounty_id:
            print(f"  id: {bounty_id}")
        print(f"  {url}")


def parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find paid agent work through Agent Bounty Feed's free MCP endpoint.")
    parser.add_argument("--limit", type=int, default=10, help="maximum number of results to print (default: 10)")
    parser.add_argument("--min-usd", type=float, help="minimum estimated USD-equivalent reward")
    parser.add_argument("--network", help="filter by network, e.g. base")
    parser.add_argument("--source", help="comma-separated source names, e.g. direct,frantic")
    parser.add_argument("--exclude", help="comma-separated requirements to exclude, e.g. kyc,stake,bond,purchase")
    parser.add_argument("--sort", choices=("reward", "first_seen"), help="sort order supported by the feed")
    parser.add_argument("--query", help="search phrase")
    parser.add_argument("--stats", action="store_true", help="show feed stats instead of bounty listings")
    parser.add_argument("--id", help="fetch one bounty by its feed ID, e.g. frantic:129")
    parser.add_argument("--new-since", help="show new bounties since an ISO-8601 timestamp")
    parser.add_argument("--show-tools", action="store_true", help="show tool names and input schemas")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")

    client = MCPClient()
    try:
        info = client.connect()
        server = info.get("serverInfo", {}) if isinstance(info, dict) else {}
        print(f"Bounty Scout · {server.get('name', 'Agent Bounty Feed')} v{server.get('version', 'unknown')}")
        tools = client.list_tools()
        print("MCP tools: " + ", ".join(tool.get("name", "?") for tool in tools))
        if args.show_tools:
            print(json.dumps(tools, indent=2, ensure_ascii=False))
            return 0
        if args.stats:
            payload = client.call_tool("get_feed_stats")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        if args.id:
            payload = client.call_tool("get_bounty", {"id": args.id})
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        if args.new_since:
            payload = client.call_tool("get_new_bounties", {"since": args.new_since, "limit": args.limit})
        else:
            call_args: dict[str, Any] = {"limit": args.limit}
            if args.min_usd is not None:
                call_args["min_usd"] = args.min_usd
            if args.network:
                call_args["network"] = args.network
            if args.source:
                call_args["source"] = args.source
            if args.exclude:
                call_args["exclude_requires"] = args.exclude
            if args.sort:
                call_args["sort"] = args.sort
            if args.query:
                call_args["q"] = args.query
            payload = client.call_tool("list_bounties", call_args)
        print_bounties(payload, args.limit)
        print("\nReminder: a listing is not a payment guarantee. Open the source URL and verify eligibility, deadline, and submission terms.")
        return 0
    except MCPError as exc:
        print(f"Bounty Scout error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
