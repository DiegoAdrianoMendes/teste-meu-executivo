"""
Configurações centrais da aplicação.
Carregadas a partir de variáveis de ambiente via Pydantic Settings.
"""

import re
from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse, urlunparse

from pydantic import ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MIN_PRODUCTION_SECRET_KEY_BYTES = 32
_MIN_INTEGRATION_SECRET_ENCRYPTION_KEY_BYTES = 32
_INSECURE_PRODUCTION_SECRET_KEYS = frozenset(
    {
        "change-me",
        "change-me-in-production",
        "change-me-to-a-very-long-random-string-in-production",
        "local-development-only-jwt-secret",
        "secret",
        "your-secret-key",
        "your-secret-key-here",
    }
)
_INSECURE_INTEGRATION_SECRET_ENCRYPTION_KEYS = _INSECURE_PRODUCTION_SECRET_KEYS | frozenset(
    {
        "integration-secret-encryption-key",
        "local-development-only-integration-secret",
        "your-integration-secret-encryption-key",
    }
)


def is_strong_integration_secret_encryption_key(
    value: str,
    *,
    jwt_secret: str = "",
) -> bool:
    """Return whether integration encryption material is strong and dedicated."""
    candidate = value.strip()
    jwt_candidate = jwt_secret.strip()
    return (
        len(candidate.encode("utf-8")) >= _MIN_INTEGRATION_SECRET_ENCRYPTION_KEY_BYTES
        and candidate.casefold() not in _INSECURE_INTEGRATION_SECRET_ENCRYPTION_KEYS
        and (not jwt_candidate or candidate != jwt_candidate)
    )


_APP_ENV_ALIASES = {
    "development": "development",
    "dev": "development",
    "local": "development",
    "test": "test",
    "testing": "test",
    "staging": "staging",
    "stage": "staging",
    "production": "production",
    "prod": "production",
}


def normalize_app_env(value: object) -> str:
    """S21: APP_ENV era string livre; "Production", "PROD" ou " production " pulavam
    todo o endurecimento de produção. Normaliza e recusa valor desconhecido (falha
    no boot, nunca em silêncio). `local` é sinônimo de `development`."""
    key = str(value or "").strip().lower()
    if key not in _APP_ENV_ALIASES:
        raise ValueError(
            f"APP_ENV inválido: {value!r}. Use development, test, staging ou production."
        )
    return _APP_ENV_ALIASES[key]


def fold_app_env(app_env: str, environment: str) -> str:
    """Rótulo de deploy (ENVIRONMENT) só promove a postura quando é um valor
    conhecido: `ENVIRONMENT=Production` com APP_ENV padrão vira `production`;
    rótulo livre (`blue`, `demo-2`) não altera a postura."""
    if app_env != "development" or not environment:
        return app_env
    key = str(environment).strip().lower()
    return _APP_ENV_ALIASES.get(key, app_env)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Aplicação ──
    APP_ENV: str = "development"

    @field_validator("APP_ENV", mode="before")
    @classmethod
    def _normalize_app_env(cls, value: object) -> str:
        return normalize_app_env(value)
    ENVIRONMENT: str = ""
    APP_NAME: str = "Meu Executivo"
    APP_VERSION: str = "0.1.0"
    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    AUTH_REFRESH_COOKIE_NAME: str = "klm_refresh_token"
    AUTH_PLATFORM_REFRESH_COOKIE_NAME: str = "klm_platform_refresh_token"
    AUTH_REFRESH_COOKIE_PATH: str = "/api/v1/auth"
    AUTH_REFRESH_COOKIE_DOMAIN: str | None = None
    AUTH_REFRESH_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    AUTH_REFRESH_COOKIE_SECURE: bool = False
    WEBSOCKET_TICKET_EXPIRE_SECONDS: int = 60
    WEBSOCKET_IDLE_TIMEOUT_SECONDS: float = 75.0
    OPENAPI_ENABLED_IN_PRODUCTION: bool = False
    SETUP_INVITE_TOKEN: str = ""
    SETUP_RATE_LIMIT_PER_HOUR: int = 5
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:4173",
        "http://localhost:4174",
        "http://localhost:8080",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:4173",
        "http://127.0.0.1:4174",
        "http://127.0.0.1:4273",
        "http://127.0.0.1:4274",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:3000",
    ]

    # ── Gate KLM: nenhum efeito externo antes da aprovação explícita ──
    KLM_RUNTIME_MODE: str = "local_simulation"
    KLM_EXTERNAL_ACTIONS_ENABLED: bool = False
    # Gate dedicado à infraestrutura privada da própria aplicação (S3/KMS).
    # Não autoriza providers, entregas externas nem APIs de modelos.
    KLM_PRIVATE_AWS_STORAGE_ENABLED: bool = False
    KLM_AWS_ENABLED: bool = False
    KLM_ONLINE_APPROVED: bool = False
    KLM_MODEL_APIS_ENABLED: bool = False
    # Habilita sessões administrativas root_direct/support_approved fora do
    # modo local. É independente dos gates de providers/efeitos externos.
    KLM_ADMINISTRATIVE_ACCESS_ENABLED: bool = False
    # S7-a: habilita o round-trip de planejamento/execução de tools READ-ONLY
    # no model mode dos agentes (app/services/agent_tool_executors.py).
    # Fail-closed por padrão: desligado não muda o comportamento/contagem de
    # chamadas ao provider já existente.
    KLM_AGENT_MODEL_MODE_TOOLS_ENABLED: bool = False
    # Módulo contratável à parte de monitoramento processual. DJEN e DataJud
    # são fontes independentes, com credencial e disponibilidade próprias, e
    # por isso têm gates SEPARADOS: abrir uma não abre a outra. Cada gate é
    # ADICIONAL a external_delivery_allowed(); a chamada de rede só acontece
    # com os dois abertos ao mesmo tempo.
    #
    # O modo simulado lê a fixture comitada em tests/fixtures/djen, que NÃO
    # existe na imagem de produção (.dockerignore exclui `tests`). Por isso o
    # worker faz no-op quando o gate está fechado e a fixture não está no
    # disco — nunca modo degradado silencioso. Ver klm_runtime.py.
    KLM_DJEN_LIVE_ENABLED: bool = False
    KLM_DATAJUD_LIVE_ENABLED: bool = False
    # Chave do DataJud: entra DENTRO do gate (datajud_live_delivery_allowed),
    # mesmo padrão de S3_KMS_KEY_ID em aws_private_storage_allowed.
    DATAJUD_API_KEY: str = ""
    # Janela de consulta ao DJEN: [hoje - N, hoje]. O tick roda 03:00 e a edição do
    # dia pode entrar depois; a ingestão é idempotente por external_id, então
    # reconsultar dias já vistos não duplica. O custo cresce por página, não por dia.
    KLM_DJEN_LOOKBACK_DAYS: int = 5
    # Paginação do Comunica (não documentada; verificada contra produção em
    # 03/09/2026): `pagina` 1-based, `itensPorPagina` com mínimo 5, `count` não
    # confiável. Tetos logados quando batem, nunca silenciosos.
    KLM_DJEN_PAGE_SIZE: int = 100
    KLM_DJEN_MAX_PAGES: int = 50
    KLM_DJEN_MAX_SECONDS_PER_OAB: int = 120
    # Limite global por IP de saída: o Comunica responde X-RateLimit-Limit: 20 por
    # janela curta. O do DataJud não foi verificado; conservador de propósito.
    KLM_DJEN_RATE_LIMIT_PER_MINUTE: int = 15
    KLM_DATAJUD_RATE_LIMIT_PER_MINUTE: int = 30
    # Relay em sa-east-1 (S71, 11/09/2026): comunicaapi.pje.jus.br responde 403 do
    # CloudFront a IPs fora do Brasil, e o worker roda em us-east-1. Com o ARN
    # preenchido, DJEN e DataJud são consultados por uma função Lambda em São
    # Paulo (deploy/lambda/cnj_relay); vazio, o cliente fala direto com o CNJ.
    # Não é gate: os dois gates acima continuam decidindo se há chamada.
    KLM_CNJ_RELAY_FUNCTION_ARN: str = ""
    KLM_LOCAL_BOOTSTRAP_ENABLED: bool = True
    KLM_CHATWOOT_URL: str = "http://127.0.0.1:3100"
    KLM_ROCKETCHAT_URL: str = "http://127.0.0.1:3200"
    KLM_CHATTERMATE_URL: str = "http://127.0.0.1:3300"
    KLM_BOTTLECRM_URL: str = "http://127.0.0.1:3400"
    KLM_OSS_HEALTHCHECK_TIMEOUT_SECONDS: float = 0.6
    KLM_OSS_LOCAL_BRIDGES_ENABLED: bool = False
    KLM_OSS_WORKSPACE_ID: str = ""
    KLM_OSS_LAUNCH_SECRET: str = ""
    KLM_CHATWOOT_ACCOUNT_ID: str = ""
    KLM_CHATWOOT_INBOX_ID: str = ""
    KLM_CHATWOOT_API_ACCESS_TOKEN: str = ""
    KLM_CHATWOOT_ADMIN_EMAIL: str = ""
    KLM_CHATWOOT_ADMIN_PASSWORD: str = ""
    KLM_CHATWOOT_WEBHOOK_SECRET: str = ""
    KLM_CHATWOOT_WEBHOOK_MAX_AGE_SECONDS: int = 300
    KLM_ROCKETCHAT_ADMIN_USERNAME: str = ""
    KLM_ROCKETCHAT_ADMIN_EMAIL: str = ""
    KLM_ROCKETCHAT_ADMIN_PASSWORD: str = ""
    KLM_ROCKETCHAT_WEBHOOK_TOKEN: str = ""
    KLM_CHATTERMATE_ADMIN_EMAIL: str = ""
    KLM_CHATTERMATE_ADMIN_PASSWORD: str = ""
    KLM_BOTTLECRM_ADMIN_EMAIL: str = ""
    KLM_BOTTLECRM_ADMIN_PASSWORD: str = ""
    KLM_BOTTLECRM_BRIDGE_SECRET: str = ""

    # ── Banco de Dados ──
    DATABASE_URL: str = "postgresql+asyncpg://klm_user:klm_password@localhost:5432/klm_legal_hub"
    DATABASE_URL_SYNC: str = "postgresql://klm_user:klm_password@localhost:5432/klm_legal_hub"
    DATABASE_SQL_ECHO: bool = True

    # ── Redis ──
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ── AWS ──
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = "klm-hub-documents"
    S3_PUBLIC_ASSET_BUCKET_NAME: str = "klm-hub-public-assets"
    S3_PRESIGNED_URL_EXPIRY: int = 3600
    S3_PRIVATE_BUCKET_REQUIRED: bool = True
    S3_KMS_KEY_ID: str = ""
    LOCAL_STORAGE_MAX_BYTES: int = 25 * 1024 * 1024
    DOCUMENT_AV_MODE: str = "disabled"
    CLAMAV_HOST: str = ""
    CLAMAV_PORT: int = 3310
    CLAMAV_CONNECT_TIMEOUT_SECONDS: float = 5.0
    CLAMAV_SCAN_TIMEOUT_SECONDS: float = 30.0
    DOCUMENT_AV_MAX_BYTES: int = 25 * 1024 * 1024
    DOCUMENT_AV_RETRY_LIMIT: int = 3
    BANK_RECONCILIATION_UPLOAD_MAX_BYTES: int = 10 * 1024 * 1024
    FINANCE_HARMONIZATION_UPLOAD_MAX_BYTES: int = 25 * 1024 * 1024
    MIGRATION_UPLOAD_MAX_BYTES: int = 25 * 1024 * 1024
    LEGAL_DOCX_UPLOAD_MAX_BYTES: int = 25 * 1024 * 1024
    IMPORT_ARCHIVE_MAX_ENTRIES: int = 5_000
    IMPORT_ARCHIVE_MAX_UNCOMPRESSED_BYTES: int = 100 * 1024 * 1024
    IMPORT_ARCHIVE_MAX_COMPRESSION_RATIO: float = 100.0
    PLATFORM_EXPORT_STORAGE_DIR: str = ".local/platform-exports"
    PLATFORM_EXPORT_STORAGE_PREFIX: str = "platform-exports"
    PLATFORM_EXPORT_DOWNLOAD_URL_EXPIRY_SECONDS: int = 300
    PLATFORM_EXPORT_MAX_BYTES: int = 500 * 1024 * 1024

    # ── URL pública do backend ──
    PAPERCLIP_PUBLIC_URL: str = "http://localhost:8000"

    # ── Runtime de modelos: API-first e desarmado por padrão ──
    AI_PROVIDER: str = "api_gateway"
    AI_TEMPERATURE: float = 0.2
    KLM_LOCAL_MODELS_ENABLED: bool = False
    KLM_AI_LEGACY_DIRECT_CALLS_ENABLED: bool = False
    KLM_AI_OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    KLM_AI_ANTHROPIC_BASE_URL: str = "https://api.anthropic.com/v1"
    KLM_AI_GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
    KLM_AI_MARITACA_BASE_URL: str = "https://chat.maritaca.ai/api"
    KLM_AI_ALLOWED_BASE_URLS_JSON: str = (
        '["https://api.openai.com/v1","https://api.anthropic.com/v1",'
        '"https://generativelanguage.googleapis.com/v1beta",'
        '"https://chat.maritaca.ai/api"]'
    )
    # Roteamento: classe SIMPLE pode preferir rotas Maritaca (sabiazinho)
    # quando habilitado. Default false preserva a ordem atual do catálogo.
    KLM_AI_SIMPLE_PREFER_MARITACA: bool = False
    KLM_AI_MODEL_ALLOWLIST_JSON: str = "[]"
    KLM_AI_ROUTE_CATALOG_JSON: str = '{"version":"unconfigured","classes":{},"agents":{}}'
    KLM_AI_PRICE_CATALOG_JSON: str = (
        '{"version":"unconfigured","currency":"USD","effective_at":null,"models":{}}'
    )
    KLM_AI_AGENT_CALL_CAPS_JSON: str = "{}"
    KLM_AI_WORKSPACE_DAILY_BUDGETS_JSON: str = "{}"
    KLM_AI_MAX_INPUT_TOKENS_PER_CALL: int = 50_000
    KLM_AI_MAX_OUTPUT_TOKENS_PER_CALL: int = 4_096
    KLM_AI_DEFAULT_MAX_OUTPUT_TOKENS: int = 1_024
    KLM_AI_MAX_COST_PER_CALL_USD: Decimal = Decimal("0.50")
    KLM_AI_WORKSPACE_DAILY_BUDGET_USD: Decimal = Decimal("10.00")
    KLM_AI_HTTP_TIMEOUT_SECONDS: float = 45.0
    KLM_AI_MAX_RETRIES: int = 0
    KLM_AI_RETRY_BASE_SECONDS: float = 0.5
    KLM_AI_CIRCUIT_FAILURE_THRESHOLD: int = 3
    KLM_AI_CIRCUIT_COOLDOWN_SECONDS: float = 60.0
    KLM_AI_CIRCUIT_BACKEND: str = "memory"
    KLM_AI_BUDGET_BACKEND: str = "memory"
    KLM_AI_TELEMETRY_BACKEND: str = "logging"
    KLM_AI_TELEMETRY_STREAM_MAXLEN: int = 100_000
    # Telemetria de fallback persistível (tabela ai_gateway_events, com RLS):
    # cada tentativa de rota vira evento consultável; retenção simples em dias.
    KLM_AI_GATEWAY_EVENTS_ENABLED: bool = True
    KLM_AI_GATEWAY_EVENTS_RETENTION_DAYS: int = 90
    KLM_AI_USAGE_ACCOUNTING_ENABLED: bool = True
    KLM_AI_RESPONSE_CACHE_ENABLED: bool = False
    KLM_AI_RESPONSE_CACHE_BACKEND: str = "memory"
    KLM_AI_RESPONSE_CACHE_TTL_SECONDS: int = 300
    KLM_AI_RESPONSE_CACHE_MAX_ENTRIES: int = 1_024
    KLM_AI_RESPONSE_CACHE_MAX_VALUE_BYTES: int = 1_048_576
    KLM_AI_FAKE_MODEL: str = "klm-fake-v1"
    KLM_AI_FAKE_SEED: str = "klm-local-fake-v1"
    KLM_AI_FAKE_RESPONSE_TEXT: str = "Local deterministic simulation."
    KLM_AI_FAKE_RESPONSES_JSON: str = "{}"
    KLM_LOCAL_REDIS_ENABLED: bool = False

    # ── Vertex AI (Gemini 2.5 Flash-Lite) ──
    VERTEX_PROJECT_ID: str = "advocacia-hub"
    VERTEX_LOCATION: str = "us-central1"
    VERTEX_MODEL: str = "gemini-2.5-flash-lite"
    VERTEX_MODEL_PRO: str = "gemini-2.5-pro"

    # ── Gemma via Vertex AI Model Garden ──
    GEMMA_ENABLED: bool = True
    GEMMA_MODEL_SIMPLE: str = "google/gemma3@gemma-3-4b-it"
    GEMMA_MODEL_MEDIUM: str = "google/gemma3@gemma-3-27b-it"
    GEMMA_ENDPOINT_SIMPLE: str = ""
    GEMMA_ENDPOINT_MEDIUM: str = ""

    # ── Ollama (modelos locais/remotos como Qwen3 e Gemma 4) ──
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL_DEFAULT: str = "qwen3:14b"
    OLLAMA_MODEL_SIMPLE: str = "qwen3:4b"
    OLLAMA_MODEL_MEDIUM: str = "qwen3:14b"
    OLLAMA_MODEL_COMPLEX: str = "gemma4:12b"
    OLLAMA_MODEL_PREMIUM: str = "gemma4:12b"
    OLLAMA_TIMEOUT_SECONDS: int = 120
    OLLAMA_KEEP_ALIVE: str = "10m"
    OLLAMA_NUM_CTX: int = 8192

    # ── Stripe Billing ──
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""

    # Billing da PLATAFORMA. Estas credenciais pertencem à Meu Executivo e
    # nunca são reutilizadas pelo Asaas operacional configurado por workspace.
    SAAS_ASAAS_ACCESS_TOKEN: str = ""
    SAAS_ASAAS_WEBHOOK_TOKEN: str = ""
    SAAS_ASAAS_SANDBOX: bool = True
    MERCADO_PAGO_ACCESS_TOKEN: str = ""
    MERCADO_PAGO_WEBHOOK_SECRET: str = ""

    # ── Meta Ads API ──
    META_ADS_ACCESS_TOKEN: str = ""
    META_ADS_APP_SECRET: str = ""
    META_ADS_AD_ACCOUNT_ID: str = ""  # format: act_123456789

    # ── Google Ads API ──
    GOOGLE_ADS_DEVELOPER_TOKEN: str = ""
    GOOGLE_ADS_CLIENT_ID: str = ""
    GOOGLE_ADS_CLIENT_SECRET: str = ""
    GOOGLE_ADS_REFRESH_TOKEN: str = ""
    GOOGLE_ADS_CUSTOMER_ID: str = ""  # format: 123-456-7890

    # ── OpenAI / LLM (fallback) ──
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_TEMPERATURE: float = 0.2
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    MARITACA_API_KEY: str = ""

    # ── LangSmith ──
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "klm-hub"

    # ── Integrações ──
    OPENCLAW_WEBHOOK_SECRET: str = ""
    SLACK_BOT_TOKEN: str = ""
    SLACK_CHANNEL_ALERTS: str = "#klm-alertas"

    # ── WhatsApp Business (Meta Cloud API) ──
    # Obtidos no Meta for Developers → App → WhatsApp → API Setup
    WHATSAPP_ACCESS_TOKEN: str = ""  # Token permanente da System User (fallback global)
    WHATSAPP_PHONE_NUMBER_ID: str = ""  # ID do número (fallback global)
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = ""  # WABA ID (fallback global)
    WHATSAPP_VERIFY_TOKEN: str = ""  # Gerado no onboarding aprovado
    # Versão canônica, pinada por release, para todos os consumidores Meta.
    META_GRAPH_API_VERSION: str = "v26.0"
    # Alias temporário de compatibilidade. Consumidores internos devem usar
    # META_GRAPH_API_VERSION; o alias será removido no release seguinte.
    WHATSAPP_API_VERSION: str = ""
    WHATSAPP_APP_SECRET: str = ""  # App Secret para validar assinatura HMAC
    WHATSAPP_QR_GATEWAY_BASE_URL: str = ""
    WHATSAPP_QR_GATEWAY_API_KEY: str = ""
    WHATSAPP_SECRET_ENCRYPTION_KEY: str = ""
    # Lista versionada opcional de chaves, separadas por vírgula. A primeira é a
    # chave ativa (usada para cifrar); as demais seguem aceitas para decifrar
    # segredos antigos até a rotação (scripts/rotate_whatsapp_secrets.py) migrar
    # todas as conexões. Quando vazia, cai no comportamento legado de 1 chave.
    WHATSAPP_SECRET_ENCRYPTION_KEYS: str = ""
    INTEGRATION_SECRET_ENCRYPTION_KEY: str = ""

    # ── Meta Embedded Signup (Tech Provider / SaaS) ──
    # App Dashboard → Facebook Login for Business → Configurations → Configuration ID
    # Guia: developers.facebook.com/docs/development/release/tech-providers/
    META_APP_ID: str = ""  # App ID do app Meta da plataforma
    META_APP_SECRET: str = ""  # App Secret (pode ser igual a WHATSAPP_APP_SECRET)
    META_CONFIG_ID: str = ""  # Configuration ID do Facebook Login for Business
    # Solution ID da Partner Solution (obrigatório para Tech Providers)
    META_SOLUTION_ID: str = ""
    PLATFORM_META_VERIFY_TOKEN: str = ""
    PLATFORM_META_PAGE_ACCESS_TOKEN: str = ""
    PLATFORM_META_PAGE_ID: str = ""
    PLATFORM_INSTAGRAM_ACCESS_TOKEN: str = ""
    PLATFORM_INSTAGRAM_ACCOUNT_ID: str = ""

    # ── ZapSign — Assinatura eletrônica ──
    ZAPSIGN_API_TOKEN: str = ""
    ZAPSIGN_WEBHOOK_SECRET: str = ""  # obrigatório; enviado pela ZapSign no header Authorization
    ZAPSIGN_SANDBOX: bool = False

    # ── Asaas (gateway de pagamentos) ──
    ASAAS_SANDBOX: bool = True
    ASAAS_USER_AGENT: str = "KLM-Hub/1.0 (financeiro@klm)"
    ASAAS_TIMEOUT_SECONDS: float = 15.0
    ASAAS_MAX_RETRIES: int = 2

    # ── Email (Resend) ──
    RESEND_API_KEY: str = ""
    RESEND_WEBHOOK_SECRET: str = ""
    PLATFORM_EMAIL_SALES_ADDRESS: str = ""
    PLATFORM_EMAIL_SUPPORT_ADDRESS: str = ""
    EMAIL_FROM: str = "noreply@localhost"
    EMAIL_FROM_NAME: str = "Meu Executivo"
    EMAIL_REPLY_TO: str = ""
    EMAIL_TRANSPORT: Literal["resend", "ses"] = "resend"
    FRONTEND_URL: str = "http://localhost:4173"
    BLACKBOX_FRONTEND_URL: str = "http://localhost:4174"
    PUBLIC_API_URL: str = ""

    # ── Logs ──
    LOG_LEVEL: str = "INFO"

    @staticmethod
    def _swap_redis_db(url: str, db_index: int) -> str:
        parsed = urlparse(url)
        path = f"/{db_index}"
        return urlunparse(parsed._replace(path=path))

    @model_validator(mode="after")
    def _apply_runtime_defaults(self):
        canonical_meta_version_explicit = "META_GRAPH_API_VERSION" in self.model_fields_set
        legacy_meta_version_explicit = (
            "WHATSAPP_API_VERSION" in self.model_fields_set
            and bool(self.WHATSAPP_API_VERSION.strip())
        )
        canonical_meta_version = self.META_GRAPH_API_VERSION.strip()
        legacy_meta_version = self.WHATSAPP_API_VERSION.strip()
        if legacy_meta_version_explicit and not canonical_meta_version_explicit:
            canonical_meta_version = legacy_meta_version
        elif (
            legacy_meta_version_explicit
            and canonical_meta_version_explicit
            and legacy_meta_version != canonical_meta_version
        ):
            raise ValueError(
                "WHATSAPP_API_VERSION diverge de META_GRAPH_API_VERSION; "
                "use apenas a configuração canônica."
            )
        if not re.fullmatch(r"v[1-9][0-9]*\.0", canonical_meta_version):
            raise ValueError(
                "META_GRAPH_API_VERSION deve seguir o formato v<major>.0."
            )
        self.META_GRAPH_API_VERSION = canonical_meta_version
        self.WHATSAPP_API_VERSION = canonical_meta_version

        self.APP_ENV = fold_app_env(self.APP_ENV, self.ENVIRONMENT)

        if self.REDIS_URL and self.REDIS_URL != "redis://localhost:6379/0":
            if self.CELERY_BROKER_URL == "redis://localhost:6379/1":
                self.CELERY_BROKER_URL = self._swap_redis_db(self.REDIS_URL, 1)
            if self.CELERY_RESULT_BACKEND == "redis://localhost:6379/2":
                self.CELERY_RESULT_BACKEND = self._swap_redis_db(self.REDIS_URL, 2)

        if self.uses_local_infrastructure and (
            self.KLM_EXTERNAL_ACTIONS_ENABLED
            or self.KLM_PRIVATE_AWS_STORAGE_ENABLED
            or self.KLM_AWS_ENABLED
        ):
            raise ValueError("KLM_RUNTIME_MODE=local_simulation não permite ações externas ou AWS.")

        if self.KLM_AWS_ENABLED and not self.KLM_EXTERNAL_ACTIONS_ENABLED:
            raise ValueError("KLM_AWS_ENABLED exige KLM_EXTERNAL_ACTIONS_ENABLED=true.")

        if self.KLM_DATAJUD_LIVE_ENABLED and not self.DATAJUD_API_KEY.strip():
            raise ValueError(
                "KLM_DATAJUD_LIVE_ENABLED=true exige DATAJUD_API_KEY preenchida. "
                "Configuração incompleta é recusada na subida, e não descoberta "
                "num log de 4h da manhã."
            )

        relay = self.KLM_CNJ_RELAY_FUNCTION_ARN.strip()
        if relay and not re.fullmatch(
            r"arn:aws:lambda:[a-z0-9-]+:\d{12}:function:[A-Za-z0-9_-]+(:[A-Za-z0-9_$-]+)?", relay
        ):
            raise ValueError(
                "KLM_CNJ_RELAY_FUNCTION_ARN deve ser o ARN completo de uma função Lambda "
                "(arn:aws:lambda:<região>:<conta>:function:<nome>); um valor torto só "
                "seria descoberto no tick das 03:00."
            )

        if (
            self.KLM_EXTERNAL_ACTIONS_ENABLED or self.KLM_AWS_ENABLED
        ) and not self.KLM_ONLINE_APPROVED:
            raise ValueError(
                "KLM_ONLINE_APPROVED deve ser true antes de habilitar ações externas ou AWS."
            )

        if self.KLM_PRIVATE_AWS_STORAGE_ENABLED and not self.S3_KMS_KEY_ID.strip():
            raise ValueError("Storage privado AWS exige S3_KMS_KEY_ID para objetos privados.")

        if self.KLM_MODEL_APIS_ENABLED and not self.KLM_ONLINE_APPROVED:
            raise ValueError(
                "KLM_ONLINE_APPROVED deve ser true antes de habilitar APIs de modelos."
            )
        if self.KLM_MODEL_APIS_ENABLED and not self.KLM_AI_USAGE_ACCOUNTING_ENABLED:
            raise ValueError("APIs de modelos exigem KLM_AI_USAGE_ACCOUNTING_ENABLED=true.")

        export_prefix = self.PLATFORM_EXPORT_STORAGE_PREFIX.strip().strip("/")
        if (
            not export_prefix
            or export_prefix != self.PLATFORM_EXPORT_STORAGE_PREFIX
            or "\\" in export_prefix
            or any(part in {"", ".", ".."} for part in export_prefix.split("/"))
            or export_prefix.split("/", 1)[0] in {"public-site", "quarantine", "staging"}
        ):
            raise ValueError("PLATFORM_EXPORT_STORAGE_PREFIX deve ser uma chave lógica relativa.")
        if not 30 <= self.PLATFORM_EXPORT_DOWNLOAD_URL_EXPIRY_SECONDS <= 900:
            raise ValueError(
                "PLATFORM_EXPORT_DOWNLOAD_URL_EXPIRY_SECONDS deve ficar entre 30 e 900."
            )

        positive_upload_limits = {
            "BANK_RECONCILIATION_UPLOAD_MAX_BYTES": (
                self.BANK_RECONCILIATION_UPLOAD_MAX_BYTES
            ),
            "FINANCE_HARMONIZATION_UPLOAD_MAX_BYTES": (
                self.FINANCE_HARMONIZATION_UPLOAD_MAX_BYTES
            ),
            "MIGRATION_UPLOAD_MAX_BYTES": self.MIGRATION_UPLOAD_MAX_BYTES,
            "LEGAL_DOCX_UPLOAD_MAX_BYTES": self.LEGAL_DOCX_UPLOAD_MAX_BYTES,
            "IMPORT_ARCHIVE_MAX_ENTRIES": self.IMPORT_ARCHIVE_MAX_ENTRIES,
            "IMPORT_ARCHIVE_MAX_UNCOMPRESSED_BYTES": (
                self.IMPORT_ARCHIVE_MAX_UNCOMPRESSED_BYTES
            ),
            "IMPORT_ARCHIVE_MAX_COMPRESSION_RATIO": (
                self.IMPORT_ARCHIVE_MAX_COMPRESSION_RATIO
            ),
        }
        invalid_upload_limits = [
            name for name, value in positive_upload_limits.items() if value <= 0
        ]
        if invalid_upload_limits:
            raise ValueError(
                "Limites de upload/importação devem ser positivos: "
                + ", ".join(sorted(invalid_upload_limits))
            )

        if self.KLM_AI_BUDGET_BACKEND not in {"memory", "redis"}:
            raise ValueError("KLM_AI_BUDGET_BACKEND deve ser memory ou redis.")
        if self.KLM_AI_RESPONSE_CACHE_BACKEND not in {"memory", "redis"}:
            raise ValueError("KLM_AI_RESPONSE_CACHE_BACKEND deve ser memory ou redis.")
        if self.KLM_AI_TELEMETRY_BACKEND not in {"logging", "redis"}:
            raise ValueError("KLM_AI_TELEMETRY_BACKEND deve ser logging ou redis.")
        if self.KLM_AI_CIRCUIT_BACKEND not in {"memory", "redis"}:
            raise ValueError("KLM_AI_CIRCUIT_BACKEND deve ser memory ou redis.")
        if (
            self.KLM_MODEL_APIS_ENABLED
            and self.KLM_ONLINE_APPROVED
            and self.KLM_AI_BUDGET_BACKEND != "redis"
        ):
            raise ValueError("APIs de modelos exigem budget Redis multiprocesso.")
        if (
            self.KLM_MODEL_APIS_ENABLED
            and self.KLM_ONLINE_APPROVED
            and self.KLM_AI_TELEMETRY_BACKEND != "redis"
        ):
            raise ValueError("APIs de modelos exigem telemetria Redis durável.")
        if (
            self.KLM_MODEL_APIS_ENABLED
            and self.KLM_ONLINE_APPROVED
            and self.KLM_AI_CIRCUIT_BACKEND != "redis"
        ):
            raise ValueError("APIs de modelos exigem circuit breaker Redis.")
        if (
            self.is_production
            and self.KLM_AI_RESPONSE_CACHE_ENABLED
            and self.KLM_AI_RESPONSE_CACHE_BACKEND != "redis"
        ):
            raise ValueError("Cache de respostas em produção exige Redis.")
        if self.AUTH_REFRESH_COOKIE_SAMESITE == "none" and not self.refresh_cookie_secure:
            raise ValueError("AUTH_REFRESH_COOKIE_SAMESITE=none exige cookie Secure em HTTPS.")
        if (
            not self.AUTH_REFRESH_COOKIE_NAME.strip()
            or not self.AUTH_PLATFORM_REFRESH_COOKIE_NAME.strip()
            or self.AUTH_REFRESH_COOKIE_NAME == self.AUTH_PLATFORM_REFRESH_COOKIE_NAME
        ):
            raise ValueError(
                "Os cookies de renovação do aplicativo e da Blackbox devem ter nomes distintos."
            )
        if self.KLM_AI_MAX_RETRIES < 0:
            raise ValueError("KLM_AI_MAX_RETRIES não pode ser negativo.")
        if (
            self.KLM_AI_MAX_INPUT_TOKENS_PER_CALL <= 0
            or self.KLM_AI_MAX_OUTPUT_TOKENS_PER_CALL <= 0
            or self.KLM_AI_DEFAULT_MAX_OUTPUT_TOKENS <= 0
            or self.KLM_AI_DEFAULT_MAX_OUTPUT_TOKENS > self.KLM_AI_MAX_OUTPUT_TOKENS_PER_CALL
            or self.KLM_AI_MAX_COST_PER_CALL_USD <= 0
            or self.KLM_AI_WORKSPACE_DAILY_BUDGET_USD <= 0
            or self.KLM_AI_HTTP_TIMEOUT_SECONDS <= 0
            or self.KLM_AI_CIRCUIT_FAILURE_THRESHOLD <= 0
            or self.KLM_AI_CIRCUIT_COOLDOWN_SECONDS <= 0
            or self.KLM_AI_RESPONSE_CACHE_TTL_SECONDS <= 0
            or self.KLM_AI_RESPONSE_CACHE_MAX_ENTRIES <= 0
            or self.KLM_AI_RESPONSE_CACHE_MAX_VALUE_BYTES <= 0
            or self.KLM_AI_TELEMETRY_STREAM_MAXLEN <= 0
            or self.KLM_AI_GATEWAY_EVENTS_RETENTION_DAYS <= 0
        ):
            raise ValueError("Limites do runtime de IA devem ser positivos e coerentes.")

        if self.is_production and self.KLM_LOCAL_BOOTSTRAP_ENABLED:
            raise ValueError("KLM_LOCAL_BOOTSTRAP_ENABLED deve ser false em produção.")

        if self.is_production:
            insecure_origins = {"*", "http://*", "https://*"}
            production_secret = self.SECRET_KEY.strip()
            if (
                not production_secret
                or production_secret.casefold() in _INSECURE_PRODUCTION_SECRET_KEYS
                or len(production_secret.encode("utf-8")) < _MIN_PRODUCTION_SECRET_KEY_BYTES
            ):
                raise ValidationError.from_exception_data(
                    "Settings",
                    [
                        {
                            "type": "value_error",
                            "loc": ("SECRET_KEY",),
                            "msg": "SECRET_KEY insegura em produção.",
                            "input": "[redacted]",
                            "ctx": {
                                "error": (
                                    "SECRET_KEY deve ter ao menos 32 bytes e não pode usar "
                                    "valores conhecidos de desenvolvimento."
                                )
                            },
                        }
                    ],
                )
            if not is_strong_integration_secret_encryption_key(
                self.INTEGRATION_SECRET_ENCRYPTION_KEY,
                jwt_secret=production_secret,
            ):
                raise ValidationError.from_exception_data(
                    "Settings",
                    [
                        {
                            "type": "value_error",
                            "loc": ("INTEGRATION_SECRET_ENCRYPTION_KEY",),
                            "msg": "Chave de criptografia de integrações insegura em produção.",
                            "input": "[redacted]",
                            "ctx": {
                                "error": (
                                    "INTEGRATION_SECRET_ENCRYPTION_KEY deve ter ao menos "
                                    "32 bytes aleatórios e ser diferente de SECRET_KEY."
                                )
                            },
                        }
                    ],
                )
            if any(origin in insecure_origins for origin in self.CORS_ORIGINS):
                raise ValidationError.from_exception_data(
                    "Settings",
                    [
                        {
                            "type": "value_error",
                            "loc": ("CORS_ORIGINS",),
                            "msg": "CORS com origem coringa não é permitido em produção.",
                            "input": self.CORS_ORIGINS,
                            "ctx": {"error": "CORS inseguro em produção."},
                        }
                    ],
                )
            frontend_surfaces = {
                "FRONTEND_URL": self.FRONTEND_URL.strip().rstrip("/"),
                "BLACKBOX_FRONTEND_URL": self.BLACKBOX_FRONTEND_URL.strip().rstrip("/"),
            }
            parsed_surfaces = {name: urlparse(origin) for name, origin in frontend_surfaces.items()}
            invalid_surfaces = [
                name
                for name, parsed in parsed_surfaces.items()
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.path not in {"", "/"}
                    or parsed.params
                    or parsed.query
                    or parsed.fragment
                )
            ]
            if invalid_surfaces:
                raise ValueError(
                    "FRONTEND_URL e BLACKBOX_FRONTEND_URL devem ser origens HTTPS sem "
                    "caminho, credenciais, query ou fragmento em produção."
                )
            if (
                parsed_surfaces["FRONTEND_URL"].hostname.casefold()
                == parsed_surfaces["BLACKBOX_FRONTEND_URL"].hostname.casefold()
            ):
                raise ValueError(
                    "O aplicativo e a Blackbox devem usar hostnames distintos em produção."
                )
            if self.AUTH_REFRESH_COOKIE_DOMAIN and self.AUTH_REFRESH_COOKIE_DOMAIN.strip():
                raise ValueError(
                    "AUTH_REFRESH_COOKIE_DOMAIN deve permanecer vazio em produção para "
                    "manter cookies host-only entre o aplicativo e a Blackbox."
                )
            configured_origins = {origin.strip().rstrip("/") for origin in self.CORS_ORIGINS}
            missing_surfaces = sorted(set(frontend_surfaces.values()) - configured_origins)
            if missing_surfaces:
                raise ValueError(
                    "CORS_ORIGINS deve incluir as origens exatas do aplicativo e da "
                    f"Blackbox: {', '.join(missing_surfaces)}."
                )
            if not self.S3_PRIVATE_BUCKET_REQUIRED:
                raise ValueError("S3_PRIVATE_BUCKET_REQUIRED deve permanecer true em produção.")
            if self.KLM_MODEL_APIS_ENABLED and self.KLM_AI_BUDGET_BACKEND != "redis":
                raise ValueError("APIs de modelos em produção exigem KLM_AI_BUDGET_BACKEND=redis.")

        return self

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def deployment_environment(self) -> str:
        """Rótulo do deploy, separado da postura de segurança controlada por APP_ENV."""
        return self.ENVIRONMENT.strip() or self.APP_ENV

    @property
    def refresh_cookie_secure(self) -> bool:
        configured_urls = (
            self.FRONTEND_URL,
            self.BLACKBOX_FRONTEND_URL,
            self.PAPERCLIP_PUBLIC_URL,
        )
        return (
            self.AUTH_REFRESH_COOKIE_SECURE
            or self.is_production
            or any(urlparse(url).scheme == "https" for url in configured_urls)
        )

    @property
    def is_local_only(self) -> bool:
        return (
            not self.KLM_EXTERNAL_ACTIONS_ENABLED
            and not self.KLM_PRIVATE_AWS_STORAGE_ENABLED
            and not self.KLM_AWS_ENABLED
            and not self.KLM_MODEL_APIS_ENABLED
        )

    @property
    def uses_local_infrastructure(self) -> bool:
        """Model inference may be online while storage, cache and channels stay local."""
        return self.KLM_RUNTIME_MODE == "local_simulation"

    @property
    def uses_in_memory_redis(self) -> bool:
        """Keep ordinary local runs zero-dependency unless the full stack opts in."""
        return self.uses_local_infrastructure and not self.KLM_LOCAL_REDIS_ENABLED


@lru_cache
def get_settings() -> Settings:
    """Retorna instância cacheada das configurações."""
    return Settings()


settings = get_settings()
