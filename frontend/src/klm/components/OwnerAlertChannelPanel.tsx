import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { klmApi } from '@/klm/api';
import { KlmPanel, KlmPill, KlmSectionTitle } from './Primitives';
import { META_PHONE_HELP, normalizeWhatsAppPhone } from '@/lib/whatsapp-phone';

export function OwnerAlertChannelPanel() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ['workspace', 'owner-alert-channel'], queryFn: klmApi.workspaceOwnerAlertChannel, retry: false });
  const [phone, setPhone] = useState('');
  const normalizedPhone = normalizeWhatsAppPhone(phone);
  const [isDirty, setIsDirty] = useState(false);
  useEffect(() => { if (query.data && !isDirty) setPhone(query.data.ownerWhatsappPhone ?? ''); }, [isDirty, query.data]);
  const save = useMutation({
    mutationFn: () => klmApi.updateWorkspaceOwnerAlertChannel(phone.trim() ? normalizedPhone : null),
    onSuccess: (value) => { setIsDirty(false); queryClient.setQueryData(['workspace', 'owner-alert-channel'], value); },
  });
  return <KlmPanel className="p-4 sm:p-5" data-testid="owner-alert-channel">
    <div className="flex flex-wrap items-start justify-between gap-3"><KlmSectionTitle title="Alertas ao dono" description="Toda chamada humana aparece no sistema. O telefone recebe WhatsApp somente quando o provedor confirma a entrega." /><KlmPill tone={query.data?.configured ? 'green' : query.isError ? 'red' : 'gold'}>{query.isPending ? 'carregando' : query.isError ? 'indisponível' : query.data?.configured ? 'telefone configurado' : 'telefone não configurado'}</KlmPill></div>
    <form className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-end" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
      <label className="min-w-0 flex-1 text-xs">WhatsApp do dono<Input aria-label="WhatsApp do dono para alertas" inputMode="tel" autoComplete="tel" value={phone} onChange={(event) => { setIsDirty(true); setPhone(event.target.value); }} onBlur={() => normalizedPhone && setPhone(normalizedPhone)} placeholder="5561999999999" aria-describedby="owner-alert-phone-help" aria-invalid={Boolean(phone && !normalizedPhone)} className="mt-1 min-h-11" /></label>
      <Button type="submit" className="min-h-11" disabled={query.isPending || save.isPending || Boolean(phone && !normalizedPhone)}>{save.isPending ? 'Salvando…' : 'Salvar canal'}</Button>
    </form>
    <p id="owner-alert-phone-help" className="mt-2 text-xs text-muted-foreground">{META_PHONE_HELP} Números brasileiros com DDD recebem o +55 automaticamente.</p>
    {save.isSuccess && <p role="status" className="mt-2 text-xs text-emerald-700">Canal atualizado. Entrega externa continuará identificada como pendente até o recibo oficial.</p>}
    {save.isError && <p role="alert" className="mt-2 text-xs text-destructive">Não foi possível atualizar. Somente owner ou admin pode gerir este canal.</p>}
  </KlmPanel>;
}
