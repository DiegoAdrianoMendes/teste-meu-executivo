"""Pure, deterministic primitives shared by flow preview and worker execution."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, TypedDict


class FlowRouteDecision(TypedDict, total=False):
    field: str
    operator: str
    expected: Any
    actual: Any
    branch: str
    routeKey: str
    fallback: bool


_EXACT_EXPRESSION = re.compile(r"^\s*\{\{?\s*([A-Za-z_][\w.]*)\s*\}\}?\s*$")
_EMBEDDED_EXPRESSION = re.compile(r"\{\{?\s*([A-Za-z_][\w.]*)\s*\}\}?")


def get_context_value(context: Mapping[str, Any], path: str, default: Any = None) -> Any:
    """Resolve dotted paths without evaluating arbitrary code."""
    normalized = str(path or "").strip()
    if not normalized:
        return default
    if normalized in context:
        return context[normalized]
    current: Any = context
    for part in normalized.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        if isinstance(current, list | tuple) and part.isdigit():
            index = int(part)
            if 0 <= index < len(current):
                current = current[index]
                continue
        return default
    return current


def set_context_value(context: dict[str, Any], path: str, value: Any) -> None:
    """Set a dotted context path while preserving sibling values."""
    parts = [part for part in str(path or "").strip().split(".") if part]
    if not parts:
        raise ValueError("Nome da variável não pode ser vazio")
    current = context
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def resolve_expression(expression: Any, context: Mapping[str, Any]) -> Any:
    """Resolve safe placeholders such as ``{{lead.name}}``; no eval is used."""
    if not isinstance(expression, str):
        return expression
    exact = _EXACT_EXPRESSION.fullmatch(expression)
    if exact:
        return get_context_value(context, exact.group(1))

    def replace(match: re.Match[str]) -> str:
        value = get_context_value(context, match.group(1), "")
        if isinstance(value, dict | list):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return "" if value is None else str(value)

    return _EMBEDDED_EXPRESSION.sub(replace, expression)


def coerce_flow_value(value: Any, value_type: str | None) -> Any:
    normalized = str(value_type or "auto").strip().lower()
    if normalized == "auto":
        return value
    if normalized == "string":
        return "" if value is None else str(value)
    if normalized == "number":
        number = float(value)
        return int(number) if number.is_integer() else number
    if normalized == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "sim", "yes", "on"}
    if normalized == "json":
        if isinstance(value, dict | list):
            return value
        return json.loads(str(value))
    raise ValueError(f"Tipo de valor não suportado: {value_type}")


def evaluate_flow_condition(
    value: Any,
    operator: str,
    target: Any = "",
    *,
    field_exists: bool | None = None,
) -> bool:
    normalized = str(operator or "eq").lower().strip()
    exists = value is not None if field_exists is None else field_exists
    if normalized == "exists":
        return exists
    if normalized == "not_empty":
        if not exists or value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list | tuple | dict | set):
            return bool(value)
        return True

    value_text = "" if value is None else str(value)
    target_text = "" if target is None else str(target)
    if normalized == "eq":
        return value_text.casefold() == target_text.casefold()
    if normalized == "contains":
        return target_text.casefold() in value_text.casefold()
    if normalized in {"gt", "gte", "lt", "lte"}:
        try:
            numeric_value = float(value_text)
            numeric_target = float(target_text)
        except (TypeError, ValueError):
            return False
        if normalized == "gt":
            return numeric_value > numeric_target
        if normalized == "gte":
            return numeric_value >= numeric_target
        if normalized == "lt":
            return numeric_value < numeric_target
        return numeric_value <= numeric_target
    return False


def edge_route_key(edge: Mapping[str, Any]) -> str | None:
    for key in ("route_key", "routeKey", "condition_value", "conditionValue", "sourceHandle"):
        raw = edge.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    label = str(edge.get("label") or "").strip().casefold()
    if label in {"sim", "yes"}:
        return "true"
    if label in {"não", "nao", "no"}:
        return "false"
    return None


def select_router_route(
    config: Mapping[str, Any],
    context: Mapping[str, Any],
) -> FlowRouteDecision:
    raw_routes = config.get("routes")
    routes = (
        [route for route in raw_routes if isinstance(route, Mapping)]
        if isinstance(raw_routes, list)
        else []
    )
    fallback_key = str(config.get("fallback_route_key") or "fallback")
    for route in routes:
        route_key = str(route.get("key") or "").strip()
        if not route_key or bool(route.get("isFallback")) or route_key == fallback_key:
            continue
        field = str(route.get("field") or "").strip()
        operator = str(route.get("operator") or "eq")
        expected = route.get("value", "")
        sentinel = object()
        actual = get_context_value(context, field, sentinel)
        if evaluate_flow_condition(
            None if actual is sentinel else actual,
            operator,
            expected,
            field_exists=actual is not sentinel,
        ):
            return {
                "field": field,
                "operator": operator,
                "expected": expected,
                "actual": None if actual is sentinel else actual,
                "branch": route_key,
                "routeKey": route_key,
                "fallback": False,
            }
    return {
        "field": "",
        "operator": "fallback",
        "expected": None,
        "actual": None,
        "branch": fallback_key,
        "routeKey": fallback_key,
        "fallback": True,
    }


def apply_data_node(
    node_type: str,
    config: Mapping[str, Any],
    context: dict[str, Any],
) -> dict[str, Any] | None:
    """Apply one side-effect-free data node and return trace metadata."""
    if node_type in {"debounce", "dedupe", "checkpoint", "retry", "timeout", "cancel"}:
        controls = dict(context.get("flowControls") or {})
        key = str(config.get("key") or config.get("name") or node_type)
        if node_type == "debounce":
            controls[f"debounce:{key}"] = {
                "delaySeconds": max(0, int(config.get("delay_seconds", 0) or 0))
            }
        elif node_type == "dedupe":
            seen = set(controls.get("dedupeKeys") or [])
            duplicate = key in seen
            seen.add(key)
            controls["dedupeKeys"] = sorted(seen)
            controls[f"dedupe:{key}"] = {"duplicate": duplicate}
        elif node_type == "checkpoint":
            controls[f"checkpoint:{key}"] = {
                "state": "reached",
                "version": int(config.get("version", 1) or 1),
            }
        elif node_type == "retry":
            attempts = int((controls.get("retryAttempts") or {}).get(key, 0)) + 1
            controls.setdefault("retryAttempts", {})[key] = attempts
            controls[f"retry:{key}"] = {
                "attempt": attempts,
                "maxAttempts": int(config.get("max_attempts", 3) or 3),
            }
        elif node_type == "timeout":
            controls[f"timeout:{key}"] = {
                "timeoutSeconds": max(1, int(config.get("timeout_seconds", 60) or 60))
            }
        else:
            controls["cancelRequested"] = True
        context["flowControls"] = controls
        return {"control": node_type, "key": key}

    if node_type.startswith("load_"):
        source = node_type.removeprefix("load_")
        raw = context.get(source)
        context[str(config.get("target") or source)] = (
            raw if isinstance(raw, (dict, list, str, int, float, bool)) else None
        )
        return {"loaded": source, "present": raw is not None}
    if node_type == "validate_data":
        required = [str(item) for item in (config.get("required") or [])]
        missing = [item for item in required if get_context_value(context, item, None) is None]
        return {"valid": not missing, "missing": missing}
    if node_type == "mask_data":
        paths = [str(item) for item in (config.get("paths") or [])]
        for path in paths:
            if path in context:
                context[path] = "••••"
        return {"maskedPaths": paths}
    if node_type == "data_quality":
        fields = [str(item) for item in (config.get("fields") or [])]
        present = sum(get_context_value(context, field, None) is not None for field in fields)
        return {
            "total": len(fields),
            "present": present,
            "score": present / len(fields) if fields else 1.0,
        }
    if node_type == "compare_versions":
        left = resolve_expression(config.get("left"), context)
        right = resolve_expression(config.get("right"), context)
        return {"equal": left == right}
    if node_type == "rag_query":
        chunks = context.get("knowledgeChunks") or []
        return {
            "count": len(chunks),
            "sources": [str(item.get("sourceType")) for item in chunks if isinstance(item, dict)],
        }

    if node_type == "set_variable":
        variable_name = str(config.get("variable_name") or "").strip()
        resolved = resolve_expression(config.get("value_expression", config.get("value")), context)
        value = coerce_flow_value(resolved, str(config.get("value_type") or "auto"))
        set_context_value(context, variable_name, value)
        return {"variable": variable_name, "valueType": type(value).__name__}

    if node_type == "transform":
        mappings = config.get("mappings")
        if not isinstance(mappings, Mapping):
            raise ValueError("Nó transform exige mappings")
        resolved_values = {
            str(target): resolve_expression(expression, context)
            for target, expression in mappings.items()
            if str(target).strip()
        }
        for target, value in resolved_values.items():
            set_context_value(context, target, value)
        return {"mappedFields": sorted(resolved_values)}

    if node_type == "aggregate":
        source_path = str(config.get("source_path") or "").strip()
        target = str(config.get("target_variable") or "").strip()
        operation = str(config.get("aggregate_operation") or "count").strip().lower()
        source = get_context_value(context, source_path, [])
        items = list(source) if isinstance(source, list | tuple | set) else []
        if operation == "count":
            result: Any = len(items)
        elif operation == "sum":
            result = sum(float(item or 0) for item in items)
        elif operation == "first":
            result = items[0] if items else None
        elif operation == "last":
            result = items[-1] if items else None
        elif operation == "join":
            result = str(config.get("separator") or ", ").join(str(item) for item in items)
        else:
            raise ValueError(f"Operação de agregação não suportada: {operation}")
        set_context_value(context, target, result)
        return {
            "source": source_path,
            "target": target,
            "operation": operation,
            "items": len(items),
        }

    if node_type in {"link_action", "landing_page"}:
        raw_url = resolve_expression(config.get("url") or "", context)
        url = str(raw_url or "").strip()
        if not url:
            raise ValueError("Link resolvido não pode ser vazio")
        if not (url.startswith("https://") or url.startswith("http://") or url.startswith("/")):
            raise ValueError("Link deve usar http(s) ou uma rota interna iniciada por /")
        link = {
            "kind": str(
                config.get("link_kind")
                or ("landing_page" if node_type == "landing_page" else "website")
            ),
            "title": str(resolve_expression(config.get("link_title") or "Abrir", context)),
            "url": url,
            "target": str(config.get("target") or "customer"),
            "openInNewTab": bool(config.get("open_in_new_tab", True)),
        }
        target_variable = str(config.get("target_variable") or "lastLink").strip()
        set_context_value(context, target_variable, link)
        links = context.setdefault("flowLinks", [])
        if not isinstance(links, list):
            links = []
            context["flowLinks"] = links
        if link not in links:
            links.append(link)
        return {"link": link, "target": target_variable}

    if node_type == "form":
        fields = config.get("fields")
        if not isinstance(fields, list) or not fields:
            raise ValueError("Formulário exige fields")
        form = {
            "fields": [dict(field) for field in fields if isinstance(field, dict)],
            "submitLabel": str(config.get("submit_label") or "Enviar"),
            "status": "aguardando_envio",
        }
        context["pendingForm"] = form
        return {"form": form}

    if node_type == "end":
        context["flowEnded"] = {
            "status": str(config.get("end_status") or "completed"),
            "reason": str(config.get("reason") or "Fluxo encerrado"),
        }
        return dict(context["flowEnded"])

    if node_type == "error_handler":
        context["lastFlowErrorHandled"] = context.get("lastFlowError")
        if bool(config.get("clear_error", True)):
            context.pop("lastFlowError", None)
        return {"handled": True}

    return None


def advance_loop(
    node_id: str,
    config: Mapping[str, Any],
    context: dict[str, Any],
) -> FlowRouteDecision:
    source_path = str(config.get("collection_path") or "").strip()
    collection = get_context_value(context, source_path, [])
    items = list(collection) if isinstance(collection, list | tuple) else []
    max_iterations = max(1, min(int(config.get("max_iterations") or 100), 500))
    state = context.setdefault("flowLoopState", {})
    if not isinstance(state, dict):
        state = {}
        context["flowLoopState"] = state
    index = int(state.get(node_id, 0) or 0)
    if index < min(len(items), max_iterations):
        item_variable = str(config.get("item_variable") or "item")
        index_variable = str(config.get("index_variable") or "index")
        set_context_value(context, item_variable, items[index])
        set_context_value(context, index_variable, index)
        state[node_id] = index + 1
        return {
            "field": source_path,
            "operator": "iterate",
            "expected": min(len(items), max_iterations),
            "actual": index,
            "branch": "loop",
            "routeKey": "loop",
            "fallback": False,
        }
    state.pop(node_id, None)
    return {
        "field": source_path,
        "operator": "complete",
        "expected": min(len(items), max_iterations),
        "actual": index,
        "branch": "done",
        "routeKey": "done",
        "fallback": False,
    }
