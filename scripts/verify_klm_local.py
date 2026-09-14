"""Verifica o contrato local do KLM Hub sem acessar rede externa."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from uuid import uuid4

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
EXPECTED_AGENT_KEYS = frozenset(
    {
        "orquestrador",
        "hermes",
        "sdr",
        "juridico",
        "crm",
        "closer",
        "secretaria",
        "paralegal",
        "peticao",
        "revisor_juridico",
        "andamento",
        "financeiro",
        "cobranca",
        "marketing",
        "track",
        "infoproduto",
        "audio",
        "auditor",
        "ti",
        "qualidade",
        "sdr_saude_vendas",
        "pos_venda_cliente",
        "trafego_google",
        "tracking_engineer",
        "disparos_meta",
        "atendimento_meta",
        "conciliacao_financeira",
        "contas_receber",
        "controladoria_financeira",
        "orcamento_forecast",
        "tributario_reforma",
        "valuation",
    }
)
AUTHENTICATED_READS = (
    "/klm/dashboard",
    "/klm/kpis",
    "/klm/funnel",
    "/klm/quality",
    "/klm/tools",
    "/klm/agents",
    "/klm/agents/runs",
    "/klm/areas",
    "/klm/omnichannel/threads",
    "/klm/follow-ups",
    "/klm/tracking/events",
    "/klm/tracking/summary",
    "/klm/technical-requests",
    "/klm/activity",
    "/klm/external-intents",
    "/flows",
)


@dataclass
class HttpResult:
    status: int
    headers: dict[str, str]
    body: Any
    raw: str


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class LocalVerifier:
    def __init__(self, base_url: str, concurrency: int) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
            raise ValueError("O verificador aceita somente uma URL HTTP de loopback.")
        self.root = base_url.rstrip("/")
        self.api = f"{self.root}/api/v1"
        self.concurrency = concurrency
        self.token: str | None = None
        self.checks: list[str] = []

    def check(self, condition: bool, label: str, detail: Any = None) -> None:
        if not condition:
            suffix = f" Detalhe: {detail!r}" if detail is not None else ""
            raise AssertionError(f"FALHOU: {label}.{suffix}")
        self.checks.append(label)

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        authenticated: bool = True,
        headers: dict[str, str] | None = None,
        api: bool = True,
    ) -> HttpResult:
        request_headers = {"Accept": "application/json", **(headers or {})}
        if authenticated:
            if not self.token:
                raise RuntimeError("Sessão local ainda não foi criada.")
            request_headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        url = f"{self.api if api else self.root}{path}"
        request = Request(url, data=data, headers=request_headers, method=method)
        try:
            response = urlopen(request, timeout=30)
            raw = response.read().decode("utf-8")
            status = response.status
            response_headers = {key.lower(): value for key, value in response.headers.items()}
        except HTTPError as error:
            raw = error.read().decode("utf-8")
            status = error.code
            response_headers = {key.lower(): value for key, value in error.headers.items()}
        except URLError as error:
            raise RuntimeError(f"API local indisponível em {url}: {error.reason}") from error
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw
        return HttpResult(status, response_headers, body, raw)

    def expect(self, method: str, path: str, status: int = 200, **kwargs: Any) -> HttpResult:
        result = self.request(method, path, **kwargs)
        self.check(result.status == status, f"{method} {path} retorna {status}", result.raw[:500])
        return result

    def request_local_without_redirect(self, url: str) -> HttpResult:
        parsed = urlparse(url)
        self.check(
            parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS,
            "abertura de motor permanece em loopback",
            url,
        )
        request = Request(url, headers={"Accept": "text/html,application/json"}, method="GET")
        opener = build_opener(_NoRedirect())
        try:
            response = opener.open(request, timeout=30)
            raw = response.read().decode("utf-8")
            status = response.status
            response_headers = {key.lower(): value for key, value in response.headers.items()}
        except HTTPError as error:
            raw = error.read().decode("utf-8")
            status = error.code
            response_headers = {key.lower(): value for key, value in error.headers.items()}
        except URLError as error:
            raise RuntimeError(f"Gateway local indisponível em {url}: {error.reason}") from error
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw
        return HttpResult(status, response_headers, body, raw)

    def burst(
        self, path: str, payload: dict[str, Any], *, agent_response: bool = False
    ) -> tuple[list[HttpResult], str]:
        def send(_: int) -> HttpResult:
            return self.request("POST", path, payload=payload)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            results = list(pool.map(send, range(self.concurrency)))

        ids: list[str] = []
        deduplicated = 0
        for result in results:
            if isinstance(result.body, dict) and agent_response:
                runs = result.body.get("runs") or []
                if runs:
                    ids.append(runs[0]["id"])
                deduplicated += int(result.body.get("deduplicated") is True)
            elif isinstance(result.body, dict):
                if result.body.get("id"):
                    ids.append(result.body["id"])
                deduplicated += int(result.headers.get("x-klm-deduplicated") == "true")

        label = path.rsplit("/", 1)[-1]
        self.check(
            all(result.status == 201 for result in results),
            f"concorrência {label}: todas as respostas são 201",
            [(result.status, result.raw[:160]) for result in results],
        )
        self.check(
            len(ids) == self.concurrency and len(set(ids)) == 1,
            f"concorrência {label}: apenas um ID persiste",
            ids,
        )
        self.check(
            deduplicated == self.concurrency - 1,
            f"concorrência {label}: perdedores declaram deduplicação",
            deduplicated,
        )
        return results, ids[0]

    def run(self) -> None:
        self.check(urlparse(self.root).hostname in LOCAL_HOSTS, "destino permanece em loopback")

        health = self.expect("GET", "/health", authenticated=False, api=False)
        self.check(isinstance(health.body, dict), "health retorna JSON")

        unauthorized = self.request("GET", "/klm/kpis", authenticated=False)
        self.check(
            unauthorized.status == 401, "rota protegida rejeita sessão ausente", unauthorized.raw
        )

        bootstrap = self.expect("POST", "/klm/local/session", authenticated=False)
        self.token = bootstrap.body.get("accessToken") if isinstance(bootstrap.body, dict) else None
        self.check(bool(self.token), "bootstrap local emite JWT")
        workspace_token = self.token
        workspace_id = bootstrap.body.get("session", {}).get("activeWorkspace", {}).get("id")
        self.check(bool(workspace_id), "bootstrap local identifica o workspace ativo")
        stale_cleanup = self.expect("DELETE", "/klm/local/verification-artifacts").body
        self.check(
            isinstance(stale_cleanup.get("artifactsRemoved"), int),
            "limpeza inicial reconhece somente o namespace transitório",
            stale_cleanup,
        )

        runtime = self.expect("GET", "/klm/runtime", authenticated=False)
        expected_gates = {
            "mode": "local_simulation",
            "localOnly": True,
            "externalActionsEnabled": False,
            "awsEnabled": False,
            "onlineApproved": False,
            "localBootstrapEnabled": True,
        }
        self.check(
            all(runtime.body.get(key) == value for key, value in expected_gates.items()),
            "gates locais permanecem cumulativamente fechados",
            runtime.body,
        )
        providers = runtime.body.get("providers", {})
        offline_providers = {
            key: value for key, value in providers.items() if key != "models"
        }
        self.check(
            bool(offline_providers)
            and set(offline_providers.values()) == {"offline"}
            and providers.get("models") == "local_fake",
            "providers externos estão offline e modelos usam apenas o simulador local",
            providers,
        )

        origin = "http://127.0.0.1:4173"
        cors_get = self.expect(
            "GET", "/klm/runtime", authenticated=False, headers={"Origin": origin}
        )
        self.check(
            cors_get.headers.get("access-control-allow-origin") == origin,
            "CORS GET aceita somente a SPA local",
        )
        preflight = self.expect(
            "OPTIONS",
            "/klm/tracking/events",
            authenticated=False,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        self.check(
            preflight.headers.get("access-control-allow-origin") == origin,
            "CORS preflight aceita a SPA local",
        )

        openapi = self.expect("GET", "/openapi.json", authenticated=False, api=False)
        operations = [
            operation
            for path_item in openapi.body.get("paths", {}).values()
            for operation in path_item.values()
            if isinstance(operation, dict) and operation.get("operationId")
        ]
        operation_ids = [operation["operationId"] for operation in operations]
        self.check(
            len(operation_ids) == len(set(operation_ids)),
            "OpenAPI não possui operationId duplicado",
        )

        reads: dict[str, HttpResult] = {}
        for path in AUTHENTICATED_READS:
            reads[path] = self.expect("GET", path)

        dashboard = reads["/klm/dashboard"].body
        self.check(
            dashboard["metrics"]["externalActions"] == 0, "dashboard comprova zero ações externas"
        )
        self.check(dashboard["runtime"]["localOnly"] is True, "dashboard expõe runtime local")

        areas = reads["/klm/areas"].body
        active_areas = [area for area in areas if area.get("isActive")]
        self.check(
            len(areas) == 18
            and not active_areas
            and {area.get("status") for area in areas} == {"blueprint"}
            and "saude_suplementar" in {area.get("areaKey") for area in areas},
            "catálogo jurídico nasce completo e sem ativação implícita",
            areas,
        )
        health_area = next(area for area in areas if area.get("areaKey") == "saude_suplementar")
        platform_session = self.expect(
            "POST", "/klm/local/platform-session", authenticated=False
        )
        platform_token = platform_session.body.get("accessToken")
        self.check(bool(platform_token), "bootstrap local emite identidade root separada")
        self.token = platform_token
        try:
            mfa_status = self.expect("GET", "/auth/mfa").body
            if not mfa_status.get("enabled"):
                enrollment = self.expect("POST", "/auth/mfa/enroll").body
                self.expect(
                    "POST",
                    f"/auth/mfa/enroll/{enrollment['challengeId']}/verify",
                    payload={"code": enrollment["simulatedCode"]},
                )
            admin_challenge = self.expect(
                "POST", "/auth/admin-sessions/challenge"
            ).body
            administrative_session = self.expect(
                "POST",
                f"/auth/admin-sessions/challenge/{admin_challenge['challengeId']}/verify",
                payload={"code": admin_challenge["simulatedCode"]},
            ).body
            administrative_session_token = administrative_session.get(
                "administrativeSessionToken"
            )
            self.check(
                bool(administrative_session_token),
                "step-up MFA local emite sessão administrativa atribuída",
            )
            direct_access = self.expect(
                "POST",
                f"/platform/support-access/root-direct/{workspace_id}",
                200,
                payload={"reason": "Ativar a área da prova local auditável."},
                headers={"X-Administrative-Session": administrative_session_token},
            )
            self.token = direct_access.body.get("supportAccessToken")
            self.check(bool(self.token), "root direto emite sessão administrativa atribuída")
            agent_catalog = self.expect("GET", "/agent-platform/catalog").body
            catalog_by_key = {
                item.get("key"): item for item in agent_catalog.get("agents", [])
            }
            for deployment_agent_key in (
                "orquestrador",
                "sdr",
                "juridico",
                "crm",
                "secretaria",
            ):
                definition = catalog_by_key[deployment_agent_key]
                allowed_models = definition.get("allowedModels") or []
                allowed_tools = definition.get("tools") or []
                self.check(
                    bool(allowed_models) and bool(allowed_tools),
                    f"agente {deployment_agent_key} possui modelo e ferramentas governados",
                    {"models": allowed_models, "tools": allowed_tools},
                )
                deployment = self.expect(
                    "POST",
                    "/agent-platform/deployments",
                    payload={
                        "agent_key": deployment_agent_key,
                        "role_package": "office_owner",
                        "version": 1,
                        "config": {
                            "definitionVersion": definition.get("version", 1),
                            "rolePackage": "office_owner",
                            "model": allowed_models[0],
                            "tools": allowed_tools,
                            "knowledgeSources": ["matter_context"],
                            "autonomyPolicy": "A1",
                            "humanRoutingPolicy": "role_skill_hierarchy_v1",
                            "budgetPolicy": {"monthlyTokenLimit": 100000},
                        },
                        "gates": {},
                    },
                ).body
                deployment_id = deployment.get("id")
                self.check(
                    bool(deployment_id),
                    f"deployment local de {deployment_agent_key} é persistido",
                )
                self.expect(
                    "POST", f"/agent-platform/deployments/{deployment_id}/validate"
                )
                self.expect(
                    "POST", f"/agent-platform/deployments/{deployment_id}/publish"
                )
            self.expect("POST", f"/klm/areas/{health_area['id']}/validate")
            activated_area = self.expect(
                "PATCH",
                f"/klm/areas/{health_area['id']}/activation",
                payload={"enabled": True},
            ).body
            self.check(
                activated_area.get("isActive") is True
                and activated_area.get("status") == "active",
                "root direto ativa explicitamente a área da prova",
                activated_area,
            )
        finally:
            self.token = workspace_token

        registry = reads["/klm/agents"].body
        agents = registry.get("agents", [])
        agent_keys = {agent.get("key") for agent in agents}
        registry_metrics = registry.get("metrics", {})
        human_requesters = [
            agent["key"] for agent in agents if agent["permissions"].get("requestHuman")
        ]
        self.check(
            agent_keys == EXPECTED_AGENT_KEYS,
            (
                "registry contém o catálogo canônico dos "
                f"{len(EXPECTED_AGENT_KEYS)} agentes especializados"
            ),
            {
                "missing": sorted(EXPECTED_AGENT_KEYS - agent_keys),
                "unexpected": sorted(agent_keys - EXPECTED_AGENT_KEYS),
            },
        )
        self.check(
            registry_metrics.get("modeled") == len(EXPECTED_AGENT_KEYS),
            "métrica modeled reconcilia o catálogo de agentes",
            registry_metrics,
        )
        self.check(
            registry_metrics.get("coordinationAgent") == "orquestrador"
            and registry_metrics.get("ownerBridgeAgent") == "hermes",
            "registry separa coordenação do sistema e ponte com o proprietário",
            registry_metrics,
        )
        self.check(
            human_requesters == ["hermes"],
            "somente Hermes solicita intervenção humana",
            human_requesters,
        )
        self.check(
            not any(agent["permissions"].get("sendExternal") for agent in agents),
            "nenhum agente pode enviar externamente",
        )

        capability_catalog = self.expect("GET", "/capabilities").body
        capability_summary = capability_catalog.get("summary", {})
        self.check(
            capability_catalog.get("schemaVersion") == "3.0"
            and capability_catalog.get("scope") == "klm-native-capabilities",
            "catálogo usa o contrato público de capacidades KLM",
            {
                "schemaVersion": capability_catalog.get("schemaVersion"),
                "scope": capability_catalog.get("scope"),
            },
        )
        self.check(
            capability_summary.get("total") == 135
            and capability_summary.get("available") == 123,
            "catálogo reconcilia as 135 capacidades e 123 disponíveis localmente",
            capability_summary,
        )
        self.check(
            capability_summary.get("byDomain")
            == {
                "communications": 39,
                "collaboration": 33,
                "automation": 27,
                "crm": 36,
            },
            "catálogo distribui todas as capacidades nos quatro domínios KLM",
            capability_summary.get("byDomain"),
        )
        self.check(
            capability_summary.get("externalEffectVerified") == 0,
            "catálogo não presume efeitos externos no ambiente local",
            capability_summary,
        )
        dispositions = capability_summary.get("byDisposition", {})
        self.check(
            dispositions.get("discovered", 0) == 0,
            "matriz de capacidades não possui item sem decisão",
            dispositions,
        )
        approved_without_exposure = [
            item.get("id")
            for item in capability_catalog.get("capabilities", [])
            if not str(item.get("disposition", "")).startswith("blocked_")
            and item.get("disposition") != "not_applicable"
            and item.get("exposure") == "none"
        ]
        self.check(
            not approved_without_exposure,
            "toda capacidade aprovada possui exposição no KLM",
            approved_without_exposure,
        )
        public_catalog = json.dumps(capability_catalog, ensure_ascii=False).lower()
        self.check(
            not any(
                imported_name in public_catalog
                for imported_name in (
                    "chatwoot",
                    "rocket.chat",
                    "rocketchat",
                    "chattermate",
                    "bottlecrm",
                )
            ),
            "catálogo público não expõe nomes dos produtos importados",
        )

        capabilities = capability_catalog.get("capabilities", [])

        def read_native_document(item: dict[str, Any]) -> tuple[dict[str, Any], HttpResult]:
            domain = str(item.get("domain") or "")
            capability = str(item.get("key") or "")
            return item, self.request(
                "GET", f"/capabilities/domains/{domain}/{capability}/document"
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            native_results = list(pool.map(read_native_document, capabilities))

        native_errors: list[tuple[str, int, str]] = []
        action_occurrences = 0
        unique_commands: set[str] = set()
        for capability, response in native_results:
            capability_id = str(capability.get("id") or "")
            if response.status != 200 or not isinstance(response.body, dict):
                native_errors.append((capability_id, response.status, response.raw[:180]))
                continue
            document = response.body
            expected_domain = str(capability.get("domain") or "")
            expected_key = str(capability.get("key") or "")
            valid_identity = (
                document.get("domain", {}).get("id") == expected_domain
                and document.get("capability", {}).get("id") == expected_key
            )
            actions = document.get("actions") or []
            tabs = document.get("tabs") or []
            serialized = json.dumps(document, ensure_ascii=False).lower()
            if (
                not valid_identity
                or not isinstance(actions, list)
                or not actions
                or not isinstance(tabs, list)
                or not tabs
                or "<iframe" in serialized
                or "[redacted]" in serialized
            ):
                native_errors.append((capability_id, response.status, "contrato nativo inválido"))
                continue
            action_occurrences += len(actions)
            unique_commands.update(
                str(action.get("id")) for action in actions if isinstance(action, dict)
            )

        self.check(
            len(native_results) == 135 and not native_errors,
            "as 135 rotas públicas retornam documento autenticado íntegro",
            native_errors[:12],
        )
        self.check(
            action_occurrences == 511 and len(unique_commands) == 360,
            "matriz KLM expõe 511 ações em 360 comandos estáveis",
            {"actionOccurrences": action_occurrences, "uniqueCommands": len(unique_commands)},
        )

        flows = reads["/flows"].body
        self.check(isinstance(flows, list) and bool(flows), "registry local possui flow canônico")
        flow = flows[0]
        flow_id = str(flow["id"])
        flow_revision = int(flow["current_revision"])
        first_flow_sync = self.expect(
            "POST", f"/automation/flows/{flow_id}/reconcile"
        ).body
        second_flow_sync = self.expect(
            "POST", f"/automation/flows/{flow_id}/reconcile"
        ).body
        self.check(
            first_flow_sync.get("externalExecutionEnabled") is False,
            "reconciliação do flow bloqueia efeitos externos",
            first_flow_sync,
        )
        self.check(
            second_flow_sync.get("status") == "synced"
            and second_flow_sync.get("graphAction") == "unchanged",
            "segunda sincronização do flow é idempotente",
            second_flow_sync,
        )
        flow_context = self.expect(
            "GET", f"/automation/flows/{flow_id}"
        ).body
        self.check(
            flow_context.get("linked") is True
            and flow_context.get("revision") == flow_revision
            and flow_context.get("digestMatches") is True
            and flow_context.get("graphMatches") is True
            and flow_context.get("externalExecutionEnabled") is False,
            "contexto confirma grafo íntegro da revisão canônica",
            flow_context,
        )

        threads = reads["/klm/omnichannel/threads"].body
        seed_thread = next((item for item in threads if item.get("matterId")), None)
        self.check(seed_thread is not None, "seed local possui thread vinculada a caso")
        self.expect("GET", f"/klm/omnichannel/threads/{seed_thread['id']}/messages")

        suffix = uuid4().hex
        tracking_key = f"verify-track-{suffix}"
        message_key = f"verify-message-{suffix}"
        intent_key = f"verify-intent-{suffix}"
        agent_key = f"verify-agent-{suffix}"

        email_key = f"verify-email-{suffix}"
        email_payload = {
            "messageId": email_key,
            "senderEmail": f"{suffix}@local.invalid",
            "senderName": "Lead de verificação",
            "subject": "Negativa de exame",
            "body": "O plano negou o exame prescrito.",
            "attachments": [
                {
                    "name": "negativa-local.pdf",
                    "mimeType": "application/pdf",
                    "contentText": "Negativa local usada somente no teste de RAG.",
                }
            ],
        }
        email_first = self.expect(
            "POST", "/klm/omnichannel/email-ingest", 201, payload=email_payload
        )
        email_second = self.expect(
            "POST", "/klm/omnichannel/email-ingest", 201, payload=email_payload
        )
        self.check(email_first.body.get("deduplicated") is False, "primeiro e-mail é ingerido")
        self.check(email_second.body.get("deduplicated") is True, "replay do e-mail é deduplicado")
        self.check(
            len(email_first.body.get("documentIds", [])) == 1,
            "anexo de e-mail vira documento local",
        )
        self.check(
            len(email_first.body.get("knowledgeChunkIds", [])) == 1,
            "anexo de e-mail entra no RAG local",
        )
        thread_id = email_first.body["thread"]["id"]
        matter_id = email_first.body["matterId"]

        native_agents = self.expect("GET", "/automation/agents?time_range=7d").body
        self.check(
            native_agents.get("linked") is True
            and native_agents.get("externalExecutionEnabled") is False
            and len(native_agents.get("agents", [])) == len(EXPECTED_AGENT_KEYS),
            "experiência nativa de agentes lê o runtime canônico",
            native_agents,
        )
        native_flow = self.expect("GET", f"/automation/flows/{flow_id}").body
        self.check(
            native_flow.get("linked") is True
            and native_flow.get("graphMatches") is True
            and native_flow.get("externalExecutionEnabled") is False,
            "editor nativo lê o grafo canônico sem efeitos externos",
            native_flow,
        )
        native_chat = self.expect(
            "GET", f"/communications/threads/{thread_id}"
        ).body
        native_collaboration = self.expect(
            "GET", f"/collaboration/threads/{thread_id}"
        ).body
        self.check(
            native_chat.get("linked") is True
            and native_collaboration.get("linked") is True
            and native_chat.get("threadId") == thread_id
            and native_collaboration.get("threadId") == thread_id
            and native_chat.get("projection", {}).get("externalChannelDeliveryEnabled") is False,
            "chat e colaboração nativos compartilham a identidade KLM",
            {
                "chat": native_chat.get("threadId"),
                "collaboration": native_collaboration.get("threadId"),
            },
        )
        native_funnel = self.expect("GET", "/crm/funnel").body
        native_matter = self.expect("GET", f"/crm/matters/{matter_id}").body
        native_work = self.expect("GET", "/crm/work").body
        native_finance = self.expect("GET", "/crm/finance").body
        self.check(
            native_funnel.get("projection", {}).get("linked") is True
            and native_matter.get("matterId") == matter_id
            and native_work.get("projection", {}).get("linked") is True
            and native_finance.get("projection", {}).get("linked") is True
            and all(
                projection.get("externalExecutionEnabled") is False
                for projection in (
                    native_funnel.get("projection", {}),
                    native_work.get("projection", {}),
                    native_finance.get("projection", {}),
                )
            ),
            "CRM nativo entrega funil, caso, trabalho e financeiro no mesmo tenant",
        )

        self.burst(
            "/klm/tracking/events",
            {
                "eventName": "Lead",
                "channel": "local_hardening",
                "source": "verifier",
                "campaign": "concurrency",
                "dedupeKey": tracking_key,
            },
        )
        message_results, _ = self.burst(
            f"/klm/omnichannel/threads/{thread_id}/messages",
            {
                "channel": "whatsapp",
                "direction": "outbound",
                "authorType": "human",
                "authorLabel": "Verificador local",
                "body": "Prova concorrente local; nenhuma mensagem foi enviada.",
                "idempotencyKey": message_key,
            },
        )
        self.check(
            all(result.body.get("deliveryStatus") == "local_draft" for result in message_results),
            "mensagem concorrente permanece rascunho local",
        )
        intent_results, _ = self.burst(
            "/klm/external-intents",
            {
                "actionType": "send_message",
                "provider": "whatsapp",
                "idempotencyKey": intent_key,
                "payload": {"localOnly": True},
            },
        )
        self.check(
            all(result.body.get("status") == "blocked_external" for result in intent_results),
            "intenção concorrente permanece bloqueada",
        )
        agent_results, _ = self.burst(
            "/klm/agents/runs",
            {
                "agentKey": "sdr",
                "matterId": matter_id,
                "threadId": thread_id,
                "requestHuman": False,
                "instructions": "Executar somente a prova determinística local.",
                "idempotencyKey": agent_key,
            },
            agent_response=True,
        )
        self.check(
            all(result.body.get("externalActionsExecuted") == 0 for result in agent_results),
            "agente concorrente executa zero ações externas",
        )
        agent_dedupe_keys = {
            run.get("dedupeKey")
            for result in agent_results
            for run in (result.body.get("runs") or [])
            if isinstance(run, dict)
        }
        self.check(
            len(agent_dedupe_keys) == 1 and None not in agent_dedupe_keys,
            "agente concorrente retorna uma única chave de deduplicação",
            sorted(str(key) for key in agent_dedupe_keys),
        )

        tracking = self.expect("GET", "/klm/tracking/events").body
        self.check(
            sum(item.get("dedupeKey") == tracking_key for item in tracking) == 1,
            "ledger contém um evento concorrente",
        )
        messages = self.expect("GET", f"/klm/omnichannel/threads/{thread_id}/messages").body
        self.check(
            sum(item.get("idempotencyKey") == message_key for item in messages) == 1,
            "thread contém uma mensagem concorrente",
        )
        intents = self.expect("GET", "/klm/external-intents").body
        self.check(
            sum(item.get("idempotencyKey") == intent_key for item in intents) == 1,
            "ledger contém uma intenção concorrente",
        )
        self.check(
            sum(item.get("idempotencyKey") == f"message:{message_key}" for item in intents) == 1,
            "mensagem gera uma única intenção bloqueada",
        )
        runs = self.expect("GET", f"/klm/agents/runs?matter_id={matter_id}&limit=500").body
        full_agent_key = next(iter(agent_dedupe_keys))
        self.check(
            sum(item.get("dedupeKey") == full_agent_key for item in runs) == 1,
            "caso contém um agent run concorrente",
        )

        simulation_key = f"verify-simulation-{suffix}"
        simulation_payload = {
            "matterId": matter_id,
            "threadId": thread_id,
            "idempotencyKey": simulation_key,
        }
        simulation_first = self.expect(
            "POST", "/klm/agents/simulations/health-intake", 201, payload=simulation_payload
        )
        simulation_second = self.expect(
            "POST", "/klm/agents/simulations/health-intake", 201, payload=simulation_payload
        )
        first_runs = simulation_first.body.get("runs", [])
        second_runs = simulation_second.body.get("runs", [])
        first_ids = [run["id"] for run in first_runs]
        second_ids = [run["id"] for run in second_runs]
        expected_chain = [
            "orquestrador",
            "sdr",
            "juridico",
            "crm",
            "secretaria",
            "orquestrador",
        ]
        self.check(
            [run.get("agentName") for run in first_runs] == expected_chain,
            "simulação separa orquestração, operação e ponte humana em seis etapas",
            [run.get("agentName") for run in first_runs],
        )
        self.check(first_ids == second_ids, "replay da simulação preserva os mesmos runs")
        self.check(
            simulation_second.body.get("deduplicated") is True, "replay da simulação é deduplicado"
        )
        self.check(
            simulation_first.body.get("externalActionsExecuted") == 0,
            "simulação executa zero ações externas",
        )

        final_dashboard = self.expect("GET", "/klm/dashboard").body
        self.check(
            final_dashboard["metrics"]["externalActions"] == 0,
            "estado final mantém zero ações externas",
        )
        cleanup = self.expect("DELETE", "/klm/local/verification-artifacts").body
        self.check(
            cleanup.get("artifactsRemoved", 0) > 0,
            "hardening remove seus próprios artefatos",
            cleanup,
        )
        clean_funnel = self.expect("GET", "/klm/funnel").body
        self.check(
            not any(
                card.get("name") == "Lead de verificação" for card in clean_funnel.get("cards", [])
            ),
            "funil termina sem lead transitório",
        )
        clean_events = self.expect("GET", "/klm/tracking/events").body
        self.check(
            not any(str(item.get("dedupeKey", "")).startswith("verify-") for item in clean_events),
            "tracking termina sem evento transitório",
        )
        clean_intents = self.expect("GET", "/klm/external-intents").body
        self.check(
            not any("verify-" in str(item.get("idempotencyKey", "")) for item in clean_intents),
            "ledger termina sem intenção transitória",
        )


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verifica o KLM Hub estritamente local.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--concurrency", type=int, default=8, choices=range(2, 33))
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    verifier = LocalVerifier(args.base_url, args.concurrency)
    failed = False
    try:
        verifier.run()
    except Exception as error:
        failed = True
        print(str(error), file=sys.stderr)
        print(f"Checks concluídos antes da falha: {len(verifier.checks)}", file=sys.stderr)
    finally:
        if verifier.token:
            try:
                cleanup = verifier.request("DELETE", "/klm/local/verification-artifacts")
                if cleanup.status != 200:
                    failed = True
                    print(
                        "Falha ao limpar artefatos do hardening: "
                        f"{cleanup.status} {cleanup.raw[:300]}",
                        file=sys.stderr,
                    )
            except Exception as cleanup_error:
                failed = True
                print(f"Falha ao limpar artefatos do hardening: {cleanup_error}", file=sys.stderr)
    if failed:
        return 1
    print(
        json.dumps(
            {
                "status": "PASS",
                "baseUrl": verifier.root,
                "checks": len(verifier.checks),
                "concurrency": verifier.concurrency,
                "externalActionsExecuted": 0,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
