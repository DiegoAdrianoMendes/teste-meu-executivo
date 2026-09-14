"""
ZapSign schemas — Assinatura eletrônica
API: https://api.zapsign.com.br/api/v1
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ZapSignSigner(BaseModel):
    name: str
    email: Optional[str] = None
    phone_country: str = "55"
    phone_number: Optional[str] = None
    auth_mode: str = "assinaturaTela-tokenEmail"
    send_automatic_email: bool = True
    lock_name: bool = True
    redirect_link: Optional[str] = None


class ZapSignCreateRequest(BaseModel):
    name: str
    pdf_url: Optional[str] = None  # URL pública do PDF
    base64_pdf: Optional[str] = None  # Base64 do PDF
    signers: List[ZapSignSigner]
    external_id: Optional[str] = None  # matter_id
    date_limit_to_sign: Optional[str] = None  # YYYY-MM-DD
    lang: str = "pt-br"
    disable_signer_emails: bool = False
    brand_name: Optional[str] = None
    brand_logo: Optional[str] = None
    brand_primary_color: Optional[str] = None


class ZapSignSignerRead(BaseModel):
    token: str
    sign_url: str
    status: str  # new | link-opened | signed
    name: str
    email: Optional[str] = None
    signed_at: Optional[datetime] = None


class ZapSignDocumentRead(BaseModel):
    token: str
    open_id: Optional[int] = None
    status: str  # pending | signed
    name: str
    original_file: Optional[str] = None
    signed_file: Optional[str] = None
    signers: List[ZapSignSignerRead] = []
    created_at: Optional[datetime] = None

    @property
    def sign_url(self) -> Optional[str]:
        """Retorna URL de assinatura do primeiro signatário"""
        if self.signers:
            return self.signers[0].sign_url
        return None


class ZapSignWebhookPayload(BaseModel):
    """Payload recebido do webhook ZapSign"""
    event_type: str  # doc_signed | doc_refused | doc_expired | doc_created
    token: str  # doc token
    status: str  # pending | signed
    external_id: Optional[str] = None  # matter_id que enviamos
    open_id: Optional[int] = None
    name: Optional[str] = None
    original_file: Optional[str] = None
    signed_file: Optional[str] = None
    sandbox: bool = False
    signers: List[Dict[str, Any]] = []
    signer_who_signed: Optional[Dict[str, Any]] = None


class ZapSignWebhookRegisterRequest(BaseModel):
    url: str
    type: str = "doc_signed"


class ZapSignStatusResponse(BaseModel):
    connected: bool
    webhook_registered: bool
    api_token_configured: bool
