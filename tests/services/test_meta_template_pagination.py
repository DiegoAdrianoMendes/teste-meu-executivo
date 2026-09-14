from unittest.mock import AsyncMock

import pytest

from app.services.whatsapp.meta_cloud_client import MetaCloudClient


@pytest.mark.asyncio
async def test_get_message_templates_reads_every_provider_page(monkeypatch):
    client = MetaCloudClient()
    client._request = AsyncMock(
        side_effect=[
            {
                "data": [{"id": "1", "name": "first"}],
                "paging": {"next": "next-page", "cursors": {"after": "cursor-1"}},
            },
            {"data": [{"id": "2", "name": "second"}]},
        ]
    )

    payload = await client.get_message_templates("waba-1", "token")

    assert [item["id"] for item in payload["data"]] == ["1", "2"]
    assert client._request.await_count == 2
    assert client._request.await_args_list[1].kwargs["params"]["after"] == "cursor-1"
