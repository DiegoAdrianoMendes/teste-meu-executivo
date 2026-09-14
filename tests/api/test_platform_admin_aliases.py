"""S20: os KPIs do painel da plataforma saem com o alias que o frontend lê."""

from app.schemas.platform_admin import PlatformWorkspaceRead


def test_kpi_aliases_use_lowercase_digit_suffix():
    props = PlatformWorkspaceRead.model_json_schema(by_alias=True)["properties"]
    assert "matterCount30d" in props and "aiRuns30d" in props
    assert "matterCount30D" not in props and "aiRuns30D" not in props


def test_construction_by_field_name_still_works():
    fields = PlatformWorkspaceRead.model_fields
    assert fields["matter_count_30d"].alias == "matterCount30d"
    assert fields["ai_runs_30d"].alias == "aiRuns30d"
