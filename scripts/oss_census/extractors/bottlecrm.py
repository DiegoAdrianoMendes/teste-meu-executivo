"""Fail-closed extractor for the pinned BottleCRM Django/Svelte/Celery/MCP tree."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scripts.oss_census.common import (
    DEFAULT_EXCLUDES,
    ExtractionError,
    excluded_candidate,
    http_capability,
    inventory_scope,
    javascript_object_members,
    manual_candidate,
    matching_files,
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
    string_list,
)

UPSTREAM = "bottlecrm"
EXTRACTOR = "bottlecrm-django-svelte-celery-mcp-v3"
_DJANGO_HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_DJANGO_HTTP_METHOD_SET = frozenset(_DJANGO_HTTP_METHODS)


_DjangoDefinition = ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class _DjangoSymbol:
    module: str
    path: str
    node: _DjangoDefinition

    @property
    def qualified_name(self) -> str:
        return f"{self.module}.{self.node.name}"


@dataclass(frozen=True)
class _DjangoModule:
    name: str
    package: str
    path: str
    symbols: dict[str, _DjangoSymbol]
    imports: dict[str, str]
    string_sequences: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class _DjangoClassMethods:
    methods: tuple[str, ...]
    method_sources: dict[str, str]
    unresolved_bases: tuple[str, ...]
    inheritance_cycle: bool
    dynamic_method_restriction: bool
    method_restriction: tuple[str, ...] | None


@dataclass(frozen=True)
class _DjangoTargetResolution:
    methods: tuple[str, ...]
    target_kind: str | None
    resolved_target: str | None
    method_sources: dict[str, str]
    reason: str | None
    details: dict[str, object]


def _python_module_identity(path: str) -> tuple[str, str]:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "backend":
        parts = parts[1:]
    is_package = bool(parts and parts[-1] == "__init__")
    if is_package:
        parts.pop()
    module = ".".join(parts)
    package = module if is_package else module.rpartition(".")[0]
    return module, package


def _relative_import(package: str, module: str | None, level: int) -> str:
    if not level:
        return module or ""
    package_parts = package.split(".") if package else []
    ascend = level - 1
    if ascend > len(package_parts):
        return ""
    base_parts = package_parts[: len(package_parts) - ascend]
    if module:
        base_parts.extend(module.split("."))
    return ".".join(part for part in base_parts if part)


def _module_imports(tree: ast.Module, package: str) -> dict[str, str]:
    imports: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                imports[bound_name] = alias.name if alias.asname else bound_name
        elif isinstance(node, ast.ImportFrom):
            module = _relative_import(package, node.module, node.level)
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound_name = alias.asname or alias.name
                imports[bound_name] = ".".join(part for part in (module, alias.name) if part)
    return imports


def _module_string_sequences(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    sequences: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        target: ast.AST | None = None
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        values = string_list(value)
        if isinstance(target, ast.Name) and values is not None:
            sequences[target.id] = tuple(values)
    return sequences


def _django_module(path: str, tree: ast.Module) -> _DjangoModule:
    module, package = _python_module_identity(path)
    if not module:
        raise ExtractionError(f"Cannot derive Django module name from {path}")
    symbols: dict[str, _DjangoSymbol] = {}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols[node.name] = _DjangoSymbol(module=module, path=path, node=node)
    return _DjangoModule(
        name=module,
        package=package,
        path=path,
        symbols=symbols,
        imports=_module_imports(tree, package),
        string_sequences=_module_string_sequences(tree),
    )


class _DjangoSymbolIndex:
    def __init__(self, modules: dict[str, _DjangoModule] | None = None) -> None:
        self._modules = dict(modules or {})

    @classmethod
    def from_sources(cls, sources: Iterable[tuple[str, str]]) -> _DjangoSymbolIndex:
        modules: dict[str, _DjangoModule] = {}
        for path, text in sources:
            module = _django_module(path, parse_python(text, path))
            if module.name in modules:
                raise ExtractionError(f"Duplicate Django module {module.name!r}")
            modules[module.name] = module
        return cls(modules)

    @classmethod
    def from_root(
        cls,
        root: Path,
        *,
        excludes: tuple[str, ...],
    ) -> _DjangoSymbolIndex:
        sources: list[tuple[str, str]] = []
        for source_path in matching_files(root, ("backend/**/*.py",), excludes):
            raw = source_path.read_bytes()
            relative_path = source_path.relative_to(root).as_posix()
            try:
                text = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ExtractionError(
                    f"Non-UTF-8 Django support source {relative_path}: {exc}"
                ) from exc
            sources.append((relative_path, text))
        return cls.from_sources(sources)

    def with_module(self, path: str, tree: ast.Module) -> _DjangoSymbolIndex:
        module = _django_module(path, tree)
        modules = dict(self._modules)
        modules[module.name] = module
        return _DjangoSymbolIndex(modules)

    def resolve(self, module: str, reference: str) -> _DjangoSymbol | None:
        return self._resolve_in_module(module, reference, set())

    def _resolve_in_module(
        self,
        module_name: str,
        reference: str,
        seen: set[tuple[str, str]],
    ) -> _DjangoSymbol | None:
        key = (module_name, reference)
        if key in seen:
            return None
        seen.add(key)
        module = self._modules.get(module_name)
        if module is None:
            return None
        if "." not in reference and reference in module.symbols:
            return module.symbols[reference]
        first, separator, remainder = reference.partition(".")
        imported = module.imports.get(first)
        if imported:
            qualified = imported + (f".{remainder}" if separator else "")
            return self._resolve_absolute(qualified, seen)
        if separator:
            return self._resolve_absolute(reference, seen)
        return None

    def _resolve_absolute(
        self,
        qualified: str,
        seen: set[tuple[str, str]],
    ) -> _DjangoSymbol | None:
        parts = qualified.split(".")
        for index in range(len(parts) - 1, 0, -1):
            module_name = ".".join(parts[:index])
            if module_name not in self._modules:
                continue
            reference = ".".join(parts[index:])
            return self._resolve_in_module(module_name, reference, seen)
        return None

    def function_http_methods(self, symbol: _DjangoSymbol) -> tuple[str, ...]:
        if not isinstance(symbol.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return ()
        restrictions: list[set[str]] = []
        for decorator in symbol.node.decorator_list:
            decorator_name = call_name(
                decorator.func if isinstance(decorator, ast.Call) else decorator
            ).split(".")[-1]
            methods: set[str] | None = None
            if decorator_name == "require_GET":
                methods = {"get"}
            elif decorator_name == "require_POST":
                methods = {"post"}
            elif decorator_name == "require_safe":
                methods = {"get"}
            elif decorator_name in {"api_view", "require_http_methods"} and isinstance(
                decorator, ast.Call
            ):
                if decorator.args:
                    values = self._string_sequence(symbol.module, decorator.args[0])
                    if values is not None:
                        methods = {value.lower() for value in values}
                elif decorator_name == "api_view":
                    methods = {"get"}
            if methods is not None:
                restrictions.append(methods & _DJANGO_HTTP_METHOD_SET)
        if not restrictions:
            return ()
        allowed = set.intersection(*restrictions)
        return tuple(method for method in _DJANGO_HTTP_METHODS if method in allowed)

    def class_http_methods(self, symbol: _DjangoSymbol) -> _DjangoClassMethods:
        return self._class_http_methods(symbol, ())

    def _class_http_methods(
        self,
        symbol: _DjangoSymbol,
        stack: tuple[str, ...],
    ) -> _DjangoClassMethods:
        if not isinstance(symbol.node, ast.ClassDef):
            return _DjangoClassMethods((), {}, (), False, False, None)
        if symbol.qualified_name in stack:
            return _DjangoClassMethods((), {}, (), True, False, None)

        methods: set[str] = set()
        method_sources: dict[str, str] = {}
        unresolved_bases: set[str] = set()
        inheritance_cycle = False
        dynamic_method_restriction = False
        inherited_restriction: tuple[str, ...] | None = None
        next_stack = (*stack, symbol.qualified_name)

        for base in reversed(symbol.node.bases):
            base_name = call_name(base)
            base_symbol = self.resolve(symbol.module, base_name) if base_name else None
            if base_symbol is None or not isinstance(base_symbol.node, ast.ClassDef):
                if base_name:
                    unresolved_bases.add(base_name)
                continue
            inherited = self._class_http_methods(base_symbol, next_stack)
            methods.update(inherited.methods)
            method_sources.update(inherited.method_sources)
            unresolved_bases.update(inherited.unresolved_bases)
            inheritance_cycle = inheritance_cycle or inherited.inheritance_cycle
            dynamic_method_restriction = (
                dynamic_method_restriction or inherited.dynamic_method_restriction
            )
            if inherited_restriction is None:
                inherited_restriction = inherited.method_restriction

        for child in symbol.node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method = child.name.lower()
                if method in _DJANGO_HTTP_METHOD_SET:
                    methods.add(method)
                    method_sources[method] = symbol.qualified_name

        restriction_present, restriction = self._class_method_restriction(symbol)
        if restriction_present and restriction is None:
            dynamic_method_restriction = True
            effective_restriction = None
        elif restriction is not None:
            effective_restriction = restriction
        else:
            effective_restriction = inherited_restriction
        if effective_restriction is not None:
            allowed = set(effective_restriction)
            methods.intersection_update(allowed)
            method_sources = {
                method: source for method, source in method_sources.items() if method in allowed
            }

        ordered_methods = tuple(method for method in _DJANGO_HTTP_METHODS if method in methods)
        return _DjangoClassMethods(
            methods=ordered_methods,
            method_sources=method_sources,
            unresolved_bases=tuple(sorted(unresolved_bases)),
            inheritance_cycle=inheritance_cycle,
            dynamic_method_restriction=dynamic_method_restriction,
            method_restriction=effective_restriction,
        )

    def _class_method_restriction(
        self, symbol: _DjangoSymbol
    ) -> tuple[bool, tuple[str, ...] | None]:
        if not isinstance(symbol.node, ast.ClassDef):
            return False, None
        for child in symbol.node.body:
            target: ast.AST | None = None
            value: ast.AST | None = None
            if isinstance(child, ast.Assign) and len(child.targets) == 1:
                target = child.targets[0]
                value = child.value
            elif isinstance(child, ast.AnnAssign):
                target = child.target
                value = child.value
            if not isinstance(target, ast.Name) or target.id != "http_method_names":
                continue
            values = self._string_sequence(symbol.module, value)
            if values is None:
                return True, None
            allowed = {value.lower() for value in values} & _DJANGO_HTTP_METHOD_SET
            return True, tuple(method for method in _DJANGO_HTTP_METHODS if method in allowed)
        return False, None

    def _string_sequence(self, module_name: str, node: ast.AST | None) -> tuple[str, ...] | None:
        values = string_list(node)
        if values is not None:
            return tuple(values)
        if isinstance(node, ast.Name):
            module = self._modules.get(module_name)
            if module is not None:
                return module.string_sequences.get(node.id)
        return None


def _target_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Call):
        return call_name(node.func)
    if node is None:
        return ""
    return call_name(node)


def _framework_route_exclusion(target_name: str, route_name: str | None) -> str | None:
    if target_name == "admin.site.urls":
        return "django_admin_mount_not_product_capability"
    if target_name == "views.LogoutView.as_view" and route_name == "logout":
        return "django_auth_framework_route_not_product_capability"
    if target_name in {
        "SpectacularAPIView.as_view",
        "SpectacularRedocView.as_view",
        "SpectacularSwaggerView.as_view",
    }:
        return "developer_schema_ui_not_product_capability"
    return None


def _resolve_django_target(
    *,
    index: _DjangoSymbolIndex,
    module: str,
    target: ast.AST | None,
) -> _DjangoTargetResolution:
    target_name = _target_name(target)
    if isinstance(target, ast.Call) and target_name.endswith(".as_view"):
        reference = target_name.removesuffix(".as_view")
        symbol = index.resolve(module, reference)
        if symbol is None:
            return _DjangoTargetResolution(
                methods=(),
                target_kind=None,
                resolved_target=None,
                method_sources={},
                reason="local_class_target_not_resolved",
                details={"targetReference": reference},
            )
        if not isinstance(symbol.node, ast.ClassDef):
            return _DjangoTargetResolution(
                methods=(),
                target_kind="function",
                resolved_target=symbol.qualified_name,
                method_sources={},
                reason="as_view_target_is_not_a_class",
                details={"targetReference": reference},
            )
        class_methods = index.class_http_methods(symbol)
        details: dict[str, object] = {
            "targetReference": reference,
            "unresolvedBases": list(class_methods.unresolved_bases),
            "inheritanceCycle": class_methods.inheritance_cycle,
            "httpMethodNames": (
                [method.upper() for method in class_methods.method_restriction]
                if class_methods.method_restriction is not None
                else None
            ),
        }
        if class_methods.dynamic_method_restriction:
            return _DjangoTargetResolution(
                methods=(),
                target_kind="class",
                resolved_target=symbol.qualified_name,
                method_sources={},
                reason="dynamic_http_method_names",
                details=details,
            )
        if class_methods.inheritance_cycle:
            return _DjangoTargetResolution(
                methods=(),
                target_kind="class",
                resolved_target=symbol.qualified_name,
                method_sources={},
                reason="local_inheritance_cycle",
                details=details,
            )
        if not class_methods.methods:
            return _DjangoTargetResolution(
                methods=(),
                target_kind="class",
                resolved_target=symbol.qualified_name,
                method_sources={},
                reason="no_local_http_methods_resolved",
                details=details,
            )
        return _DjangoTargetResolution(
            methods=tuple(method.upper() for method in class_methods.methods),
            target_kind="class",
            resolved_target=symbol.qualified_name,
            method_sources={
                method.upper(): source for method, source in class_methods.method_sources.items()
            },
            reason=None,
            details=details,
        )

    if isinstance(target, ast.Call):
        return _DjangoTargetResolution(
            methods=(),
            target_kind=None,
            resolved_target=None,
            method_sources={},
            reason="wrapped_or_dynamic_callable_target",
            details={"targetReference": target_name or None},
        )
    if not target_name:
        return _DjangoTargetResolution(
            methods=(),
            target_kind=None,
            resolved_target=None,
            method_sources={},
            reason="callable_target_name_not_static",
            details={"targetReference": None},
        )
    symbol = index.resolve(module, target_name)
    if symbol is None:
        return _DjangoTargetResolution(
            methods=(),
            target_kind=None,
            resolved_target=None,
            method_sources={},
            reason="local_function_target_not_resolved",
            details={"targetReference": target_name},
        )
    if isinstance(symbol.node, ast.ClassDef):
        return _DjangoTargetResolution(
            methods=(),
            target_kind="class",
            resolved_target=symbol.qualified_name,
            method_sources={},
            reason="class_target_without_as_view",
            details={"targetReference": target_name},
        )
    methods = index.function_http_methods(symbol)
    if not methods:
        return _DjangoTargetResolution(
            methods=(),
            target_kind="function",
            resolved_target=symbol.qualified_name,
            method_sources={},
            reason="function_http_methods_not_explicit",
            details={"targetReference": target_name},
        )
    return _DjangoTargetResolution(
        methods=tuple(method.upper() for method in methods),
        target_kind="function",
        resolved_target=symbol.qualified_name,
        method_sources={method.upper(): symbol.qualified_name for method in methods},
        reason=None,
        details={"targetReference": target_name},
    )


def _django_routes(
    path: str,
    text: str,
    *,
    symbol_index: _DjangoSymbolIndex | None = None,
) -> ParseResult:
    tree = parse_python(text, path)
    symbol_index = (symbol_index or _DjangoSymbolIndex()).with_module(path, tree)
    module, _package = _python_module_identity(path)
    constants = module_string_constants(tree)
    items: list[Observation] = []
    candidates: list[Candidate] = []
    realtime_source = path.endswith("routing.py")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = call_name(node.func).split(".")[-1]
        if function_name in {"path", "re_path", "url"}:
            offset = line_offset(text, node.lineno)
            route = static_string(node.args[0], constants) if node.args else None
            target = node.args[1] if len(node.args) > 1 else None
            if route is None:
                candidates.append(
                    manual_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="django_route",
                        reason="dynamic_route_requires_manual_review",
                    )
                )
                continue
            if isinstance(target, ast.Call) and call_name(target.func).split(".")[-1] == "include":
                candidates.append(
                    excluded_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="django_route",
                        reason="include_mount_container",
                        details={"routePrefix": route},
                    )
                )
                continue
            target_name = _target_name(target)
            route_name = static_string(call_keyword(node, "name"), constants)
            normalized_route = normalize_route(route)
            framework_exclusion = _framework_route_exclusion(target_name, route_name)
            if framework_exclusion is not None:
                candidates.append(
                    excluded_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="django_route",
                        reason=framework_exclusion,
                        details={
                            "route": normalized_route,
                            "routeName": route_name,
                            "target": target_name,
                        },
                    )
                )
                continue
            if realtime_source:
                category = "realtime"
                boundary_type = "realtime_event"
                transport = "websocket"
                family = "django-channels-route"
                operations = ("connect",)
                resolution = None
            else:
                category = http_capability(normalized_route)
                boundary_type = "http_route"
                transport = "http"
                family = "django-url-route"
                resolution = (
                    _DjangoTargetResolution(
                        methods=("GET",),
                        target_kind="framework_class",
                        resolved_target="django.views.generic.TemplateView",
                        method_sources={"GET": "django.views.generic.TemplateView"},
                        reason=None,
                        details={
                            "targetReference": "TemplateView",
                            "frameworkMethodContract": "get_head_options",
                        },
                    )
                    if target_name == "TemplateView.as_view" and route_name == "healthz"
                    else _resolve_django_target(
                        index=symbol_index,
                        module=module,
                        target=target,
                    )
                )
                if resolution.reason is not None:
                    candidates.append(
                        manual_candidate(
                            path=path,
                            text=text,
                            offset=offset,
                            kind="django_route",
                            reason="ambiguous_django_route_target",
                            details={
                                "resolution": "ambiguous",
                                "ambiguity": resolution.reason,
                                "route": normalized_route,
                                "routeName": route_name,
                                "target": target_name or None,
                                "targetKind": resolution.target_kind,
                                "resolvedTarget": resolution.resolved_target,
                                **resolution.details,
                            },
                        )
                    )
                    continue
                operations = resolution.methods

            for operation in operations:
                details: dict[str, object] = {
                    "framework": "django-channels" if realtime_source else "django",
                    "routeName": route_name,
                }
                if resolution is not None:
                    details.update(
                        {
                            "targetKind": resolution.target_kind,
                            "resolvedTarget": resolution.resolved_target,
                            "methodSource": resolution.method_sources.get(operation),
                            **resolution.details,
                        }
                    )
                items.append(
                    observation(
                        upstream=UPSTREAM,
                        category=category,
                        boundary_type=boundary_type,
                        family=family,
                        transport=transport,
                        operation=operation,
                        route=normalized_route,
                        handler=target_name or route_name or "unresolved-view",
                        name=f"{operation} {normalized_route} -> {target_name}",
                        path=path,
                        text=text,
                        offset=offset,
                        extractor=EXTRACTOR,
                        candidate_kind="django_route",
                        aliases=(route_name,) if route_name else (),
                        details=details,
                    )
                )
        elif function_name == "register" and node.args:
            offset = line_offset(text, node.lineno)
            route = static_string(node.args[0], constants)
            if route is None:
                candidates.append(
                    manual_candidate(
                        path=path,
                        text=text,
                        offset=offset,
                        kind="drf_router_registration",
                        reason="dynamic_router_registration_requires_manual_review",
                    )
                )
                continue
            target_name = _target_name(node.args[1] if len(node.args) > 1 else None)
            normalized_route = normalize_route(route)
            items.append(
                observation(
                    upstream=UPSTREAM,
                    category=http_capability(normalized_route),
                    boundary_type="http_route",
                    family="drf-router-registration",
                    transport="http",
                    operation="REST",
                    route=normalized_route,
                    handler=target_name or "unresolved-viewset",
                    name=target_name or normalized_route,
                    path=path,
                    text=text,
                    offset=offset,
                    extractor=EXTRACTOR,
                    candidate_kind="drf_router_registration",
                    details={"framework": "django-rest-framework"},
                )
            )
    return parse_result(items, candidates)


def _celery_tasks(path: str, text: str) -> ParseResult:
    tree = parse_python(text, path)
    constants = module_string_constants(tree)
    module_parts = list(Path(path).with_suffix("").parts)
    if module_parts and module_parts[0] == "backend":
        module_parts = module_parts[1:]
    if module_parts and module_parts[-1] == "__init__":
        module_parts.pop()
    module_name = ".".join(module_parts)
    if not module_name:
        raise ExtractionError(f"Cannot derive Celery module name from {path}")
    items: list[Observation] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        decorated = [
            (
                call_name(decorator.func)
                if isinstance(decorator, ast.Call)
                else call_name(decorator),
                decorator,
            )
            for decorator in node.decorator_list
        ]
        task_decorator = next(
            (
                decorator
                for decorator_name, decorator in decorated
                if decorator_name.split(".")[-1] in {"shared_task", "task"}
            ),
            None,
        )
        if task_decorator is None:
            continue
        explicit_name = (
            static_string(call_keyword(task_decorator, "name"), constants)
            if isinstance(task_decorator, ast.Call)
            else None
        )
        qualified_name = explicit_name or f"{module_name}.{node.name}"
        decorators = [decorator_name for decorator_name, _decorator in decorated]
        items.append(
            observation(
                upstream=UPSTREAM,
                category="job",
                boundary_type="scheduled_job",
                family="celery-task",
                transport="celery",
                operation="execute",
                route=qualified_name,
                handler=qualified_name,
                name=qualified_name,
                path=path,
                text=text,
                offset=line_offset(text, node.lineno),
                extractor=EXTRACTOR,
                candidate_kind="celery_task",
                details={
                    "framework": "celery",
                    "decorators": decorators,
                    "module": module_name,
                    "function": node.name,
                    "explicitTaskName": explicit_name,
                },
            )
        )
    return parse_result(items)


def _mcp_tools(path: str, text: str) -> ParseResult:
    tree = parse_python(text, path)
    constants = module_string_constants(tree)
    items: list[Observation] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not call_name(decorator.func).endswith(
                ".tool"
            ):
                continue
            declared_name = static_string(call_keyword(decorator, "name"), constants) or node.name
            items.append(
                observation(
                    upstream=UPSTREAM,
                    category="mcp",
                    boundary_type="mcp_tool",
                    family="fastmcp-tool",
                    transport="mcp",
                    operation="invoke",
                    route=declared_name,
                    handler=node.name,
                    name=declared_name,
                    path=path,
                    text=text,
                    offset=line_offset(text, decorator.lineno),
                    extractor=EXTRACTOR,
                    candidate_kind="mcp_tool",
                    details={"framework": "fastmcp"},
                )
            )
    return parse_result(items)


def _permissions(path: str, text: str) -> ParseResult:
    tree = parse_python(text, path)
    items: list[Observation] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [call_name(base) for base in node.bases]
        if not any(base.endswith("BasePermission") for base in bases):
            continue
        methods = sorted(
            child.name
            for child in node.body
            if isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef))
            and child.name.startswith("has_")
        )
        items.append(
            observation(
                upstream=UPSTREAM,
                category="permission",
                boundary_type="permission_rule",
                family="drf-permission-class",
                transport="authorization",
                operation="evaluate",
                route=node.name,
                handler=node.name,
                name=node.name,
                path=path,
                text=text,
                offset=line_offset(text, node.lineno),
                extractor=EXTRACTOR,
                candidate_kind="drf_permission_class",
                details={"framework": "django-rest-framework", "checks": methods},
            )
        )
    return parse_result(items)


def _svelte_route(path: str) -> str:
    parts = list(PurePosixPath(path).parts)
    try:
        route_index = parts.index("routes")
    except ValueError:
        return normalize_route(path)
    route_parts = [
        part
        for part in parts[route_index + 1 : -1]
        if not (part.startswith("(") and part.endswith(")"))
    ]
    return normalize_route(*route_parts)


def _svelte_pages(path: str, text: str) -> ParseResult:
    route = _svelte_route(path)
    return parse_result(
        [
            observation(
                upstream=UPSTREAM,
                category="ui",
                boundary_type="ui_route",
                family="sveltekit-page",
                transport="browser",
                operation="navigate",
                route=route,
                handler=path,
                name=route,
                path=path,
                text=text,
                offset=0,
                extractor=EXTRACTOR,
                candidate_kind="sveltekit_page",
                details={"framework": "sveltekit"},
            )
        ]
    )


_SVELTE_METHOD = re.compile(
    r"\bexport\s+(?:(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\b"
    r"|const\s+(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s*=)"
)


def _svelte_server_routes(path: str, text: str) -> ParseResult:
    route = _svelte_route(path)
    items: list[Observation] = []
    for match in _SVELTE_METHOD.finditer(text):
        method = match.group(1) or match.group(2)
        if method is None:
            raise ValueError("Svelte method regex produced no method")
        items.append(
            observation(
                upstream=UPSTREAM,
                category=http_capability(route),
                boundary_type="http_route",
                family="sveltekit-server-route",
                transport="http",
                operation=method,
                route=route,
                handler=method,
                name=f"{method} {route}",
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="sveltekit_server_handler",
                details={"framework": "sveltekit"},
            )
        )
    candidates: list[Candidate] = []
    if not items and text.strip():
        candidates.append(
            manual_candidate(
                path=path,
                text=text,
                offset=0,
                kind="sveltekit_server_module",
                reason="no_static_http_handler_export",
            )
        )
    return parse_result(items, candidates)


_PAGE_ACTIONS = re.compile(r"\bexport\s+const\s+actions\s*=\s*\{")
_PAGE_LOAD = re.compile(r"\bexport\s+(?:(?:async\s+)?function\s+load\b|const\s+load\s*=)")


def _svelte_page_server(path: str, text: str) -> ParseResult:
    route = _svelte_route(path)
    items: list[Observation] = []
    candidates: list[Candidate] = []
    for match in _PAGE_LOAD.finditer(text):
        items.append(
            observation(
                upstream=UPSTREAM,
                category="ui",
                boundary_type="ui_route",
                family="sveltekit-page-load",
                transport="browser_server",
                operation="load",
                route=route,
                handler="load",
                name=f"load {route}",
                path=path,
                text=text,
                offset=match.start(),
                extractor=EXTRACTOR,
                candidate_kind="sveltekit_page_load",
                details={"framework": "sveltekit"},
            )
        )
    for match in _PAGE_ACTIONS.finditer(text):
        opening = text.find("{", match.start(), match.end())
        if opening == -1:
            raise ValueError(f"Svelte actions declaration lacks object literal in {path}")
        members, _closing = javascript_object_members(text, opening)
        if not members:
            candidates.append(
                excluded_candidate(
                    path=path,
                    text=text,
                    offset=match.start(),
                    kind="sveltekit_action_map",
                    reason="empty_or_dynamic_action_map",
                )
            )
            continue
        for action_name, action_offset in members:
            action_route = route if action_name == "default" else f"{route}?/{action_name}"
            items.append(
                observation(
                    upstream=UPSTREAM,
                    category=http_capability(route),
                    boundary_type="http_route",
                    family="sveltekit-form-action",
                    transport="http",
                    operation="POST",
                    route=action_route,
                    handler=f"actions.{action_name}",
                    name=f"POST {action_route}",
                    path=path,
                    text=text,
                    offset=action_offset,
                    extractor=EXTRACTOR,
                    candidate_kind="sveltekit_form_action",
                    details={"framework": "sveltekit", "action": action_name},
                )
            )
    if not items and text.strip():
        candidates.append(
            manual_candidate(
                path=path,
                text=text,
                offset=0,
                kind="sveltekit_page_server_module",
                reason="no_static_load_or_actions_export",
            )
        )
    return parse_result(items, candidates)


SOURCE_PATTERNS = (
    "backend/**/*.py",
    "backend/**/*urls.py",
    "backend/**/*routing.py",
    "backend/**/tasks.py",
    "backend/**/*permissions.py",
    "mcp_server/src/**/*.py",
    "frontend/src/routes/**/+page.svelte",
    "frontend/src/routes/**/+page.server.js",
    "frontend/src/routes/**/+page.server.ts",
    "frontend/src/routes/**/+server.js",
    "frontend/src/routes/**/+server.ts",
    "backend/**/tests/**/*.py",
    "mcp_server/tests/**/*.py",
)


def extract(root: Path) -> ExtractionResult:
    source_excludes = DEFAULT_EXCLUDES + ("tests", "migrations", "__pycache__")
    symbol_index = _DjangoSymbolIndex.from_root(root, excludes=source_excludes)

    def django_routes(path: str, text: str) -> ParseResult:
        return _django_routes(path, text, symbol_index=symbol_index)

    scopes = (
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="django-drf-routes",
            extractor=EXTRACTOR,
            includes=("backend/**/*urls.py",),
            excludes=source_excludes,
            parser=django_routes,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="django-channels-routes",
            extractor=EXTRACTOR,
            includes=("backend/**/*routing.py",),
            excludes=source_excludes,
            parser=django_routes,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="celery-jobs",
            extractor=EXTRACTOR,
            includes=("backend/**/tasks.py",),
            excludes=source_excludes,
            parser=_celery_tasks,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="mcp-tools",
            extractor=EXTRACTOR,
            includes=("mcp_server/src/**/*.py",),
            parser=_mcp_tools,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="permission-contracts",
            extractor=EXTRACTOR,
            includes=("backend/**/*permissions.py",),
            excludes=source_excludes,
            parser=_permissions,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="svelte-pages",
            extractor=EXTRACTOR,
            includes=("frontend/src/routes/**/+page.svelte",),
            parser=_svelte_pages,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="svelte-page-server",
            extractor=EXTRACTOR,
            includes=(
                "frontend/src/routes/**/+page.server.js",
                "frontend/src/routes/**/+page.server.ts",
            ),
            parser=_svelte_page_server,
        ),
        text_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="svelte-server-routes",
            extractor=EXTRACTOR,
            includes=("frontend/src/routes/**/+server.js", "frontend/src/routes/**/+server.ts"),
            parser=_svelte_server_routes,
        ),
        inventory_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="django-python-support",
            includes=("backend/**/*.py",),
            excludes=source_excludes,
            classification="supporting_source",
        ),
        inventory_scope(
            upstream=UPSTREAM,
            root=root,
            scope_id="upstream-tests",
            includes=("backend/**/tests/**/*.py", "mcp_server/tests/**/*.py"),
            classification="supporting_test",
        ),
    )
    return ExtractionResult(upstream=UPSTREAM, root=root, scopes=scopes)
