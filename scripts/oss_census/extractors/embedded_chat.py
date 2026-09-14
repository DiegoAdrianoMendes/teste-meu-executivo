"""Fail-closed extractor for the pinned Rocket EmbeddedChat package surface."""

from __future__ import annotations

import re
from pathlib import Path

from scripts.oss_census.common import (
    ExtractionError,
    find_balanced,
    inventory_scope,
    observation,
    parse_result,
    text_scope,
)
from scripts.oss_census.model import ExtractionResult, Observation, ParseResult

UPSTREAM = "rocket-embedded-chat"
EXTRACTOR = "embedded-chat-api-react-v2"

_CLASS_METHOD = re.compile(
    r"^ {2}(?:(?:public|protected)\s+)?(?P<async>async\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\(",
    re.MULTILINE,
)


def _api_methods(path: str, text: str) -> ParseResult:
    class_match = re.search(r"export\s+default\s+class\s+EmbeddedChatApi\b", text)
    if not class_match:
        raise ExtractionError(f"EmbeddedChat public API class not found in {path}")
    opening = text.find("{", class_match.end())
    if opening == -1:
        raise ExtractionError(f"EmbeddedChat public API class has no body in {path}")
    closing = find_balanced(text, opening)
    items: list[Observation] = []
    for match in _CLASS_METHOD.finditer(text, opening + 1, closing):
        name = match.group("name")
        signature_end = text.find("{", match.end(), min(closing, match.end() + 1000))
        if signature_end == -1:
            signature_end = text.find("\n", match.end(), closing)
        if signature_end == -1:
            raise ExtractionError(f"Cannot bound EmbeddedChat method signature {name!r} in {path}")
        signature = " ".join(text[match.start() : signature_end].split())
        is_constructor = name == "constructor"
        items.append(
            observation(
                upstream=UPSTREAM,
                category="config" if is_constructor else "api",
                boundary_type="package_api",
                family="embedded-chat-api-method",
                transport="package",
                operation="construct" if is_constructor else "call",
                route=f"EmbeddedChatApi.{name}",
                handler=name,
                name=name,
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="typescript_public_method",
                details={
                    "framework": "embedded-chat-typescript-api",
                    "signature": signature,
                    "async": bool(match.group("async")),
                },
            )
        )
    if not items:
        raise ExtractionError(f"EmbeddedChat public API class has no static methods in {path}")
    return parse_result(items)


_PUBLIC_EXPORT = re.compile(
    r"^\s*export\s+\{\s*(?:default\s+as\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*\}"
    r"\s*from\s*['\"](?P<source>[^'\"]+)['\"]",
    re.MULTILINE,
)


def _react_exports(path: str, text: str) -> ParseResult:
    items: list[Observation] = []
    for match in _PUBLIC_EXPORT.finditer(text):
        name = match.group("name")
        module = match.group("source")
        items.append(
            observation(
                upstream=UPSTREAM,
                category="ui",
                boundary_type="package_export",
                family="react-package-export",
                transport="module",
                operation="export",
                route=module,
                handler=name,
                name=name,
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="react_public_export",
                details={"framework": "react-package-export", "module": module},
            )
        )
    if not items and text.strip():
        raise ExtractionError(f"No static React package exports found in {path}")
    return parse_result(items)


def _react_props(path: str, text: str) -> ParseResult:
    assignment = re.search(r"\bEmbeddedChat\.propTypes\s*=\s*\{", text)
    if not assignment:
        raise ExtractionError(f"EmbeddedChat propTypes declaration not found in {path}")
    opening = text.find("{", assignment.start(), assignment.end())
    closing = find_balanced(text, opening)
    body = text[opening + 1 : closing]
    body_offset = opening + 1
    items: list[Observation] = []
    for match in re.finditer(
        r"^\s*(?P<name>[A-Za-z_$][\w$]*)\s*:\s*PropTypes\.(?P<type>[A-Za-z_$][\w$]*)",
        body,
        re.MULTILINE,
    ):
        prop_name = match.group("name")
        items.append(
            observation(
                upstream=UPSTREAM,
                category="config",
                boundary_type="configuration_item",
                family="react-public-prop",
                transport="component_prop",
                operation="configure",
                route="EmbeddedChat",
                handler=prop_name,
                name=f"EmbeddedChat.{prop_name}",
                path=path,
                text=text,
                offset=body_offset + match.start(),
                extractor=EXTRACTOR,
                candidate_kind="react_public_prop",
                details={"framework": "react-proptypes", "propType": match.group("type")},
            )
        )
    if not items:
        raise ExtractionError(f"EmbeddedChat propTypes declaration is empty in {path}")
    return parse_result(items)


SOURCE_PATTERNS = (
    "packages/api/src/EmbeddedChatApi.ts",
    "packages/react/src/index.js",
    "packages/react/src/views/EmbeddedChat.js",
    "packages/*/package.json",
    "packages/api/**/*.test.ts",
    "packages/react/**/*.test.js",
)


def extract(root: Path) -> ExtractionResult:
    scopes = (
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="typescript-public-api",
            extractor=EXTRACTOR,
            includes=("packages/api/src/EmbeddedChatApi.ts",),
            parser=_api_methods,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="react-package-exports",
            extractor=EXTRACTOR,
            includes=("packages/react/src/index.js",),
            parser=_react_exports,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="react-public-props",
            extractor=EXTRACTOR,
            includes=("packages/react/src/views/EmbeddedChat.js",),
            parser=_react_props,
        ),
        inventory_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="package-license-metadata",
            includes=("packages/*/package.json",),
            classification="license_metadata",
        ),
        inventory_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="upstream-tests",
            includes=("packages/api/**/*.test.ts", "packages/react/**/*.test.js"),
            classification="supporting_test",
        ),
    )
    return ExtractionResult(upstream=UPSTREAM, root=root, scopes=scopes)
