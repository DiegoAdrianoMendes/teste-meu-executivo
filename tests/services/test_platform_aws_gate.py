from unittest.mock import Mock

import boto3
import pytest

from app.core.config import settings
from app.services.platform_service import PlatformService


@pytest.mark.parametrize(
    ("runtime_mode", "online_approved", "external_enabled", "aws_enabled"),
    [
        ("local_simulation", True, True, True),
        ("production", False, True, True),
        ("production", True, False, True),
        ("production", True, True, False),
    ],
)
async def test_cost_explorer_denied_matrix_never_creates_boto3_client(
    monkeypatch,
    runtime_mode,
    online_approved,
    external_enabled,
    aws_enabled,
):
    boto3_client = Mock(side_effect=AssertionError("boto3.client não deveria ser chamado"))
    monkeypatch.setattr(boto3, "client", boto3_client)
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", runtime_mode)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", online_approved)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", external_enabled)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", aws_enabled)
    service = PlatformService(db=None)

    payload = await service.get_aws_cost_estimate()

    assert payload["source"] == "estimate"
    with pytest.raises(RuntimeError, match="gate externo"):
        service._fetch_aws_cost_real()
    boto3_client.assert_not_called()


def test_cost_explorer_uses_mocked_boto3_only_when_every_gate_is_open(monkeypatch):
    cost_explorer = Mock()
    cost_explorer.get_cost_and_usage.return_value = {
        "ResultsByTime": [
            {
                "Groups": [
                    {
                        "Keys": ["Amazon Simple Storage Service"],
                        "Metrics": {"UnblendedCost": {"Amount": "1.25"}},
                    }
                ]
            }
        ]
    }
    boto3_client = Mock(return_value=cost_explorer)
    monkeypatch.setattr(boto3, "client", boto3_client)
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", True)

    payload = PlatformService(db=None)._fetch_aws_cost_real()

    assert payload["source"] == "aws_cost_explorer"
    assert payload["total_usd"] == 1.25
    boto3_client.assert_called_once_with("ce", region_name="us-east-1")
    cost_explorer.get_cost_and_usage.assert_called_once()
