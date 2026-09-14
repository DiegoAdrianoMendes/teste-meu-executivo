from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.oss_census.census import _load_json, _skipped_pin_receipt, _validate_upstream_lock
from scripts.oss_census.common import ExtractionError, specialized_category, text_scope
from scripts.oss_census.dispositions import (
    DispositionError,
    DispositionPolicy,
    DispositionRule,
)
from scripts.oss_census.extractors import (
    bottlecrm,
    chattermate,
    chatwoot,
    embedded_chat,
    rocketchat,
)
from scripts.oss_census.licenses import build_license_scopes, resolve_license_scope
from scripts.oss_census.model import Observation, ParseResult, canonical_contract_key
from scripts.oss_census.python_ast import parser_receipt

REPO_ROOT = Path(__file__).resolve().parents[2]


def _observations(result: ParseResult) -> tuple[Observation, ...]:
    assert isinstance(result, ParseResult)
    assert all(
        candidate.status in {"accepted", "excluded", "manual_review"}
        for candidate in result.candidates
    )
    return result.observations


def _categories(result: ParseResult) -> set[str]:
    return {item.category for item in _observations(result)}


def _assert_canonical(result: ParseResult) -> None:
    for item in _observations(result):
        assert item.key == canonical_contract_key(
            transport=item.transport,
            operation=item.operation,
            route=item.route,
            handler=item.handler,
        )
        assert "#declaration" not in item.key


def test_exact_cpython_312_toolchain_is_registered() -> None:
    assert parser_receipt() == {
        "implementation": "CPython",
        "runtimeVersion": "3.12.13",
        "requiredRuntimeVersion": "3.12.13",
        "requiredMajorMinor": "3.12",
        "parser": "cpython-stdlib-ast-pep701",
        "grammarVersion": "3.12",
        "featureVersion": [3, 12],
        "pep701": True,
        "fallbackParser": None,
    }


def test_structural_categories_do_not_use_arbitrary_substrings() -> None:
    assert specialized_category("api", "/hooks/provider") == "webhook"
    assert specialized_category("api", "/contacts/import") == "import"
    assert specialized_category("api", "/reports/export") == "export"
    assert specialized_category("api", "/contacts/importer") == "api"
    assert specialized_category("api", "/reports/exportable") == "api"
    assert specialized_category("job", "socket.io-presence") == "job"
    assert specialized_category("job", "contact-export") == "job"


def test_chatwoot_extracts_rails_symbols_root_devise_vue_jobs_and_config() -> None:
    routes = chatwoot._rails_routes(
        "config/routes.rb",
        """namespace :api do
  resources :contacts, only: %i[index create]
  root :status
  devise_for :users, path: 'auth', controllers: { sessions: 'api/sessions' }
end
if ChatwootApp.enterprise?
  resources :secret_records
end
""",
    )
    jobs = chatwoot._jobs(
        "app/jobs/contact_export_job.rb",
        "class ContactExportJob < ApplicationJob\nend\n",
    )
    pages = chatwoot._vue_routes(
        "app/javascript/dashboard/routes/settings.routes.js",
        """export default {
  routes: [{
    path: frontendURL('accounts/:accountId/settings'),
    component: SettingsWrapper,
    children: [{
      path: 'list',
      name: 'settings_list',
      component: SettingsList,
    }],
  }],
};
""",
    )
    config = chatwoot._yaml_config(
        "config/features.yml",
        "- name: inbox_automation\n- key: contact_import\n",
    )

    rails = _observations(routes)
    assert {(item.method, item.route) for item in rails} >= {
        ("GET", "/api/contacts"),
        ("POST", "/api/contacts"),
        ("GET", "/api"),
    }
    assert any(item.handler == "api/sessions#create" for item in rails)
    assert any(
        candidate.status == "excluded" and candidate.reason == "proprietary_guard"
        for candidate in routes.candidates
    )
    assert _observations(jobs)[0].category == "job"
    child = next(
        item for item in _observations(pages) if item.details["routeName"] == "settings_list"
    )
    assert child.route == "/accounts/:accountId/settings/list"
    assert child.handler.startswith("settings_list@")
    assert len(_observations(config)) == 2
    for result in (routes, jobs, pages, config):
        _assert_canonical(result)


def test_chattermate_extracts_public_app_fastapi_realtime_workflow_tools_and_vue() -> None:
    routes = chattermate._fastapi_routes(
        "backend/app/main.py",
        """from fastapi import APIRouter, FastAPI
API_PREFIX = '/api'
router = APIRouter(prefix=API_PREFIX)
public_app = FastAPI()

@router.post('/leads')
async def create_lead():
    pass

@router.websocket('/events')
async def events():
    pass

@public_app.get('/healthz')
async def public_health():
    pass
""",
    )
    socket_events = chattermate._socketio_events(
        "backend/app/core/socketio.py",
        """@sio.on('typing', namespace='/inbox')
async def typing_event():
    pass
""",
    )
    nodes = chattermate._workflow_nodes(
        "backend/app/models/workflow_node.py",
        "class NodeType:\n    MESSAGE = 'message'\n    HANDOFF = 'handoff'\n",
    )
    tools = chattermate._tool_classes(
        "backend/app/tools/crm.py",
        "class CRMTool(Toolkit):\n    pass\nclass MCPTool(Toolkit):\n    pass\n",
    )
    pages = chattermate._vue_routes(
        "frontend/src/router/index.ts",
        "{ path: '/flows', name: 'flows', component: Flows }",
    )

    assert _categories(routes) == {"api", "realtime"}
    public = next(item for item in _observations(routes) if item.route == "/healthz")
    assert public.details["application"] == "public_app"
    assert _observations(socket_events)[0].details["event"] == "typing"
    assert {item.name for item in _observations(nodes)} == {"message", "handoff"}
    assert _categories(tools) == {"job", "mcp"}
    assert _observations(pages)[0].route == "/flows"
    for result in (routes, socket_events, nodes, tools, pages):
        _assert_canonical(result)


def test_bottlecrm_extracts_django_celery_mcp_permissions_and_svelte_actions() -> None:
    routes = bottlecrm._django_routes(
        "backend/crm/urls.py",
        """from django.urls import path
class LeadList:
    def get(self, request):
        pass

urlpatterns = [path('leads/', LeadList.as_view(), name='lead-list')]
router.register('accounts', AccountViewSet)
""",
    )
    realtime = bottlecrm._django_routes(
        "backend/crm/routing.py",
        "websocket_urlpatterns = [path('ws/events/', EventsConsumer.as_asgi())]\n",
    )
    jobs = bottlecrm._celery_tasks(
        "backend/crm/tasks.py",
        "@shared_task(name='crm.refresh_scores')\ndef refresh_scores():\n    pass\n",
    )
    same_name_a = bottlecrm._celery_tasks(
        "backend/accounts/tasks.py",
        "@shared_task\ndef send_email():\n    pass\n",
    )
    same_name_b = bottlecrm._celery_tasks(
        "backend/invoices/tasks.py",
        "@shared_task\ndef send_email():\n    pass\n",
    )
    tools = bottlecrm._mcp_tools(
        "mcp_server/src/server.py",
        "@mcp.tool(name='search_leads')\ndef search_leads():\n    pass\n",
    )
    permissions = bottlecrm._permissions(
        "backend/crm/permissions.py",
        "class IsWorkspaceMember(BasePermission):\n"
        "    def has_permission(self):\n"
        "        return True\n",
    )
    pages = bottlecrm._svelte_pages(
        "frontend/src/routes/(app)/leads/+page.svelte",
        "<h1>Leads</h1>",
    )
    page_server = bottlecrm._svelte_page_server(
        "frontend/src/routes/(app)/leads/+page.server.ts",
        """export const load = async () => ({ leads: [] });
export const actions = {
  default: async () => ({ ok: true }),
  save: async () => ({ ok: true }),
};
""",
    )
    server = bottlecrm._svelte_server_routes(
        "frontend/src/routes/api/leads/+server.ts",
        "export async function GET() {}\nexport const POST = async () => {};",
    )

    assert len(_observations(routes)) == 2
    django_route = next(item for item in _observations(routes) if item.family == "django-url-route")
    drf_router = next(
        item for item in _observations(routes) if item.family == "drf-router-registration"
    )
    assert django_route.operation == "GET"
    assert drf_router.operation == "REST"
    assert _observations(realtime)[0].family == "django-channels-route"
    assert _observations(realtime)[0].operation == "connect"
    assert all(item.operation != "ANY" for item in _observations(routes))
    assert _observations(jobs)[0].route == "crm.refresh_scores"
    first_task = _observations(same_name_a)[0]
    second_task = _observations(same_name_b)[0]
    assert first_task.route == "accounts.tasks.send_email"
    assert second_task.route == "invoices.tasks.send_email"
    assert first_task.key != second_task.key
    assert _observations(tools)[0].category == "mcp"
    assert _observations(permissions)[0].category == "permission"
    assert _observations(pages)[0].route == "/leads"
    assert {(item.operation, item.route) for item in _observations(page_server)} == {
        ("load", "/leads"),
        ("POST", "/leads"),
        ("POST", "/leads?/save"),
    }
    assert {item.method for item in _observations(server)} == {"GET", "POST"}
    for result in (
        routes,
        realtime,
        jobs,
        same_name_a,
        same_name_b,
        tools,
        permissions,
        pages,
        page_server,
        server,
    ):
        _assert_canonical(result)


def test_bottlecrm_django_ast_resolves_real_methods_and_marks_ambiguity(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "backend" / "crm"
    backend.mkdir(parents=True)
    (backend / "views.py").write_text(
        """from django.views import View
from django.views.decorators.http import require_http_methods

class GetOnlyView(View):
    def get(self, request):
        pass

class PostOnlyView(View):
    def post(self, request):
        pass

class MultiMethodView(View):
    def get(self, request):
        pass
    def post(self, request):
        pass
    def put(self, request):
        pass
    def patch(self, request):
        pass
    def delete(self, request):
        pass

class LocalGetBase(View):
    def get(self, request):
        pass

class InheritedView(LocalGetBase):
    def post(self, request):
        pass

@require_http_methods(["PATCH"])
def function_view(request):
    pass
""",
        encoding="utf-8",
    )
    (backend / "urls.py").write_text(
        """from django.urls import path
from crm.views import (
    GetOnlyView,
    InheritedView,
    MultiMethodView,
    PostOnlyView,
    function_view,
)

urlpatterns = [
    path("get-only/", GetOnlyView.as_view()),
    path("post-only/", PostOnlyView.as_view()),
    path("multi/", MultiMethodView.as_view()),
    path("function/", function_view),
    path("inherited/", InheritedView.as_view()),
    path("unresolved/", MissingView.as_view()),
]

router.register("accounts", AccountViewSet)
""",
        encoding="utf-8",
    )
    (backend / "routing.py").write_text(
        "websocket_urlpatterns = [path('ws/events/', EventsConsumer.as_asgi())]\n",
        encoding="utf-8",
    )

    extraction = bottlecrm.extract(tmp_path)
    route_scope = next(
        scope for scope in extraction.scopes if scope.scope_id == "django-drf-routes"
    )
    methods_by_route = {
        route: {
            item.operation
            for item in route_scope.observations
            if item.family == "django-url-route" and item.route == route
        }
        for route in ("/get-only", "/post-only", "/multi", "/function", "/inherited")
    }

    assert methods_by_route == {
        "/get-only": {"GET"},
        "/post-only": {"POST"},
        "/multi": {"GET", "POST", "PUT", "PATCH", "DELETE"},
        "/function": {"PATCH"},
        "/inherited": {"GET", "POST"},
    }
    assert not any(item.operation == "ANY" for item in route_scope.observations)
    assert not any(item.route == "/unresolved" for item in route_scope.observations)

    unresolved = next(
        candidate
        for candidate in route_scope.candidates
        if candidate.details.get("route") == "/unresolved"
    )
    assert unresolved.status == "manual_review"
    assert unresolved.reason == "ambiguous_django_route_target"
    assert unresolved.details["resolution"] == "ambiguous"
    assert unresolved.details["ambiguity"] == "local_class_target_not_resolved"

    router = next(
        item for item in route_scope.observations if item.family == "drf-router-registration"
    )
    assert router.operation == "REST"

    realtime_scope = next(
        scope for scope in extraction.scopes if scope.scope_id == "django-channels-routes"
    )
    assert {(item.family, item.operation) for item in realtime_scope.observations} == {
        ("django-channels-route", "connect")
    }

    support_scope = next(
        scope for scope in extraction.scopes if scope.scope_id == "django-python-support"
    )
    assert support_scope.includes == ("backend/**/*.py",)
    assert {item.path for item in support_scope.files} == {
        "backend/crm/routing.py",
        "backend/crm/urls.py",
        "backend/crm/views.py",
    }


def test_rocketchat_extracts_multimethod_addroute_ddp_and_structural_units() -> None:
    rest = rocketchat._rest_routes(
        "apps/meteor/app/api/server/v1/livechat.ts",
        """API.v1.addRoute('livechat/appearance', {
  permissionsRequired: ['view-livechat-settings'],
}, {
  async get() {},
  post() {},
  delete: async function remove() {},
});
""",
    )
    ddp = rocketchat._ddp_contracts(
        "apps/meteor/app/rooms/server/publications.ts",
        """Meteor.publish('room-data', function publishRoom() {});
Meteor.methods({
  'joinRoom': async function joinRoom() {},
  leaveRoom() {},
  async archiveRoom() {},
});
""",
    )
    adapter = rocketchat._ddp_contracts(
        "apps/meteor/server/sdk/Registrar.ts",
        """registerMethod(name: string): void {
  Meteor.methods(methods);
}
""",
    )
    permissions = rocketchat._permissions(
        "apps/meteor/app/authorization/server/constant/permissions.ts",
        "{ _id: 'view-room', roles: ['admin', 'user'] }",
    )
    settings = rocketchat._settings(
        "apps/meteor/server/settings/accounts.ts",
        "settingsRegistry.add('Accounts_RegistrationForm');",
    )
    jobs = rocketchat._cron_jobs(
        "apps/meteor/server/cron/cleanup.ts",
        "const cleanupName = 'cleanup-messages';\n"
        "cronJobs.add(cleanupName, '* * * * *', () => {});",
    )
    pages = rocketchat._ui_routes(
        "apps/meteor/client/routes.ts",
        "{ path: '/admin', id: 'admin-home' }\nregisterPublicRoute('/login', { name: 'login' });",
    )

    assert {item.method for item in _observations(rest)} == {"GET", "POST", "DELETE"}
    assert all(
        item.details["permissions"] == ["view-livechat-settings"] for item in _observations(rest)
    )
    assert {item.operation for item in _observations(ddp)} == {
        "subscribe",
        "joinRoom",
        "leaveRoom",
        "archiveRoom",
    }
    assert not adapter.observations
    assert len(adapter.candidates) == 1
    assert adapter.candidates[0].status == "excluded"
    assert adapter.candidates[0].reason == "dynamic_registration_adapter"
    assert _observations(permissions)[0].details["defaultRoles"] == ["admin", "user"]
    assert _observations(settings)[0].category == "config"
    assert _observations(jobs)[0].category == "job"
    assert len(_observations(pages)) == 2
    for result in (rest, ddp, adapter, permissions, settings, jobs, pages):
        _assert_canonical(result)


def test_embedded_chat_uses_package_prefix_licenses_and_structural_categories(
    tmp_path: Path,
) -> None:
    api = embedded_chat._api_methods(
        "packages/api/src/EmbeddedChatApi.ts",
        """export default class EmbeddedChatApi {
  constructor(config: object) {}
  async sendMessage(text: string) {}
  typingListener() {}
}
""",
    )
    exports = embedded_chat._react_exports(
        "packages/react/src/index.js",
        "export { default as EmbeddedChat } from './views/EmbeddedChat';",
    )
    props = embedded_chat._react_props(
        "packages/react/src/views/EmbeddedChat.js",
        """EmbeddedChat.propTypes = {
  host: PropTypes.string,
  roomId: PropTypes.string,
};
""",
    )

    assert _categories(api) == {"api", "config"}
    assert (
        next(item for item in _observations(api) if item.name == "typingListener").category == "api"
    )
    assert _observations(exports)[0].category == "ui"
    assert {item.name for item in _observations(props)} == {
        "EmbeddedChat.host",
        "EmbeddedChat.roomId",
    }

    for package_name, version, license_id in (
        ("@embeddedchat/api", "0.1.3", "ISC"),
        ("@embeddedchat/react", "0.2.3", "ISC"),
    ):
        package_dir = tmp_path / "packages" / package_name.rsplit("/", 1)[-1]
        package_dir.mkdir(parents=True)
        (package_dir / "package.json").write_text(
            json.dumps({"name": package_name, "version": version, "license": license_id}),
            encoding="utf-8",
        )
    scopes = build_license_scopes(
        tmp_path,
        {
            "name": "rocket-embedded-chat",
            "license": {
                "status": "package-metadata-only",
                "conclusion": "Repository root has no verified license file.",
            },
        },
    )
    api_scope = resolve_license_scope(_observations(api)[0].path, scopes)
    react_scope = resolve_license_scope(_observations(exports)[0].path, scopes)
    assert api_scope["prefix"] == "packages/api"
    assert api_scope["package"] == {
        "name": "@embeddedchat/api",
        "version": "0.1.3",
        "license": "ISC",
    }
    assert react_scope["prefix"] == "packages/react"

    policy = DispositionPolicy(
        schema_version=1,
        policy="test",
        rules=(
            DispositionRule(
                rule_id="embedded-license",
                state="blocked_license",
                reason="root license review required",
                upstreams=frozenset({"rocket-embedded-chat"}),
                categories=frozenset(),
                license_statuses=frozenset({"package-metadata-only"}),
            ),
        ),
    )
    assert policy.resolve(_observations(api)[0], api_scope["status"])["state"] == "blocked_license"
    with pytest.raises(DispositionError):
        policy.resolve(_observations(api)[0], "verified-file")


def test_parser_errors_are_never_swallowed_or_downgraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ExtractionError, match="Python 3.12 parser rejected"):
        chattermate._fastapi_routes("backend/app/api/broken.py", "def broken(:\n")
    with pytest.raises(ExtractionError, match="Unbalanced JavaScript"):
        chatwoot._vue_routes("app/javascript/routes.js", "{ path: '/broken' ")

    from scripts.oss_census import python_ast

    def reject_grammar(*_args: object, **_kwargs: object) -> object:
        raise ValueError("unsupported grammar")

    monkeypatch.setattr(python_ast.ast, "parse", reject_grammar)
    with pytest.raises(ExtractionError, match="unsupported grammar"):
        python_ast.parse_python("value = 1", "grammar.py")

    source = tmp_path / "candidate.txt"
    source.write_text("candidate", encoding="utf-8")

    def broken_parser(_path: str, _text: str) -> ParseResult:
        raise ValueError("unexpected parser failure")

    with pytest.raises(ExtractionError, match="unexpected parser failure"):
        text_scope(
            upstream="chatwoot",
            root=tmp_path,
            scope_id="failure-proof",
            extractor="adversarial-test",
            includes=("*.txt",),
            parser=broken_parser,
        )


def test_skip_pin_verification_is_explicit_and_noncanonical() -> None:
    sources = _validate_upstream_lock(
        _load_json(REPO_ROOT / "docs" / "oss-upstreams.lock.json")
    )
    for source in sources:
        receipt = _skipped_pin_receipt(source, ("**/*.py",))
        assert receipt["status"] == "skipped"
        assert receipt["reason"] == "explicit_skip_pin_verification"
        assert receipt["canonical"] is False
        assert receipt["origin"] is None
        assert receipt["checkout"] == {"status": "not_verified"}
        assert receipt["submodules"] == {"status": "not_verified"}
        assert receipt["selectedBlobs"] is None
        assert receipt["selectedBytes"] is None


def test_all_extractors_register_exactly_the_five_pinned_providers() -> None:
    from scripts.oss_census.extractors import EXTRACTORS, SOURCE_PATTERNS
    from scripts.oss_census.model import REQUIRED_UPSTREAMS

    assert tuple(EXTRACTORS) == REQUIRED_UPSTREAMS
    assert tuple(SOURCE_PATTERNS) == REQUIRED_UPSTREAMS
