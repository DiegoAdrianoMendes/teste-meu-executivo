"""Fail-closed extractor for the pinned ChatterMate FastAPI/Vue community tree."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from scripts.oss_census.common import (
    http_capability,
    inventory_scope,
    manual_candidate,
    normalize_route,
    observation,
    parse_result,
    text_scope,
)
from scripts.oss_census.model import Candidate, ExtractionResult, Observation, ParseResult
from scripts.oss_census.python_ast import (
    call_keyword,
    call_name,
    line_offset,
    module_string_constants,
    parse_python,
    static_string,
)

UPSTREAM = "chattermate"
EXTRACTOR = "chattermate-fastapi-vue-v2"
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _application_prefixes(tree: ast.Module, constants: dict[str, str]) -> dict[str, str]:
    prefixes = {"app": "", "router": "", "public_app": ""}
    for node in tree.body:
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if not isinstance(value, ast.Call):
            continue
        constructor = call_name(value.func).split(".")[-1]
        if constructor not in {"APIRouter", "FastAPI"}:
            continue
        prefix = static_string(call_keyword(value, "prefix"), constants) or ""
        for target in targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix
    return prefixes


def _fastapi_routes(path: str, text: str) -> ParseResult:
    tree = parse_python(text, path)
    constants = module_string_constants(tree)
    prefixes = _application_prefixes(tree, constants)
    items: list[Observation] = []
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            decorator_name = call_name(decorator.func)
            method = decorator_name.split(".")[-1].lower()
            owner = decorator_name.rsplit(".", maxsplit=1)[0] if "." in decorator_name else ""
            if owner not in prefixes or method not in _HTTP_METHODS | {"websocket"}:
                continue
            offset = line_offset(text, decorator.lineno)
            route = static_string(decorator.args[0], constants) if decorator.args else None
            if route is None:
                candidates.append(
                    manual_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="fastapi_route",
                        reason="dynamic_route_requires_manual_review",
                        details={"owner": owner, "method": method, "handler": node.name},
                    )
                )
                continue
            full_route = normalize_route(prefixes[owner], route)
            if method == "websocket":
                category = "realtime"
                boundary_type = "realtime_event"
                transport = "websocket"
                operation = "connect"
                family = "fastapi-websocket-route"
            else:
                segments = {segment.lower() for segment in full_route.strip("/").split("/")}
                category = "mcp" if "mcp" in segments else http_capability(full_route)
                boundary_type = "http_route"
                transport = "http"
                operation = method.upper()
                family = "fastapi-http-route"
            items.append(
                observation(
                    upstream=UPSTREAM,
                    category=category,
                    boundary_type=boundary_type,
                    family=family,
                    transport=transport,
                    operation=operation,
                    route=full_route,
                    handler=node.name,
                    name=f"{operation} {full_route} -> {node.name}",
                    path=path,
                    text=text,
                    offset=offset,
                    extractor=EXTRACTOR,
                    candidate_kind="fastapi_route",
                    details={
                        "framework": "fastapi",
                        "application": owner,
                        "declaredRoute": route,
                        "openapi": method != "websocket",
                    },
                )
            )
    return parse_result(items, candidates)


def _socketio_events(path: str, text: str) -> ParseResult:
    tree = parse_python(text, path)
    constants = module_string_constants(tree)
    items: list[Observation] = []
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or call_name(decorator.func) not in {
                "sio.on",
                "sio.event",
            }:
                continue
            offset = line_offset(text, decorator.lineno)
            event = (
                static_string(decorator.args[0], constants)
                if decorator.args
                else node.name
            )
            namespace = static_string(call_keyword(decorator, "namespace"), constants) or "/"
            if event is None:
                candidates.append(
                    manual_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="socketio_event",
                        reason="dynamic_event_requires_manual_review",
                        details={"handler": node.name, "namespace": namespace},
                    )
                )
                continue
            items.append(
                observation(
                    upstream=UPSTREAM,
                    category="realtime",
                    boundary_type="realtime_event",
                    family="socketio-event",
                    transport="socketio",
                    operation=event,
                    route=normalize_route(namespace),
                    handler=node.name,
                    name=f"{namespace}:{event}",
                    path=path,
                    text=text,
                    offset=offset,
                    extractor=EXTRACTOR,
                    candidate_kind="socketio_event",
                    details={"framework": "socket.io", "event": event},
                )
            )
    return parse_result(items, candidates)


def _workflow_nodes(path: str, text: str) -> ParseResult:
    tree = parse_python(text, path)
    items: list[Observation] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "NodeType":
            continue
        for child in node.body:
            if not isinstance(child, ast.Assign) or len(child.targets) != 1:
                continue
            target = child.targets[0]
            value = static_string(child.value)
            if not isinstance(target, ast.Name) or value is None:
                continue
            items.append(
                observation(
                    upstream=UPSTREAM,
                    category="job",
                    boundary_type="scheduled_job",
                    family="workflow-node-type",
                    transport="workflow",
                    operation="execute",
                    route=value,
                    handler=f"NodeType.{target.id}",
                    name=value,
                    path=path,
                    text=text,
                    offset=line_offset(text, child.lineno),
                    extractor=EXTRACTOR,
                    candidate_kind="workflow_node_type",
                    details={"framework": "chattermate-workflow", "nodeType": value},
                )
            )
    return parse_result(items)


def _tool_classes(path: str, text: str) -> ParseResult:
    tree = parse_python(text, path)
    items: list[Observation] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or not node.name.endswith(
            ("Tool", "Tools", "Toolkit")
        ):
            continue
        bases = [call_name(base) for base in node.bases]
        is_mcp = node.name in {"MCPTool", "MCPTools"}
        items.append(
            observation(
                upstream=UPSTREAM,
                category="mcp" if is_mcp else "job",
                boundary_type="mcp_tool" if is_mcp else "scheduled_job",
                family="mcp-tool-class" if is_mcp else "agent-tool-class",
                transport="mcp" if is_mcp else "agent_tool",
                operation="invoke",
                route=node.name,
                handler=node.name,
                name=node.name,
                path=path,
                text=text,
                offset=line_offset(text, node.lineno),
                extractor=EXTRACTOR,
                candidate_kind="tool_class",
                details={"framework": "agno-toolkit", "baseClasses": bases},
            )
        )
    return parse_result(items)


_VUE_ROUTE = re.compile(r"\bpath\s*:\s*(['\"`])([^'\"`]*)\1", re.MULTILINE)


def _vue_routes(path: str, text: str) -> ParseResult:
    items: list[Observation] = []
    for index, match in enumerate(_VUE_ROUTE.finditer(text), start=1):
        declared_route = match.group(2)
        route = normalize_route(declared_route)
        window = text[match.start() : min(len(text), match.end() + 450)]
        name_match = re.search(r"\bname\s*:\s*['\"`]([^'\"`]+)", window)
        name = name_match.group(1) if name_match else f"route-{index}:{route}"
        component_match = re.search(
            r"\bcomponent\s*:\s*([A-Za-z_$][\w$]*(?:\([^)]*\))?)", window
        )
        handler = component_match.group(1) if component_match else name
        items.append(
            observation(
                upstream=UPSTREAM,
                category="ui",
                boundary_type="ui_route",
                family="vue-router-route",
                transport="browser",
                operation="navigate",
                route=route,
                handler=handler,
                name=name,
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="vue_route",
                aliases=(name,),
                details={"framework": "vue-router", "declaredRoute": declared_route},
            )
        )
    return parse_result(items)


SOURCE_PATTERNS = (
    "backend/app/main.py",
    "backend/app/api/**/*.py",
    "backend/app/core/socketio.py",
    "backend/app/models/workflow_node.py",
    "backend/app/tools/**/*.py",
    "frontend/src/router/**/*.ts",
    "frontend/src/router/**/*.js",
    "frontend/src/router/index.ts",
    "backend/tests/**/*.py",
    "chattermate-test/**/*.py",
    "chattermate-test/**/*.ts",
    "chattermate-test/**/*.js",
)


def extract(root: Path) -> ExtractionResult:
    scopes = (
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="fastapi-openapi-routes",
            extractor=EXTRACTOR,
            includes=("backend/app/main.py", "backend/app/api/**/*.py"),
            parser=_fastapi_routes,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="socketio-events",
            extractor=EXTRACTOR,
            includes=("backend/app/api/**/*.py", "backend/app/core/socketio.py"),
            parser=_socketio_events,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="workflow-node-types",
            extractor=EXTRACTOR,
            includes=("backend/app/models/workflow_node.py",),
            parser=_workflow_nodes,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="agent-and-mcp-tools",
            extractor=EXTRACTOR,
            includes=("backend/app/tools/**/*.py",),
            parser=_tool_classes,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="vue-public-routes",
            extractor=EXTRACTOR,
            includes=(
                "frontend/src/router/**/*.ts",
                "frontend/src/router/**/*.js",
                "frontend/src/router/index.ts",
            ),
            parser=_vue_routes,
        ),
        inventory_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="upstream-tests",
            includes=(
                "backend/tests/**/*.py",
                "chattermate-test/**/*.py",
                "chattermate-test/**/*.ts",
                "chattermate-test/**/*.js",
            ),
            classification="supporting_test",
        ),
    )
    return ExtractionResult(upstream=UPSTREAM, root=root, scopes=scopes)
