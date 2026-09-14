import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, Link2, Smartphone, Webhook } from 'lucide-react';

import { SectionHeader } from '@/components/common/SectionHeader';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { MetaWebhookOperationsPanel } from '@/components/whatsapp/MetaWebhookOperationsPanel';
import { fetchWhatsAppConnections, fetchWhatsAppMetaOperations } from '@/services/whatsapp';

export default function WhatsAppConnections() {
  const query = useQuery({
    queryKey: ['whatsapp-connections-page'],
    queryFn: fetchWhatsAppConnections,
  });

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Conexões do WhatsApp"
        description="Superfície própria para operação do número oficial do workspace, separada dos canais sociais da suíte Meta."
      />
      <div className="grid gap-4 lg:grid-cols-2">
        {(query.data ?? []).map((connection) => (
          <Card key={connection.id}>
            <CardHeader>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-base">{connection.displayName || connection.displayPhoneNumber || connection.phoneNumber || 'Número oficial'}</CardTitle>
                  <CardDescription>{connection.connectionType}</CardDescription>
                </div>
                <Badge>{connection.status}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Smartphone className="h-4 w-4" />
                {connection.displayPhoneNumber || connection.phoneNumber || 'Sem telefone visível'}
              </div>
              <div className="flex items-center gap-2 text-muted-foreground">
                <Link2 className="h-4 w-4" />
                WABA: {connection.wabaId || 'não informado'}
              </div>
              <div className="flex items-center gap-2 text-muted-foreground">
                <Webhook className="h-4 w-4" />
                {connection.webhookRegistered ? 'Webhook registrado' : 'Webhook pendente'}
              </div>
              {connection.isDefault && (
                <div className="flex items-center gap-2 text-green-700">
                  <CheckCircle2 className="h-4 w-4" />
                  Linha padrão do workspace
                </div>
              )}
            </CardContent>
          </Card>
        ))}
        {(query.data ?? []).length === 0 && (
          <Card>
            <CardContent className="py-10 text-sm text-muted-foreground">
              Nenhuma conexão encontrada. Continue usando o onboarding e o painel oficial de WhatsApps para ativar a primeira linha.
            </CardContent>
          </Card>
        )}
      </div>
      <MetaWebhookOperationsPanel
        queryKey={['whatsapp-meta-operations']}
        queryFn={fetchWhatsAppMetaOperations}
      />
    </div>
  );
}
