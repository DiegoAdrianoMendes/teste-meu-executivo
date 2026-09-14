from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.mcp.protocol import LATEST_PROTOCOL_VERSION, McpProtocol
from app.mcp.server import StdioMcpServer
from tests.mcp.conftest import runtime_for


@pytest.mark.asyncio
async def test_requires_initialize(mcp_runtime):
    response = await McpProtocol(mcp_runtime).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    assert response["error"]["code"] == -32002


@pytest.mark.asyncio
async def test_initialize_negotiates_latest_for_unknown_version(mcp_runtime):
    response = await McpProtocol(mcp_runtime).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2099-01-01", "clientInfo": {"name": "test"}},
        }
    )
    assert response["result"]["protocolVersion"] == LATEST_PROTOCOL_VERSION
    assert response["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert "workspace" in response["result"]["instructions"].lower()


@pytest.mark.asyncio
async def test_tool_schemas_never_accept_workspace_or_acting_user(mcp_runtime):
    protocol = McpProtocol(mcp_runtime)
    await protocol.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": LATEST_PROTOCOL_VERSION},
        }
    )
    response = await protocol.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    assert len(response["result"]["tools"]) == 9
    for tool in response["result"]["tools"]:
        properties = tool["inputSchema"].get("properties", {})
        assert "workspace_id" not in properties
        assert "workspaceId" not in properties
        assert "user_id" not in properties
        assert "userId" not in properties
        assert tool["inputSchema"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_read_only_grant_hides_all_mutation_tools(auth_context, db_session, tmp_path: Path):
    runtime = runtime_for(
        auth_context,
        db_session,
        tmp_path,
        capabilities=("crm.read",),
    )
    assert [item["name"] for item in runtime.tool_definitions()] == [
        "klm.crm.list_clients",
        "klm.crm.get_client",
        "klm.crm.list_matters",
        "klm.crm.get_matter",
    ]


@pytest.mark.asyncio
async def test_tool_validation_is_returned_as_tool_error(mcp_runtime):
    protocol = McpProtocol(mcp_runtime)
    await protocol.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": LATEST_PROTOCOL_VERSION},
        }
    )
    response = await protocol.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "klm.crm.create_case_note",
                "arguments": {"matter_id": "invalid", "note": "x", "confirm": False},
            },
        }
    )
    result = response["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "MCP_INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_stdio_is_newline_delimited_json_rpc(mcp_runtime):
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": LATEST_PROTOCOL_VERSION},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}},
    ]
    stdin = io.BytesIO("".join(json.dumps(item) + "\n" for item in requests).encode())
    stdout = io.StringIO()
    await StdioMcpServer(McpProtocol(mcp_runtime), stdin=stdin, stdout=stdout).run()
    lines = stdout.getvalue().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["id"] for line in lines] == [1, 2]


@pytest.mark.asyncio
async def test_stdio_parse_error_does_not_stop_server(mcp_runtime):
    stdin = io.BytesIO(b"not-json\n{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"ping\"}\n")
    stdout = io.StringIO()
    await StdioMcpServer(McpProtocol(mcp_runtime), stdin=stdin, stdout=stdout).run()
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1]["result"] == {}
