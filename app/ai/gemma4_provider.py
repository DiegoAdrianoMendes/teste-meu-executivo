"""
Compat wrapper legado.

Mantém imports antigos funcionando enquanto o sistema usa o provider novo
baseado em endpoint oficial do Vertex AI Model Garden.
"""

from app.ai.gemma_vertex_provider import VertexGemmaProvider

Gemma4Provider = VertexGemmaProvider

__all__ = ["Gemma4Provider"]
