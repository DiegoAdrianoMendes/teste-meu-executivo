"""Gateway-routed transcription, OCR, media download, and image-to-PDF helpers."""
from __future__ import annotations

import hashlib
import io
from uuid import UUID

import httpx

from app.ai.base import AIMessage
from app.ai.errors import AIRuntimeError, ProviderCallError
from app.ai.factory import get_model_gateway
from app.ai.gateway import ModelGateway
from app.ai.models import (
    MediaKind,
    ModelAttribution,
    ModelMediaPart,
    ModelRequest,
    TaskClass,
)
from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.services.whatsapp.media_security import trusted_meta_media_url, trusted_meta_object_id


class TranscriptionService:
    def __init__(self, gateway: ModelGateway | None = None) -> None:
        self._gateway = gateway

    @property
    def gateway(self) -> ModelGateway:
        return self._gateway or get_model_gateway()

    async def download_whatsapp_media(self, media_id: str, access_token: str) -> tuple[bytes, str]:
        """
        Baixa mídia do WhatsApp usando a Graph API.
        Retorna (bytes, mime_type).

        Passo 1: GET /{META_GRAPH_API_VERSION}/{media_id} → {url, mime_type}
        Passo 2: GET {url} com Authorization header → bytes
        """
        if not external_delivery_allowed():
            raise RuntimeError("Download de mídia externa bloqueado pelo gate KLM.")
        media_id = trusted_meta_object_id(media_id)
        graph_url = f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}/{media_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Obter URL da mídia
            resp = await client.get(
                graph_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            media_info = resp.json()
            download_url = trusted_meta_media_url(media_info.get("url"))
            mime_type = media_info.get("mime_type", "audio/ogg")

            # Baixar bytes da mídia
            dl_resp = await client.get(
                download_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            dl_resp.raise_for_status()
            return dl_resp.content, mime_type

    async def transcribe_audio(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        *,
        workspace_id: UUID | str,
        source_ref: str | None = None,
        allow_fallback: bool = True,
    ) -> str:
        """
        Transcreve áudio usando Gemini multimodal via Vertex AI SDK.
        Suporta: audio/ogg, audio/mpeg, audio/mp4, audio/wav
        """
        return await self._extract_media_text(
            data=audio_bytes,
            mime_type=mime_type,
            kind=MediaKind.AUDIO,
            workspace_id=workspace_id,
            source_ref=source_ref,
            agent_name="media_transcriber",
            system_prompt=(
                "Você é um transcritor de áudio. Transcreva fielmente em português "
                "brasileiro, sem inferir conteúdo inaudível."
            ),
            user_prompt=(
                "Transcreva o áudio anexado. Retorne apenas a transcrição; marque trechos "
                "inaudíveis de forma explícita."
            ),
            fallback="[Áudio recebido — transcrição não disponível]",
            allow_fallback=allow_fallback,
        )

    async def transcribe_image(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        *,
        workspace_id: UUID | str,
        source_ref: str | None = None,
        allow_fallback: bool = True,
    ) -> str:
        """
        Extrai texto de uma imagem via Gemini Vision (Vertex AI).
        Útil para: documentos, RGs, CNHs, holerites, contratos fotografados.
        """
        return await self._extract_media_text(
            data=image_bytes,
            mime_type=mime_type,
            kind=MediaKind.IMAGE,
            workspace_id=workspace_id,
            source_ref=source_ref,
            agent_name="media_ocr",
            system_prompt=(
                "Você é um OCR especializado. Extraia somente o texto visível e preserve "
                "a ordem de leitura; não complete campos ausentes."
            ),
            user_prompt=(
                "Extraia todo o texto visível da imagem anexada. Retorne apenas o texto "
                "extraído, preservando a formatação relevante."
            ),
            fallback="[Imagem recebida — texto não extraído]",
            allow_fallback=allow_fallback,
        )

    async def _extract_media_text(
        self,
        *,
        data: bytes,
        mime_type: str,
        kind: MediaKind,
        workspace_id: UUID | str,
        source_ref: str | None,
        agent_name: str,
        system_prompt: str,
        user_prompt: str,
        fallback: str,
        allow_fallback: bool,
    ) -> str:
        try:
            media = ModelMediaPart(kind=kind, mime_type=mime_type, data=data)
            workspace = str(workspace_id).strip()
            if not workspace:
                raise ValueError("workspace_id is required for media processing.")
            idempotency_material = ":".join(
                (workspace, kind.value, source_ref or media.content_sha256)
            )
            idempotency_digest = hashlib.sha256(
                idempotency_material.encode("utf-8")
            ).hexdigest()
            result = await self.gateway.generate(
                ModelRequest(
                    messages=[
                        AIMessage("system", system_prompt),
                        AIMessage("user", user_prompt),
                    ],
                    workspace_id=workspace,
                    agent_name=agent_name,
                    task_class=TaskClass.MEDIUM,
                    max_output_tokens=4_096,
                    idempotent=True,
                    idempotency_key=f"media:{kind.value}:{idempotency_digest}",
                    contains_sensitive_data=True,
                    allow_response_cache=False,
                    media_parts=(media,),
                    attribution=ModelAttribution(
                        operation=f"media.{kind.value}.extract",
                        source_refs=(source_ref or media.content_sha256,),
                    ),
                )
            )
            extracted = result.text.strip()
            if not extracted:
                raise ProviderCallError(
                    "O provider concluiu a chamada sem texto de mídia.",
                    request_started=True,
                    billing_unknown=False,
                    provider_request_id=result.provider_request_id,
                )
            logger.info(
                "transcription.media_done",
                kind=kind.value,
                chars=len(extracted),
                request_id=result.request_id,
                provider=result.provider,
            )
            return extracted
        except ProviderCallError as exc:
            logger.warning(
                "transcription.media_provider_failed",
                kind=kind.value,
                request_started=exc.request_started,
                billing_unknown=exc.billing_unknown,
            )
            if exc.request_started or exc.billing_unknown or not allow_fallback:
                raise
            if allow_fallback:
                return fallback
            raise
        except AIRuntimeError as exc:
            logger.warning(
                "transcription.media_preflight_blocked",
                kind=kind.value,
                error_type=safe_error_type(exc),
            )
            if allow_fallback:
                return fallback
            raise
        except Exception as exc:
            logger.warning(
                "transcription.media_failed",
                kind=kind.value,
                error_type=safe_error_type(exc),
            )
            if allow_fallback:
                return fallback
            raise

    async def images_to_pdf(self, images_bytes: list[bytes]) -> bytes:
        """
        Converte uma lista de imagens para um único PDF.
        Usa Pillow. Retorna bytes do PDF.
        """
        try:
            from PIL import Image  # type: ignore

            pil_images = []
            for img_bytes in images_bytes:
                img = Image.open(io.BytesIO(img_bytes))
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")
                pil_images.append(img)

            if not pil_images:
                raise ValueError("Nenhuma imagem fornecida")

            pdf_buffer = io.BytesIO()
            first_img = pil_images[0]
            rest = pil_images[1:] if len(pil_images) > 1 else []
            first_img.save(
                pdf_buffer,
                format="PDF",
                save_all=True,
                append_images=rest,
            )
            pdf_buffer.seek(0)
            logger.info("transcription.pdf_created", pages=len(pil_images))
            return pdf_buffer.read()
        except ImportError:
            logger.error("transcription.pillow_not_installed")
            raise RuntimeError("Pillow não instalado. Execute: pip install Pillow>=10.4.0")
        except Exception as e:
            logger.error("transcription.pdf_failed", error_type=safe_error_type(e))
            raise


# Singleton
_transcription_service: TranscriptionService | None = None


def get_transcription_service() -> TranscriptionService:
    global _transcription_service
    if _transcription_service is None:
        _transcription_service = TranscriptionService()
    return _transcription_service
