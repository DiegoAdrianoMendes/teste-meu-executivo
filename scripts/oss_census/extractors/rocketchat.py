"""Fail-closed extractor for the pinned Rocket.Chat REST/DDP/UI/settings tree."""

from __future__ import annotations

import re
from pathlib import Path

from scripts.oss_census.common import (
    DEFAULT_EXCLUDES,
    excluded_candidate,
    find_balanced,
    http_capability,
    inventory_scope,
    javascript_object_members,
    manual_candidate,
    normalize_route,
    observation,
    parse_result,
    text_scope,
)
from scripts.oss_census.model import Candidate, ExtractionResult, Observation, ParseResult

UPSTREAM = "rocketchat"
EXTRACTOR = "rocketchat-rest-ddp-ui-v2"
_METHODS = "get|post|put|patch|delete|options|head"
_HTTP_METHODS = {item.upper() for item in _METHODS.split("|")}

_FLUENT_ROUTE = re.compile(
    rf"(?:\bAPI(?:\.v1)?|^\s*)\s*\.(?P<method>{_METHODS})\s*\(\s*"
    r"(?P<quote>['\"`])(?P<route>[^'\"`]+)(?P=quote)",
    re.MULTILINE | re.IGNORECASE,
)
_DIRECT_ROUTE = re.compile(
    rf"\b(?:API(?:\.v1)?|api)\.(?P<method>{_METHODS})\s*\(\s*"
    r"(?P<quote>['\"`])(?P<route>[^'\"`]+)(?P=quote)",
    re.IGNORECASE,
)
_ADD_ROUTE = re.compile(
    r"\b(?:API(?:\.v1)?|api)\.addRoute\s*\(\s*"
    r"(?P<quote>['\"`])(?P<route>[^'\"`]+)(?P=quote)",
    re.IGNORECASE,
)


def _nearby_permission(text: str, start: int, end: int) -> list[str]:
    window = text[start:end]
    permission_match = re.search(r"permissionsRequired\s*:\s*\[([^\]]*)\]", window)
    if not permission_match:
        return []
    return re.findall(r"['\"]([^'\"]+)['\"]", permission_match.group(1))


def _top_level_object_openings(text: str, opening: int, closing: int) -> list[int]:
    result: list[int] = []
    index = opening + 1
    parentheses = 0
    braces = 0
    brackets = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    while index < closing:
        char = text[index]
        nxt = text[index + 1] if index + 1 < closing else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and nxt == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and nxt == "*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if char == "(" and braces == 0 and brackets == 0:
            parentheses += 1
        elif char == ")" and braces == 0 and brackets == 0:
            parentheses -= 1
        elif char == "[" and braces == 0:
            brackets += 1
        elif char == "]" and braces == 0:
            brackets -= 1
        elif char == "{":
            if parentheses == 0 and braces == 0 and brackets == 0:
                result.append(index)
            braces += 1
        elif char == "}":
            braces -= 1
        index += 1
    if parentheses != 0 or braces != 0 or brackets != 0 or quote is not None:
        raise ValueError(f"Unbalanced Rocket.Chat call at offset {opening}")
    return result


def _rest_observation(
    *,
    path: str,
    text: str,
    offset: int,
    declaration: str,
    method: str,
    route: str,
    handler: str,
    permissions: list[str],
    candidate_kind: str,
) -> Observation:
    normalized_route = normalize_route(route)
    return observation(
        upstream=UPSTREAM,
        category=http_capability(normalized_route),
        boundary_type="http_route",
        family="rocket-chat-rest-route",
        transport="http",
        operation=method,
        route=normalized_route,
        handler=handler,
        name=f"{method} {normalized_route}",
        path=path,
        text=text,
        offset=offset,
        extractor=EXTRACTOR,
        candidate_kind=candidate_kind,
        declaration=declaration,
        details={"framework": "rocket-chat-rest", "permissions": permissions},
    )


def _rest_routes(path: str, text: str) -> ParseResult:
    items: list[Observation] = []
    candidates: list[Candidate] = []
    occupied: set[tuple[int, str, str]] = set()

    for pattern in (_DIRECT_ROUTE, _FLUENT_ROUTE):
        for match in pattern.finditer(text):
            method = match.group("method").upper()
            route = match.group("route")
            identity = (match.start(), method, route)
            if identity in occupied:
                continue
            occupied.add(identity)
            opening = text.find("(", match.start(), match.end())
            closing = find_balanced(text, opening, "(", ")")
            declaration = text[match.start() : closing + 1]
            items.append(
                _rest_observation(
                    path=path,
                    text=text,
                    offset=match.start(),
                    declaration=declaration,
                    method=method,
                    route=route,
                    handler=f"route.{method.lower()}",
                    permissions=_nearby_permission(text, match.start(), closing + 1),
                    candidate_kind="rocket_rest_route",
                )
            )

    for match in _ADD_ROUTE.finditer(text):
        route = match.group("route")
        opening = text.find("(", match.start(), match.end())
        closing = find_balanced(text, opening, "(", ")")
        object_openings = _top_level_object_openings(text, opening, closing)
        declaration = text[match.start() : closing + 1]
        if not object_openings:
            candidates.append(
                manual_candidate(
                    path=path,
                    text=text,
                    offset=match.start(),
                    kind="rocket_add_route",
                    reason="add_route_without_static_handler_object",
                    declaration=declaration,
                    details={"route": route},
                )
            )
            continue
        handler_opening = object_openings[-1]
        members, handler_closing = javascript_object_members(text, handler_opening)
        methods = sorted({name.upper() for name, _offset in members} & _HTTP_METHODS)
        if not methods:
            candidates.append(
                manual_candidate(
                    path=path,
                    text=text,
                    offset=match.start(),
                    kind="rocket_add_route",
                    reason="add_route_without_static_http_method",
                    declaration=declaration,
                    details={"route": route},
                )
            )
            continue
        permissions = _nearby_permission(text, match.start(), handler_closing + 1)
        for method in methods:
            items.append(
                _rest_observation(
                    path=path,
                    text=text,
                    offset=match.start(),
                    declaration=declaration,
                    method=method,
                    route=route,
                    handler=f"addRoute.{method.lower()}",
                    permissions=permissions,
                    candidate_kind="rocket_add_route",
                )
            )
    return parse_result(items, candidates)


_PUBLISH = re.compile(
    r"\bMeteor\.publish(?:Composite)?\s*\(\s*(['\"`])([^'\"`]+)\1",
    re.MULTILINE,
)
_METEOR_METHODS = re.compile(r"\bMeteor\.methods(?:<[^;{}]+>)?\s*\(")


def _ddp_contracts(path: str, text: str) -> ParseResult:
    items: list[Observation] = []
    candidates: list[Candidate] = []
    for match in _PUBLISH.finditer(text):
        channel = match.group(2)
        items.append(
            observation(
                upstream=UPSTREAM,
                category="realtime",
                boundary_type="realtime_event",
                family="meteor-ddp-publication",
                transport="ddp",
                operation="subscribe",
                route=channel,
                handler="Meteor.publish",
                name=channel,
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="meteor_publication",
                details={"framework": "meteor-ddp", "contract": "publication"},
            )
        )

    for match in _METEOR_METHODS.finditer(text):
        opening = text.rfind("(", match.start(), match.end())
        closing = find_balanced(text, opening, "(", ")")
        cursor = opening + 1
        while cursor < closing and text[cursor].isspace():
            cursor += 1
        declaration = text[match.start() : closing + 1]
        if cursor >= closing or text[cursor] != "{":
            adapter_prefix = text[max(0, match.start() - 500) : match.start()]
            dynamic_adapter = re.search(
                r"registerMethod\s*\([^)]*\)\s*:\s*void\s*\{\s*$",
                adapter_prefix,
                re.MULTILINE,
            )
            candidate_factory = excluded_candidate if dynamic_adapter else manual_candidate
            candidates.append(
                candidate_factory(
                    path=path,
                    text=text,
                    offset=match.start(),
                    kind="meteor_methods",
                    reason=(
                        "dynamic_registration_adapter"
                        if dynamic_adapter
                        else "dynamic_method_map_requires_manual_review"
                    ),
                    declaration=declaration,
                )
            )
            continue
        members, _object_closing = javascript_object_members(text, cursor)
        if not members:
            candidates.append(
                manual_candidate(
                    path=path,
                    text=text,
                    offset=match.start(),
                    kind="meteor_methods",
                    reason="empty_or_dynamic_method_map",
                    declaration=declaration,
                )
            )
            continue
        for method_name, member_offset in members:
            items.append(
                observation(
                    upstream=UPSTREAM,
                    category="realtime",
                    boundary_type="realtime_event",
                    family="meteor-ddp-method",
                    transport="ddp",
                    operation=method_name,
                    route="/methods",
                    handler=method_name,
                    name=method_name,
                    path=path,
                    text=text,
                    offset=member_offset,
                    extractor=EXTRACTOR,
                    candidate_kind="meteor_methods",
                    declaration=declaration,
                    details={"framework": "meteor-ddp", "contract": "method"},
                )
            )
    return parse_result(items, candidates)


_PERMISSION = re.compile(
    r"\{\s*_id\s*:\s*(['\"])(?P<name>[A-Za-z0-9_.:-]+)\1\s*,\s*roles\s*:\s*\[(?P<roles>[^\]]*)\]"
)


def _permissions(path: str, text: str) -> ParseResult:
    items: list[Observation] = []
    for match in _PERMISSION.finditer(text):
        permission_name = match.group("name")
        roles = re.findall(r"['\"]([^'\"]+)['\"]", match.group("roles"))
        items.append(
            observation(
                upstream=UPSTREAM,
                category="permission",
                boundary_type="permission_rule",
                family="rocket-chat-permission",
                transport="authorization",
                operation="evaluate",
                route=permission_name,
                handler=permission_name,
                name=permission_name,
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="rocket_permission",
                details={"framework": "rocket-chat-permissions", "defaultRoles": roles},
            )
        )
    return parse_result(items)


_SETTING = re.compile(
    r"\b(?:this|[A-Za-z_$][\w$]*\._settings|settingsRegistry)\.add\s*\(\s*"
    r"(['\"])(?P<name>[^'\"]+)\1"
)


def _settings(path: str, text: str) -> ParseResult:
    items: list[Observation] = []
    for match in _SETTING.finditer(text):
        setting_name = match.group("name")
        items.append(
            observation(
                upstream=UPSTREAM,
                category="config",
                boundary_type="configuration_item",
                family="rocket-chat-setting",
                transport="configuration",
                operation="read_write",
                route=setting_name,
                handler="settingsRegistry",
                name=setting_name,
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="rocket_setting",
                details={"framework": "rocket-chat-settings"},
            )
        )
    return parse_result(items)


def _cron_jobs(path: str, text: str) -> ParseResult:
    assignments = {
        match.group("variable"): match.group("value")
        for match in re.finditer(
            r"\bconst\s+(?P<variable>[A-Za-z_$][\w$]*)\s*=\s*['\"](?P<value>[^'\"]+)['\"]",
            text,
        )
    }
    items: list[Observation] = []
    candidates: list[Candidate] = []
    for match in re.finditer(
        r"\bcronJobs\.add\s*\(\s*(?:(['\"])(?P<literal>[^'\"]+)\1|(?P<variable>[A-Za-z_$][\w$]*))",
        text,
    ):
        job_name = match.group("literal") or assignments.get(match.group("variable") or "")
        if not job_name:
            candidates.append(
                manual_candidate(
                    path=path,
                    text=text,
                    offset=match.start(),
                    kind="rocket_cron_job",
                    reason="dynamic_cron_name_requires_manual_review",
                )
            )
            continue
        items.append(
            observation(
                upstream=UPSTREAM,
                category="job",
                boundary_type="scheduled_job",
                family="rocket-chat-cron-job",
                transport="scheduler",
                operation="execute",
                route=job_name,
                handler=job_name,
                name=job_name,
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="rocket_cron_job",
                details={"framework": "rocket-chat-cron"},
            )
        )
    return parse_result(items, candidates)


_UI_PATH = re.compile(r"\bpath\s*:\s*(['\"`])(?P<route>[^'\"`]*)\1")
_REGISTER_ROUTE = re.compile(
    r"\b(?P<register>register[A-Za-z0-9_$]*Route)\s*\(\s*"
    r"(['\"`])(?P<route>[^'\"`]*)\2"
)


def _ui_routes(path: str, text: str) -> ParseResult:
    items: list[Observation] = []
    for index, match in enumerate(_UI_PATH.finditer(text), start=1):
        route = normalize_route(match.group("route"))
        window = text[match.start() : min(len(text), match.end() + 500)]
        name_match = re.search(r"\b(?:id|name)\s*:\s*['\"`]([^'\"`]+)", window)
        name = name_match.group(1) if name_match else f"route-{index}:{route}"
        items.append(
            observation(
                upstream=UPSTREAM,
                category="ui",
                boundary_type="ui_route",
                family="rocket-chat-ui-route",
                transport="browser",
                operation="navigate",
                route=route,
                handler=name,
                name=name,
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="rocket_ui_route",
                aliases=(name,),
                details={"framework": "rocket-chat-router"},
            )
        )
    for match in _REGISTER_ROUTE.finditer(text):
        route = normalize_route(match.group("route"))
        window = text[match.end() : min(len(text), match.end() + 500)]
        name_match = re.search(r"\bname\s*:\s*['\"`]([^'\"`]+)", window)
        name = name_match.group(1) if name_match else f"{match.group('register')}:{route}"
        items.append(
            observation(
                upstream=UPSTREAM,
                category="ui",
                boundary_type="ui_route",
                family="rocket-chat-registered-ui-route",
                transport="browser",
                operation="navigate",
                route=route,
                handler=name,
                name=name,
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="rocket_registered_ui_route",
                aliases=(name,),
                details={
                    "framework": "rocket-chat-router",
                    "register": match.group("register"),
                },
            )
        )
    return parse_result(items)


SOURCE_PATTERNS = (
    "apps/meteor/app/api/server/v1/**/*.ts",
    "apps/meteor/app/api/server/v1/**/*.js",
    "apps/meteor/app/**/server/api/**/*.ts",
    "apps/meteor/app/**/server/api/**/*.js",
    "apps/meteor/app/**/server/rest/**/*.ts",
    "apps/meteor/app/**/server/rest/**/*.js",
    "apps/meteor/app/**/server/**/*.ts",
    "apps/meteor/app/**/server/**/*.js",
    "apps/meteor/imports/**/server/**/*.ts",
    "apps/meteor/imports/**/server/**/*.js",
    "apps/meteor/server/publications/**/*.ts",
    "apps/meteor/server/publications/**/*.js",
    "apps/meteor/app/authorization/server/constant/permissions.ts",
    "apps/meteor/server/settings/**/*.ts",
    "apps/meteor/server/settings/**/*.js",
    "apps/meteor/app/**/server/**/*settings*.ts",
    "apps/meteor/app/**/server/**/*settings*.js",
    "apps/meteor/server/cron/**/*.ts",
    "apps/meteor/server/cron/**/*.js",
    "apps/meteor/client/**/*routes*.ts",
    "apps/meteor/client/**/*routes*.tsx",
    "apps/meteor/client/**/*Router.ts",
    "apps/meteor/client/**/*Router.tsx",
    "apps/meteor/tests/**/*.ts",
    "apps/meteor/tests/**/*.js",
)


def extract(root: Path) -> ExtractionResult:
    source_excludes = DEFAULT_EXCLUDES + (
        "tests",
        "test",
        "__tests__",
        "spec",
        "stories",
    )
    scopes = (
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="rest-api",
            extractor=EXTRACTOR,
            includes=SOURCE_PATTERNS[:6],
            excludes=source_excludes,
            parser=_rest_routes,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="meteor-ddp",
            extractor=EXTRACTOR,
            includes=SOURCE_PATTERNS[6:12],
            excludes=source_excludes,
            parser=_ddp_contracts,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="permission-catalog",
            extractor=EXTRACTOR,
            includes=("apps/meteor/app/authorization/server/constant/permissions.ts",),
            parser=_permissions,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="settings-catalog",
            extractor=EXTRACTOR,
            includes=SOURCE_PATTERNS[13:17],
            excludes=source_excludes,
            parser=_settings,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="scheduled-jobs",
            extractor=EXTRACTOR,
            includes=SOURCE_PATTERNS[17:19],
            excludes=source_excludes,
            parser=_cron_jobs,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="react-public-routes",
            extractor=EXTRACTOR,
            includes=SOURCE_PATTERNS[19:23],
            excludes=source_excludes,
            parser=_ui_routes,
        ),
        inventory_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="upstream-tests",
            includes=SOURCE_PATTERNS[23:25],
            classification="supporting_test",
        ),
    )
    return ExtractionResult(upstream=UPSTREAM, root=root, scopes=scopes)
