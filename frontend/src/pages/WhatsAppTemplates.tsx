import { useQuery, useMutation } from '@tanstack/react-query';

import { SectionHeader } from '@/components/common/SectionHeader';
import { MessageTemplatesPanel } from '@/components/admin/MessageTemplatesPanel';
import { useToast } from '@/hooks/use-toast';
import { fetchMessageTemplates, saveMessageTemplates } from '@/services/admin';

export default function WhatsAppTemplates() {
  const { toast } = useToast();
  const query = useQuery({
    queryKey: ['whatsapp-template-settings'],
    queryFn: fetchMessageTemplates,
  });
  const mutation = useMutation({
    mutationFn: saveMessageTemplates,
    onSuccess: () => {
      toast({ title: 'Biblioteca do WhatsApp salva.' });
    },
  });

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Respostas rápidas internas"
        description="Mensagens padrão, propostas e contratos da plataforma. Diferente dos templates oficiais aprovados pela Meta (Templates WhatsApp Meta), estes textos só podem ser usados dentro da janela de 24h de uma conversa já aberta."
      />
      <MessageTemplatesPanel
        value={query.data ?? null}
        loading={query.isLoading}
        onSave={async (value) => { await mutation.mutateAsync(value); }}
      />
    </div>
  );
}
