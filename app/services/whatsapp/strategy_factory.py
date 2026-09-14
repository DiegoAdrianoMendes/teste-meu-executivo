from __future__ import annotations

from app.models.whatsapp import ConnectionType, WhatsAppConnection
from app.services.whatsapp.meta_cloud_client import MetaCloudClient
from app.services.whatsapp.qr_gateway_client import QrGatewayClient
from app.services.whatsapp.strategies.meta_cloud import MetaCloudStrategy
from app.services.whatsapp.strategies.qr_gateway import QrGatewayStrategy


class WhatsAppStrategyFactory:
    def __init__(
        self,
        *,
        meta_client: MetaCloudClient,
        qr_client: QrGatewayClient,
        access_token_resolver,
        instance_token_resolver,
    ) -> None:
        self._meta_client = meta_client
        self._qr_client = qr_client
        self._access_token_resolver = access_token_resolver
        self._instance_token_resolver = instance_token_resolver

    def build(self, connection: WhatsAppConnection):
        if connection.connection_type == ConnectionType.QR_CODE:
            return QrGatewayStrategy(self._qr_client, self._instance_token_resolver)
        return MetaCloudStrategy(self._meta_client, self._access_token_resolver)
