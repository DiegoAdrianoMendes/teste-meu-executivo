import { Link } from 'react-router-dom';

import { MetaWebhookOperationsPanel } from '@/components/whatsapp/MetaWebhookOperationsPanel';
import { Button } from '@/components/ui/button';
import {
  fetchWhatsAppMetaOperations,
  previewWhatsAppMetaSubscriptions,
  reconcileWhatsAppMetaSubscriptions,
  retryWhatsAppMetaReceipt,
  updateWhatsAppMetaAutomationPolicy,
} from '@/services/whatsapp';
import { useAppStore } from '@/store/app-context';

export default function MetaWebhooksPage() {
  const { can } = useAppStore();
  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">WhatsApp oficial</p>
          <h1 className="text-2xl font-semibold">Webhooks e operações Meta</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Estado operacional dos 33 campos: código disponível, assinatura desejada, resposta observada da Meta e último processamento são sinais separados.
          </p>
        </div>
        <Button asChild variant="outline"><Link to="/app/conexoes">Voltar para conexões</Link></Button>
      </div>
      <MetaWebhookOperationsPanel
        queryKey={['whatsapp-meta-operations-control-plane']}
        queryFn={fetchWhatsAppMetaOperations}
        canManageSubscriptions={can('integrations', 'manage')}
        canManagePolicies={can('automations', 'manage')}
        previewSubscriptions={previewWhatsAppMetaSubscriptions}
        reconcileSubscriptions={reconcileWhatsAppMetaSubscriptions}
        retryReceipt={retryWhatsAppMetaReceipt}
        updatePolicy={updateWhatsAppMetaAutomationPolicy}
      />
    </div>
  );
}
