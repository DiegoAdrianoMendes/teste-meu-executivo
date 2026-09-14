"""Fail-closed extractor for the pinned Chatwoot Rails/Vue community tree."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from scripts.oss_census.common import (
    DEFAULT_EXCLUDES,
    ExtractionError,
    excluded_candidate,
    http_capability,
    inventory_scope,
    javascript_brace_regions,
    javascript_object_members,
    normalize_route,
    observation,
    parse_result,
    text_scope,
)
from scripts.oss_census.model import Candidate, ExtractionResult, Observation, ParseResult

UPSTREAM = "chatwoot"
EXTRACTOR = "chatwoot-rails-vue-v2"

_PLURAL_ACTIONS = ("index", "show", "create", "update", "destroy", "new", "edit")
_SINGULAR_ACTIONS = ("show", "create", "update", "destroy", "new", "edit")
_ACTION_ROUTES = {
    "index": (("GET", "collection"),),
    "show": (("GET", "member"),),
    "new": (("GET", "new"),),
    "edit": (("GET", "edit"),),
    "create": (("POST", "collection"),),
    "update": (("PATCH", "member"), ("PUT", "member")),
    "destroy": (("DELETE", "member"),),
}
_HTTP_VERBS = ("get", "post", "put", "patch", "delete", "options", "head")

_TOKEN_AUTH_ROUTES = (
    ("POST", "", "registrations#create"),
    ("PUT", "", "registrations#update"),
    ("PATCH", "", "registrations#update"),
    ("DELETE", "", "registrations#destroy"),
    ("POST", "sign_in", "sessions#create"),
    ("DELETE", "sign_out", "sessions#destroy"),
    ("GET", "validate_token", "token_validations#validate_token"),
    ("POST", "password", "passwords#create"),
    ("PUT", "password", "passwords#update"),
    ("PATCH", "password", "passwords#update"),
    ("GET", "password/edit", "passwords#edit"),
    ("POST", "confirmation", "confirmations#create"),
    ("GET", "confirmation", "confirmations#show"),
    ("GET", ":omniauth_prefix/:provider", "omniauth_callbacks#omniauth_success"),
)

_DEVISE_ROUTES = (
    ("GET", "sign_in", "sessions#new"),
    ("POST", "sign_in", "sessions#create"),
    ("DELETE", "sign_out", "sessions#destroy"),
    ("GET", "password/new", "passwords#new"),
    ("GET", "password/edit", "passwords#edit"),
    ("PATCH", "password", "passwords#update"),
    ("PUT", "password", "passwords#update"),
    ("POST", "password", "passwords#create"),
    ("GET", "cancel", "registrations#cancel"),
    ("GET", "sign_up", "registrations#new"),
    ("GET", "edit", "registrations#edit"),
    ("PATCH", "", "registrations#update"),
    ("PUT", "", "registrations#update"),
    ("DELETE", "", "registrations#destroy"),
    ("POST", "", "registrations#create"),
)


@dataclass(frozen=True)
class _Frame:
    kind: str
    path: str
    controller: str | None = None
    resource_name: str | None = None
    singular: bool = False
    parameter: str = "id"
    route_mode: str | None = None
    proprietary: bool = False
    condition: str | None = None


def _ruby_symbols(value: str) -> list[str]:
    percent = re.search(r"%i(?:\[([^\]]*)\]|\(([^)]*)\)|\{([^}]*)\})", value)
    if percent:
        body = next(group for group in percent.groups() if group is not None)
        return re.findall(r"[A-Za-z_][A-Za-z0-9_!?]*", body)
    bracket = re.search(r"\[([^\]]*)\]", value)
    if bracket:
        return re.findall(r":([A-Za-z_][A-Za-z0-9_!?]*)", bracket.group(1))
    symbol = re.search(r":([A-Za-z_][A-Za-z0-9_!?]*)", value)
    return [symbol.group(1)] if symbol else []


def _ruby_value(statement: str, option: str) -> str | None:
    match = re.search(
        rf"\b{re.escape(option)}:\s*(?:['\"]([^'\"]+)['\"]|:([A-Za-z_][\w!?]*))",
        statement,
    )
    if not match:
        return None
    return match.group(1) or match.group(2)


def _ruby_aliases(statement: str) -> tuple[str, ...]:
    alias = _ruby_value(statement, "as")
    return (alias,) if alias else ()


def _ruby_hash(statement: str, option: str) -> dict[str, str]:
    match = re.search(rf"\b{re.escape(option)}:\s*\{{(?P<body>.*?)\}}", statement)
    if not match:
        return {}
    return {
        item.group("key"): item.group("value")
        for item in re.finditer(
            r"(?P<key>[A-Za-z_][\w!?]*)\s*:\s*['\"](?P<value>[^'\"]+)['\"]",
            match.group("body"),
        )
    }


def _route_statement(lines: list[str], index: int) -> str:
    parts = [lines[index].strip()]
    for next_line in lines[index + 1 : index + 20]:
        joined = " ".join(parts)
        balanced = all(
            joined.count(opening) <= joined.count(closing)
            for opening, closing in (("[", "]"), ("(", ")"), ("{", "}"))
        )
        if balanced and not parts[-1].rstrip().endswith(","):
            break
        parts.append(next_line.strip())
    statement = " ".join(parts)
    if any(statement.count(opening) > statement.count(closing) for opening, closing in (("[", "]"), ("(", ")"), ("{", "}"))):
        raise ExtractionError(f"Unbalanced Rails route statement at line {index + 1}")
    return statement


def _offsets(text: str) -> list[int]:
    result = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        result.append(cursor)
        cursor += len(line)
    return result


def _current_frame(stack: list[_Frame], kind: str) -> _Frame | None:
    return next((frame for frame in reversed(stack) if frame.kind == kind), None)


def _current_path(stack: list[_Frame]) -> str:
    return stack[-1].path if stack else "/"


def _nesting_path(stack: list[_Frame]) -> str:
    if stack and stack[-1].kind == "resource":
        return _resource_paths(stack[-1])["member"]
    return _current_path(stack)


def _conditions(stack: list[_Frame]) -> list[str]:
    return [frame.condition for frame in stack if frame.condition]


def _proprietary(stack: list[_Frame], statement: str) -> bool:
    return any(frame.proprietary for frame in stack) or "ChatwootApp.enterprise?" in statement


def _controller_for(stack: list[_Frame], fallback: str) -> str:
    resource = _current_frame(stack, "resource")
    return resource.controller if resource and resource.controller else fallback


def _resource_paths(frame: _Frame) -> dict[str, str]:
    collection = frame.path
    member = collection if frame.singular else normalize_route(collection, f":{frame.parameter}")
    return {
        "collection": collection,
        "member": member,
        "new": normalize_route(collection, "new"),
        "edit": normalize_route(member, "edit"),
    }


def _route_observation(
    *,
    path: str,
    text: str,
    offset: int,
    statement: str,
    method: str,
    route: str,
    handler: str,
    family: str,
    candidate_kind: str,
    aliases: tuple[str, ...] = (),
    details: dict[str, object] | None = None,
) -> Observation:
    capability = http_capability(route)
    return observation(
        upstream=UPSTREAM,
        category=capability,
        boundary_type="http_route",
        family=family,
        transport="http",
        operation=method,
        route=route,
        handler=handler,
        name=f"{method} {route} -> {handler}",
        path=path,
        text=text,
        offset=offset,
        extractor=EXTRACTOR,
        candidate_kind=candidate_kind,
        declaration=statement,
        aliases=aliases,
        facets=("conditional",) if details and details.get("conditions") else (),
        details={"framework": "rails", **(details or {})},
    )


def _resource_observations(
    *,
    path: str,
    text: str,
    offset: int,
    statement: str,
    frame: _Frame,
    actions: list[str],
    conditions: list[str],
) -> list[Observation]:
    paths = _resource_paths(frame)
    items: list[Observation] = []
    for action in actions:
        if action not in _ACTION_ROUTES:
            raise ExtractionError(f"Unknown Rails REST action {action!r} in {path}")
        for method, route_kind in _ACTION_ROUTES[action]:
            items.append(
                _route_observation(
                    path=path,
                    text=text,
                    offset=offset,
                    statement=statement,
                    method=method,
                    route=paths[route_kind],
                    handler=f"{frame.controller}#{action}",
                    family="rails-resource-route",
                    candidate_kind="rails_resource",
                    aliases=_ruby_aliases(statement),
                    details={
                        "resourceKind": "resource" if frame.singular else "resources",
                        "action": action,
                        "conditions": conditions,
                    },
                )
            )
    return items


def _custom_route(
    *,
    stack: list[_Frame],
    verb: str,
    target: str,
    statement: str,
) -> tuple[str, str]:
    resource = _current_frame(stack, "resource")
    explicit_on = _ruby_value(statement, "on")
    mode_frame = _current_frame(stack, "route_mode")
    mode = explicit_on or (mode_frame.route_mode if mode_frame else None)
    target_is_symbol = target.startswith(":")
    fragment = target[1:] if target_is_symbol else target.strip("'\"")
    if resource is None:
        route = normalize_route(_current_path(stack), fragment)
        default_controller = fragment.strip("/").split("/", maxsplit=1)[0] or "application"
    else:
        paths = _resource_paths(resource)
        if mode == "collection":
            base = paths["collection"]
        elif mode == "new":
            base = paths["new"]
        else:
            base = paths["member"]
        route = normalize_route(base, fragment)
        default_controller = resource.controller or resource.resource_name or "application"

    handler = _ruby_value(statement, "to")
    action_override = _ruby_value(statement, "action")
    if handler is None and "=>" in statement:
        hash_target = re.search(r"=>\s*['\"]([^'\"]+)['\"]", statement)
        handler = hash_target.group(1) if hash_target else None
    if handler is None:
        action = action_override or (fragment if target_is_symbol else fragment.rsplit("/", 1)[-1])
        action = action.split(":", 1)[0]
        if not action or any(token in action for token in ("*", "(", ")")):
            raise ExtractionError(f"Cannot infer Rails handler for: {statement}")
        handler = f"{default_controller}#{action}"
    return route, handler


def _devise_observations(
    *,
    path: str,
    text: str,
    offset: int,
    statement: str,
    stack: list[_Frame],
    token_auth: bool,
) -> list[Observation]:
    base = _ruby_value(statement, "at" if token_auth else "path")
    if base is None:
        first = re.search(r"\b(?:mount_devise_token_auth_for|devise_for)\s+[:'\"]?([\w-]+)", statement)
        if not first:
            raise ExtractionError(f"Cannot parse Devise declaration: {statement}")
        base = first.group(1).lower()
    base_route = normalize_route(_current_path(stack), base)
    routes = _TOKEN_AUTH_ROUTES if token_auth else _DEVISE_ROUTES
    controllers = _ruby_hash(statement, "controllers")
    family = "rails-devise-token-auth-route" if token_auth else "rails-devise-route"
    kind = "rails_mount_devise_token_auth" if token_auth else "rails_devise_for"
    return [
        _route_observation(
            path=path,
            text=text,
            offset=offset,
            statement=statement,
            method=method,
            route=normalize_route(base_route, suffix),
            handler=(
                f"{controllers.get(handler.split('#', 1)[0], handler.split('#', 1)[0])}"
                f"#{handler.split('#', 1)[1]}"
            ),
            family=family,
            candidate_kind=kind,
            details={
                "macro": "mount_devise_token_auth_for" if token_auth else "devise_for",
                "expandedStatically": True,
                "conditions": _conditions(stack),
            },
        )
        for method, suffix, handler in routes
    ]


def _rails_routes(path: str, text: str) -> ParseResult:
    items: list[Observation] = []
    excluded: list[Candidate] = []
    lines = text.splitlines()
    offsets = _offsets(text)
    stack: list[_Frame] = []
    continuation_lines: set[int] = set()
    recognized_lines: set[int] = set()

    for index, raw_line in enumerate(lines):
        if index in continuation_lines:
            continue
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.fullmatch(r"end(?:\s*#.*)?", stripped):
            if not stack:
                raise ExtractionError(f"Unexpected Rails end in {path}:{index + 1}")
            stack.pop()
            continue
        if stripped == "else":
            if not stack or stack[-1].kind != "condition":
                raise ExtractionError(f"Unexpected Rails else in {path}:{index + 1}")
            previous = stack[-1]
            stack[-1] = replace(
                previous,
                proprietary=False,
                condition=f"else({previous.condition})",
            )
            continue

        statement = _route_statement(lines, index)
        consumed = statement.count("\n")
        # _route_statement normalizes newlines, so infer continuation by bracket completion.
        if statement != stripped:
            accumulated = stripped
            for next_index in range(index + 1, min(len(lines), index + 20)):
                continuation_lines.add(next_index)
                accumulated += " " + lines[next_index].strip()
                if accumulated == statement:
                    break
        offset = offsets[index]
        proprietary = _proprietary(stack, statement)
        conditions = _conditions(stack)
        inline_condition = re.search(r"\s+(if|unless)\s+(.+)$", statement)
        if inline_condition:
            conditions = [*conditions, f"{inline_condition.group(1)} {inline_condition.group(2)}"]

        token_auth = re.match(r"\s*mount_devise_token_auth_for\b", raw_line)
        devise = re.match(r"\s*devise_for\b", raw_line)
        if token_auth or devise:
            recognized_lines.add(index)
            if proprietary:
                excluded.append(
                    excluded_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="rails_devise_macro",
                        reason="proprietary_guard",
                        declaration=statement,
                    )
                )
            else:
                items.extend(
                    _devise_observations(
                        path=path,
                        text=text,
                        offset=offset,
                        statement=statement,
                        stack=stack,
                        token_auth=bool(token_auth),
                    )
                )

        resource_match = re.match(r"\s*(resources?|resource)\s+[:'\"]([\w-]+)", raw_line)
        resource_frame: _Frame | None = None
        if resource_match:
            recognized_lines.add(index)
            resource_kind, resource_name = resource_match.groups()
            singular = resource_kind == "resource"
            controller = _ruby_value(statement, "controller") or resource_name
            parameter = _ruby_value(statement, "param") or "id"
            collection_path = normalize_route(_nesting_path(stack), resource_name)
            resource_frame = _Frame(
                kind="resource",
                path=collection_path,
                controller=controller,
                resource_name=resource_name,
                singular=singular,
                parameter=parameter,
                proprietary=proprietary,
            )
            defaults = list(_SINGULAR_ACTIONS if singular else _PLURAL_ACTIONS)
            only_match = re.search(r"\bonly:\s*(.+?)(?:,\s*\w+:|\s+do\b|$)", statement)
            except_match = re.search(r"\bexcept:\s*(.+?)(?:,\s*\w+:|\s+do\b|$)", statement)
            if only_match:
                defaults = _ruby_symbols(only_match.group(1))
                if "[]" in only_match.group(1):
                    defaults = []
            elif except_match:
                excluded_actions = set(_ruby_symbols(except_match.group(1)))
                defaults = [action for action in defaults if action not in excluded_actions]
            if proprietary:
                excluded.append(
                    excluded_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="rails_resource",
                        reason="proprietary_guard",
                        declaration=statement,
                        details={"expandedRouteCount": sum(len(_ACTION_ROUTES[a]) for a in defaults)},
                    )
                )
            elif defaults:
                items.extend(
                    _resource_observations(
                        path=path,
                        text=text,
                        offset=offset,
                        statement=statement,
                        frame=resource_frame,
                        actions=defaults,
                        conditions=conditions,
                    )
                )
            else:
                excluded.append(
                    excluded_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="rails_resource",
                        reason="routing_container_only",
                        declaration=statement,
                    )
                )

        root_match = re.match(r"\s*root\b", raw_line)
        if root_match:
            recognized_lines.add(index)
            handler = _ruby_value(statement, "to")
            if not handler:
                direct_handler = re.match(
                    r"\s*root\s+(?:to:\s*)?(?:['\"]([^'\"]+)['\"]|:([A-Za-z_][\w!?]*))",
                    statement,
                )
                if direct_handler:
                    handler = direct_handler.group(1) or direct_handler.group(2)
            if not handler:
                raise ExtractionError(f"Rails root lacks a static handler in {path}:{index + 1}")
            if proprietary:
                excluded.append(
                    excluded_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="rails_root",
                        reason="proprietary_guard",
                        declaration=statement,
                    )
                )
            else:
                items.append(
                    _route_observation(
                        path=path,
                        text=text,
                        offset=offset,
                        statement=statement,
                        method="GET",
                        route=_current_path(stack),
                        handler=handler,
                        family="rails-root-route",
                        candidate_kind="rails_root",
                        aliases=_ruby_aliases(statement),
                        details={"conditions": conditions},
                    )
                )

        verb_match = re.match(
            rf"\s*({'|'.join(_HTTP_VERBS)})\s+(?P<target>:[A-Za-z_][\w!?]*|['\"][^'\"]+['\"])",
            raw_line,
        )
        if verb_match:
            recognized_lines.add(index)
            verb = verb_match.group(1).upper()
            target = verb_match.group("target")
            route, handler = _custom_route(
                stack=stack,
                verb=verb,
                target=target,
                statement=statement,
            )
            if proprietary:
                excluded.append(
                    excluded_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="rails_http_verb",
                        reason="proprietary_guard",
                        declaration=statement,
                    )
                )
            else:
                items.append(
                    _route_observation(
                        path=path,
                        text=text,
                        offset=offset,
                        statement=statement,
                        method=verb,
                        route=route,
                        handler=handler,
                        family="rails-custom-route",
                        candidate_kind="rails_http_verb",
                        aliases=_ruby_aliases(statement),
                        details={"conditions": conditions},
                    )
                )

        match_route = re.match(r"\s*match\s+['\"]([^'\"]+)", raw_line)
        if match_route:
            recognized_lines.add(index)
            via_match = re.search(r"\bvia:\s*(.+?)(?:,\s*\w+:|\s+do\b|$)", statement)
            methods = _ruby_symbols(via_match.group(1)) if via_match else []
            if not methods:
                raise ExtractionError(f"Rails match lacks static via methods in {path}:{index + 1}")
            target = repr(match_route.group(1))
            route, handler = _custom_route(
                stack=stack,
                verb="MATCH",
                target=target,
                statement=statement,
            )
            if proprietary:
                excluded.append(
                    excluded_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="rails_match",
                        reason="proprietary_guard",
                        declaration=statement,
                    )
                )
            else:
                for method in methods:
                    items.append(
                        _route_observation(
                            path=path,
                            text=text,
                            offset=offset,
                            statement=statement,
                            method=method.upper(),
                            route=route,
                            handler=handler,
                            family="rails-match-route",
                            candidate_kind="rails_match",
                            aliases=_ruby_aliases(statement),
                            details={"conditions": conditions},
                        )
                    )

        mount_match = re.match(r"\s*mount\s+(.+?)\s*(?:=>|,\s*at:)\s*['\"]([^'\"]+)", raw_line)
        if mount_match:
            recognized_lines.add(index)
            handler, route_fragment = mount_match.groups()
            route = normalize_route(_current_path(stack), route_fragment)
            if proprietary:
                excluded.append(
                    excluded_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="rails_mount",
                        reason="proprietary_guard",
                        declaration=statement,
                    )
                )
            else:
                items.append(
                    _route_observation(
                        path=path,
                        text=text,
                        offset=offset,
                        statement=statement,
                        method="ANY",
                        route=route,
                        handler=handler.strip(),
                        family="rails-rack-mount",
                        candidate_kind="rails_mount",
                        details={"conditions": conditions},
                    )
                )

        block_frame: _Frame | None = None
        namespace_match = re.match(r"\s*namespace\s+[:'\"]([\w-]+)", raw_line)
        scope_match = re.match(r"\s*scope(?:\s+[:'\"]([^'\"\s,]+)|\s+path:\s*['\"]([^'\"]+))?", raw_line)
        mode_match = re.match(r"\s*(member|collection|new)\s+do\b", raw_line)
        condition_match = re.match(r"\s*(if|unless)\s+(.+?)(?:\s+do)?$", raw_line)
        if namespace_match:
            block_frame = _Frame(
                kind="namespace",
                path=normalize_route(_nesting_path(stack), namespace_match.group(1)),
                proprietary=proprietary,
            )
        elif scope_match and re.search(r"\bdo\b", statement):
            scope_path = scope_match.group(1) or scope_match.group(2)
            block_frame = _Frame(
                kind="scope",
                path=normalize_route(_nesting_path(stack), scope_path or ""),
                proprietary=proprietary,
            )
        elif mode_match:
            block_frame = _Frame(
                kind="route_mode",
                path=_nesting_path(stack),
                route_mode=mode_match.group(1),
                proprietary=proprietary,
            )
        elif resource_frame and re.search(r"\bdo\b", statement):
            block_frame = resource_frame
        elif condition_match and not inline_condition:
            expression = f"{condition_match.group(1)} {condition_match.group(2)}"
            block_frame = _Frame(
                kind="condition",
                path=_nesting_path(stack),
                proprietary=proprietary or "ChatwootApp.enterprise?" in expression,
                condition=expression,
            )
        elif re.search(r"\bdo\s*(?:\|[^|]*\|)?\s*(?:#.*)?$", stripped):
            block_frame = _Frame(kind="block", path=_nesting_path(stack), proprietary=proprietary)
        if block_frame:
            stack.append(block_frame)

    route_start = re.compile(
        rf"^\s*(?:resources?|resource|root|match|mount|mount_devise_token_auth_for|devise_for|{'|'.join(_HTTP_VERBS)})\b"
    )
    unrecognized = [
        index + 1
        for index, line in enumerate(lines)
        if route_start.match(line) and index not in recognized_lines and index not in continuation_lines
    ]
    if unrecognized:
        raise ExtractionError(f"Unparsed Rails route declarations in {path}: lines {unrecognized[:10]}")
    return parse_result(items, excluded)


def _jobs(path: str, text: str) -> ParseResult:
    items = []
    for match in re.finditer(
        r"^\s*class\s+([A-Z][A-Za-z0-9_:]*Job)\s*<\s*([A-Z][A-Za-z0-9_:]*)",
        text,
        re.MULTILINE,
    ):
        class_name, base = match.groups()
        items.append(
            observation(
                upstream=UPSTREAM,
                category="job",
                boundary_type="scheduled_job",
                family="active-job-class",
                transport="active_job",
                operation="perform",
                route=class_name,
                handler=class_name,
                name=class_name,
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="active_job_class",
                details={"framework": "active_job", "baseClass": base},
            )
        )
    return parse_result(items)


_JS_PATH = re.compile(
    r"\bpath\s*:\s*(?:(?P<helper>frontendURL|getPortalRoute)\s*\(\s*)?"
    r"(?P<quote>['\"`])(?P<route>[^'\"`]*)(?P=quote)",
    re.MULTILINE,
)


def _javascript_member_value(
    text: str,
    members: list[tuple[str, int]],
    closing: int,
    member_name: str,
) -> str | None:
    for index, (name, offset) in enumerate(members):
        if name != member_name:
            continue
        end = members[index + 1][1] if index + 1 < len(members) else closing
        segment = text[offset:end]
        colon = segment.find(":")
        if colon == -1:
            return None
        return segment[colon + 1 :].strip().rstrip(",").strip()
    return None


def _static_javascript_string(value: str | None) -> str | None:
    if not value:
        return None
    match = re.match(r"(['\"`])([^'\"`]*)\1", value)
    return match.group(2) if match else None


def _vue_handler(
    *,
    path: str,
    route_name: str | None,
    component_value: str | None,
    redirect_value: str | None,
) -> tuple[str, tuple[str, ...]]:
    component = None
    if component_value:
        direct = re.match(r"([A-Za-z_$][\w$]*)", component_value)
        imported = re.search(r"\bimport\(\s*['\"]([^'\"]+)['\"]\s*\)", component_value)
        component = direct.group(1) if direct else (f"import:{imported.group(1)}" if imported else None)
    redirect = None
    if redirect_value:
        redirect = _static_javascript_string(redirect_value)
        if redirect is None:
            named = re.search(r"\bname\s*:\s*['\"]([^'\"]+)['\"]", redirect_value)
            redirect = f"name:{named.group(1)}" if named else "dynamic"
    behavior = component or (f"redirect:{redirect}" if redirect else "route-object")
    identity = f"{route_name or behavior}@{path}"
    aliases = tuple(alias for alias in (route_name, component) if alias)
    return identity, aliases


def _vue_routes(path: str, text: str) -> ParseResult:
    items: list[Observation] = []
    regions = javascript_brace_regions(text)
    route_specs: list[dict[str, object]] = []
    for match in _JS_PATH.finditer(text):
        object_region: tuple[int, int] | None = None
        object_members: list[tuple[str, int]] = []
        for opening, closing in sorted(
            (region for region in regions if region[0] < match.start() < region[1]),
            reverse=True,
        ):
            members, member_closing = javascript_object_members(text, opening)
            if member_closing != closing:
                raise ExtractionError(f"JavaScript object boundary mismatch in {path}")
            if any(
                name == "path" and offset <= match.start() <= offset + len(name) + 2
                for name, offset in members
            ):
                object_region = (opening, closing)
                object_members = members
                break
        if object_region is None:
            raise ExtractionError(f"Vue path is not a static object member in {path}:{match.start()}")
        declared_route = match.group("route")
        route_name = _static_javascript_string(
            _javascript_member_value(text, object_members, object_region[1], "name")
        )
        component_value = _javascript_member_value(
            text, object_members, object_region[1], "component"
        )
        redirect_value = _javascript_member_value(
            text, object_members, object_region[1], "redirect"
        )
        handler, aliases = _vue_handler(
            path=path,
            route_name=route_name,
            component_value=component_value,
            redirect_value=redirect_value,
        )
        route_specs.append(
            {
                "opening": object_region[0],
                "closing": object_region[1],
                "match": match,
                "declaredRoute": declared_route,
                "absolute": bool(match.group("helper")) or declared_route.startswith("/"),
                "routeName": route_name,
                "handler": handler,
                "aliases": aliases,
            }
        )

    by_opening = {int(spec["opening"]): spec for spec in route_specs}

    def resolved_route(spec: dict[str, object], resolving: set[int] | None = None) -> str:
        opening = int(spec["opening"])
        if "resolvedRoute" in spec:
            return str(spec["resolvedRoute"])
        resolving = set() if resolving is None else resolving
        if opening in resolving:
            raise ExtractionError(f"Cyclic Vue route nesting in {path}:{opening}")
        resolving.add(opening)
        declared_route = str(spec["declaredRoute"])
        parent = next(
            (
                candidate
                for candidate in sorted(route_specs, key=lambda item: int(item["opening"]), reverse=True)
                if int(candidate["opening"]) < opening
                and int(spec["closing"]) < int(candidate["closing"])
            ),
            None,
        )
        if bool(spec["absolute"]) or parent is None:
            route = normalize_route(declared_route)
        else:
            route = normalize_route(resolved_route(by_opening[int(parent["opening"])], resolving), declared_route)
        resolving.remove(opening)
        spec["resolvedRoute"] = route
        return route

    for spec in route_specs:
        match = spec["match"]
        if not isinstance(match, re.Match):
            raise ExtractionError(f"Invalid Vue route parser state in {path}")
        route = resolved_route(spec)
        route_name = spec["routeName"]
        name = str(route_name) if route_name else f"route:{route}"
        items.append(
            observation(
                upstream=UPSTREAM,
                category="ui",
                boundary_type="ui_route",
                family="vue-router-route",
                transport="browser",
                operation="navigate",
                route=route,
                handler=str(spec["handler"]),
                name=name,
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="vue_route",
                aliases=tuple(str(alias) for alias in spec["aliases"]),
                details={
                    "framework": "vue-router",
                    "declaredRoute": str(spec["declaredRoute"]),
                    "resolvedRoute": route,
                    "routeName": route_name,
                },
            )
        )
    return parse_result(items)


def _yaml_config(path: str, text: str) -> ParseResult:
    items = []
    pattern = re.compile(r"^\s*-\s+(?:name|key|id):\s*['\"]?([^\s#'\"]+)", re.MULTILINE)
    for match in pattern.finditer(text):
        config_name = match.group(1)
        items.append(
            observation(
                upstream=UPSTREAM,
                category="config",
                boundary_type="configuration_item",
                family="chatwoot-yaml-configuration",
                transport="configuration",
                operation="read_write",
                route=config_name,
                handler=path,
                name=config_name,
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="yaml_configuration",
                details={"framework": "yaml", "configurationFile": path},
            )
        )
    return parse_result(items)


SOURCE_PATTERNS = (
    "config/routes.rb",
    "config/routes/**/*.rb",
    "app/jobs/**/*.rb",
    "app/javascript/dashboard/routes/**/*routes*.js",
    "app/javascript/dashboard/routes/**/routes.js",
    "app/javascript/widget/router.js",
    "app/javascript/v3/views/routes.js",
    "config/features.yml",
    "config/installation_config.yml",
    "config/integration/apps.yml",
    "config/agents/tools.yml",
    "spec/**/*_spec.rb",
    "app/javascript/**/specs/*.js",
    "app/javascript/**/specs/*.ts",
)


def extract(root: Path) -> ExtractionResult:
    scopes = (
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="rails-routes",
            extractor=EXTRACTOR,
            includes=("config/routes.rb", "config/routes/**/*.rb"),
            parser=_rails_routes,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="active-jobs",
            extractor=EXTRACTOR,
            includes=("app/jobs/**/*.rb",),
            parser=_jobs,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="vue-public-routes",
            extractor=EXTRACTOR,
            includes=(
                "app/javascript/dashboard/routes/**/*routes*.js",
                "app/javascript/dashboard/routes/**/routes.js",
                "app/javascript/widget/router.js",
                "app/javascript/v3/views/routes.js",
            ),
            excludes=DEFAULT_EXCLUDES + ("spec", "specs", "test", "tests"),
            parser=_vue_routes,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="public-configuration",
            extractor=EXTRACTOR,
            includes=(
                "config/features.yml",
                "config/installation_config.yml",
                "config/integration/apps.yml",
                "config/agents/tools.yml",
            ),
            parser=_yaml_config,
        ),
        inventory_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="upstream-tests",
            includes=(
                "spec/**/*_spec.rb",
                "app/javascript/**/specs/*.js",
                "app/javascript/**/specs/*.ts",
            ),
            classification="supporting_test",
        ),
    )
    return ExtractionResult(upstream=UPSTREAM, root=root, scopes=scopes)
