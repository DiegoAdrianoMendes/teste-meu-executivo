"""Invariante do gate aberto sem chave: adapters constroem com chave vazia.

Produção abre KLM_MODEL_APIS_ENABLED antes de existir chave em qualquer camada
(env ou Blackbox). O boot não pode cair: a ausência de credencial só aparece em
``require_configured``, que o gateway chama por rota, depois de consultar o
resolver de credenciais (workspace → plataforma → env).
"""
from __future__ import annotations

import pytest

from app.ai.errors import ProviderNotConfiguredError
from app.ai.providers.anthropic_messages import AnthropicMessagesAdapter
from app.ai.providers.gemini_interactions import GeminiInteractionsAdapter
from app.ai.providers.maritaca_chat import MaritacaChatAdapter
from app.ai.providers.openai_responses import OpenAIResponsesAdapter
from app.ai.providers.vertex_gemini import VertexGeminiAdapter


@pytest.mark.parametrize(
    ("adapter_class", "base_url"),
    [
        (OpenAIResponsesAdapter, "https://api.openai.com/v1"),
        (AnthropicMessagesAdapter, "https://api.anthropic.com"),
        (GeminiInteractionsAdapter, "https://generativelanguage.googleapis.com"),
        (MaritacaChatAdapter, "https://chat.maritaca.ai/api"),
    ],
)
def test_key_based_adapters_construct_with_empty_key(adapter_class, base_url):
    adapter = adapter_class(api_key="", base_url=base_url)
    with pytest.raises(ProviderNotConfiguredError):
        adapter.require_configured()

    configured = adapter_class(api_key="chave-sintetica-0123456789", base_url=base_url)
    configured.require_configured()


def test_vertex_adapter_constructs_without_project():
    adapter = VertexGeminiAdapter(project_id="", location="")
    with pytest.raises(ProviderNotConfiguredError):
        adapter.require_configured()
