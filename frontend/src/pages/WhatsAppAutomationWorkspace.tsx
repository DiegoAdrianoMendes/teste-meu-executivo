import { useMutation, useQuery } from '@tanstack/react-query';

import { SectionHeader } from '@/components/common/SectionHeader';
import { WhatsAppAutomationRulesPanel } from '@/components/admin/WhatsAppAutomationRulesPanel';
import { useToast } from '@/hooks/use-toast';
import { fetchWhatsAppAutomationRules, fetchWorkspaceOperations, saveWhatsAppAutomationRules } from '@/services/admin';

export default function WhatsAppAutomationWorkspace() {
  const { toast } = useToast();
  const rulesQuery = useQuery({
    queryKey: ['whatsapp-automation-workspace'],
    queryFn: fetchWhatsAppAutomationRules,
  });
  const operationsQuery = useQuery({
    queryKey: ['workspace-ops-flow-options'],
    queryFn: fetchWorkspaceOperations,
  });
  const mutation = useMutation({
    mutationFn: saveWhatsAppAutomationRules,
    onSuccess: () => toast({ title: 'Automações do WhatsApp salvas.' }),
  });

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Automações do WhatsApp"
        description="Canal comercial com regras próprias de inbox, follow-up e escalada humana, separado das automações sociais de Instagram e Messenger."
      />
      <WhatsAppAutomationRulesPanel
        value={rulesQuery.data ?? null}
        loading={rulesQuery.isLoading}
        flowOptions={operationsQuery.data?.areaActivations.flatMap((area) => area.flowOptions) ?? []}
        onSave={async (value) => { await mutation.mutateAsync(value); }}
      />
    </div>
  );
}
