from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
RUNTIME_BOUNDARIES = ("services", "agents", "workers", "api")
FORBIDDEN_IMPORT_ROOTS = {"openai", "anthropic", "vertexai"}
FORBIDDEN_PROVIDER_HOSTS = {
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
}
FORBIDDEN_DIRECT_PROVIDER_MODULES = {
    "app.ai.gemma4_provider",
    "app.ai.gemma_vertex_provider",
    "app.ai.ollama_provider",
    "app.ai.openai_provider",
    "app.ai.providers",
    "app.ai.vertex_gemini",
}


def _python_files() -> list[Path]:
    return sorted(
        path
        for boundary in RUNTIME_BOUNDARIES
        for path in (APP_ROOT / boundary).rglob("*.py")
    )


def test_model_sdk_imports_are_confined_to_the_ai_gateway_boundary() -> None:
    violations: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for module in names:
                root = module.split(".", 1)[0]
                if root in FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"{path.relative_to(APP_ROOT)}:{node.lineno}:{module}")
                if module.startswith("google.genai") or module.startswith(
                    "google.cloud.aiplatform"
                ):
                    violations.append(f"{path.relative_to(APP_ROOT)}:{node.lineno}:{module}")

    assert violations == [], "SDK de modelo fora de app/ai: " + ", ".join(violations)


def test_provider_http_hosts_are_confined_to_the_ai_gateway_boundary() -> None:
    violations: list[str] = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        for host in FORBIDDEN_PROVIDER_HOSTS:
            if host in source:
                violations.append(f"{path.relative_to(APP_ROOT)}:{host}")

    assert violations == [], "Endpoint de modelo fora de app/ai: " + ", ".join(violations)


def test_direct_provider_modules_are_confined_to_the_ai_gateway_boundary() -> None:
    violations: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if any(
                    module == forbidden or module.startswith(f"{forbidden}.")
                    for forbidden in FORBIDDEN_DIRECT_PROVIDER_MODULES
                ):
                    violations.append(
                        f"{path.relative_to(APP_ROOT)}:{node.lineno}:{module}"
                    )

    assert violations == [], "Provider direto fora de app/ai: " + ", ".join(violations)
