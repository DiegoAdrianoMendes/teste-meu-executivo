# KLM Hub MCP - Third-party references

## BottleCRM

- Project: BottleCRM / Django-CRM
- Upstream: https://github.com/MicroPyramid/Django-CRM
- Local pinned source: `.vendor/upstreams/bottlecrm`
- Version in the KLM upstream lock: `0.46`
- License: MIT, Copyright (c) 2017 MicroPyramid
- License text: `.vendor/upstreams/bottlecrm/LICENSE`

The KLM adapter was designed after reviewing BottleCRM's standalone MCP server,
compact CRM tool surface, server-side validation, tool annotations, confirmation
gates, and token-conscious responses. No BottleCRM source file was modified, and
no BottleCRM function was removed. The KLM implementation is original code and
does not vendor FastMCP.

## Model Context Protocol

- Specification: https://modelcontextprotocol.io/specification/2025-11-25
- Stdio transport: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- Tools: https://modelcontextprotocol.io/specification/2025-11-25/server/tools

The local server implements the documented newline-delimited JSON-RPC stdio
transport and tool lifecycle. This notice records specifications and design
references; it does not incorporate their implementation source.
