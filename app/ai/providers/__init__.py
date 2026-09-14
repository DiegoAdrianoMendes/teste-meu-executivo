"""Official API adapters used by the KLM model gateway."""

from app.ai.providers.anthropic_messages import AnthropicMessagesAdapter
from app.ai.providers.base import ModelProviderAdapter
from app.ai.providers.fake import FakeModelConfig, FakeModelProviderAdapter
from app.ai.providers.gemini_interactions import GeminiInteractionsAdapter
from app.ai.providers.maritaca_chat import MaritacaChatAdapter
from app.ai.providers.openai_responses import OpenAIResponsesAdapter
from app.ai.providers.vertex_gemini import VertexGeminiAdapter

__all__ = [
    "AnthropicMessagesAdapter",
    "FakeModelConfig",
    "FakeModelProviderAdapter",
    "GeminiInteractionsAdapter",
    "MaritacaChatAdapter",
    "ModelProviderAdapter",
    "OpenAIResponsesAdapter",
    "VertexGeminiAdapter",
]
