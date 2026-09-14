import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, CheckCircle2, Gauge, Loader2, MessageCircleMore, Save, ShieldCheck, Sparkles } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { klmApi, type KlmMessagingCadenceSettings } from '@/klm/api';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';

const seconds = (milliseconds: number) => `${(milliseconds / 1000).toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}s`;

function NumericField({ label, value, min, max, suffix, onChange }: { label: string; value: number; min: number; max: number; suffix: string; onChange: (value: number) => void }) {
  return <div className="space-y-1.5"><Label>{label}</Label><div className="relative"><Input aria-label={label} type="number" min={min} max={max} value={value} onChange={(event) => onChange(Math.max(min, Math.min(max, Number(event.target.value) || min)))} className="pr-12" /><span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-2xs text-muted-foreground">{suffix}</span></div></div>;
}

function SafetyToggle({ label, description, checked, disabled = false, onCheckedChange }: { label: string; description: string; checked: boolean; disabled?: boolean; onCheckedChange: (checked: boolean) => void }) {
  return <div className="flex items-start justify-between gap-4 border-b border-border py-3 last:border-b-0"><div className="min-w-0"><p className="text-2xs font-semibold">{label}</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">{description}</p></div><Switch checked={checked} disabled={disabled} onCheckedChange={onCheckedChange} aria-label={label} /></div>;
}

export function MessagingCadenceSettingsPanel() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({ queryKey: ['klm', 'messaging-cadence'], queryFn: klmApi.messagingCadenceSettings });
  const [draft, setDraft] = useState<KlmMessagingCadenceSettings | null>(null);
  const [previewText, setPreviewText] = useState('Olá, revisei os documentos do seu caso e vou explicar o próximo passo com calma.');

  useEffect(() => {
    if (settingsQuery.data) setDraft({ ...settingsQuery.data });
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (payload: KlmMessagingCadenceSettings) => klmApi.updateMessagingCadenceSettings(payload),
    onSuccess: (saved) => {
      setDraft({ ...saved });
      queryClient.setQueryData(['klm', 'messaging-cadence'], saved);
      toast({ title: 'Cadência salva e registrada na auditoria.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível salvar', description: error.message }),
  });
  const previewMutation = useMutation({
    mutationFn: () => klmApi.previewMessagingCadence(previewText.trim()),
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Prévia indisponível', description: error.message }),
  });

  const update = <K extends keyof KlmMessagingCadenceSettings>(key: K, value: KlmMessagingCadenceSettings[K]) => setDraft((current) => current ? { ...current, [key]: value } : current);

  if (settingsQuery.isPending) return <KlmPanel className="flex min-h-44 items-center justify-center gap-2 p-5 text-2xs text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Carregando cadência dos agentes</KlmPanel>;
  if (settingsQuery.isError) return <KlmPanel className="p-5"><p className="text-xs font-semibold text-[#a53e3c]">Não foi possível carregar a cadência.</p><Button variant="outline" size="sm" className="mt-3" onClick={() => void settingsQuery.refetch()}>Tentar novamente</Button></KlmPanel>;
  if (!draft) return null;

  const preview = previewMutation.data;
  return (
    <KlmPanel className="overflow-hidden" data-testid="messaging-cadence-settings">
      <div className="flex flex-col gap-4 border-b border-border p-4 sm:flex-row sm:items-start sm:justify-between sm:p-5">
        <KlmSectionTitle title="Cadência humana dos agentes" description="Atraso proporcional, sinal de digitação, limites e interrupção automática antes de qualquer entrega ao cliente." action={<KlmPill tone={draft.enabled ? 'green' : 'muted'}>{draft.enabled ? 'Ativa' : 'Desativada'}</KlmPill>} />
        <div className="flex shrink-0 items-center gap-2"><span className="text-2xs font-semibold">Aplicar aos bots</span><Switch checked={draft.enabled} onCheckedChange={(checked) => update('enabled', checked)} aria-label="Aplicar cadência aos bots" /></div>
      </div>

      <div className="grid lg:grid-cols-2">
        <section className="border-b border-border p-4 sm:p-5 lg:border-b-0 lg:border-r">
          <div className="mb-4 flex items-center gap-2"><MessageCircleMore className="h-4 w-4 text-[#0057d8]" /><h3 className="text-sm font-semibold">Ritmo e digitação</h3></div>
          <SafetyToggle label="Mostrar “digitando”" description="A mensagem só aparece depois do tempo calculado para leitura e digitação." checked={draft.showTypingIndicator} onCheckedChange={(checked) => update('showTypingIndicator', checked)} />
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <NumericField label="Atraso inicial mínimo" value={draft.preDelayMinMs} min={0} max={60000} suffix="ms" onChange={(value) => update('preDelayMinMs', Math.min(value, draft.preDelayMaxMs))} />
            <NumericField label="Atraso inicial máximo" value={draft.preDelayMaxMs} min={draft.preDelayMinMs} max={60000} suffix="ms" onChange={(value) => update('preDelayMaxMs', value)} />
            <NumericField label="Digitação mínima" value={draft.typingMinMs} min={0} max={120000} suffix="ms" onChange={(value) => update('typingMinMs', Math.min(value, draft.typingMaxMs))} />
            <NumericField label="Digitação máxima" value={draft.typingMaxMs} min={draft.typingMinMs} max={120000} suffix="ms" onChange={(value) => update('typingMaxMs', value)} />
          </div>
          <div className="mt-5 space-y-5">
            <div><div className="mb-2 flex justify-between text-2xs"><Label>Velocidade de digitação</Label><strong>{draft.charactersPerSecond.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} caracteres/s</strong></div><Slider min={1} max={20} step={0.5} value={[draft.charactersPerSecond]} onValueChange={([value]) => update('charactersPerSecond', value)} aria-label="Velocidade de digitação" /></div>
            <div><div className="mb-2 flex justify-between text-2xs"><Label>Variação natural</Label><strong>{draft.jitterPercent}%</strong></div><Slider min={0} max={50} step={1} value={[draft.jitterPercent]} onValueChange={([value]) => update('jitterPercent', value)} aria-label="Variação natural" /></div>
          </div>
        </section>

        <section className="p-4 sm:p-5">
          <div className="mb-4 flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-[#0b7435]" /><h3 className="text-sm font-semibold">Controles de proteção</h3></div>
          <div className="grid gap-3 sm:grid-cols-2">
            <NumericField label="Máximo por minuto" value={draft.maxMessagesPerMinute} min={1} max={60} suffix="msgs" onChange={(value) => update('maxMessagesPerMinute', Math.min(value, draft.maxMessagesPerHour))} />
            <NumericField label="Máximo por hora" value={draft.maxMessagesPerHour} min={draft.maxMessagesPerMinute} max={1000} suffix="msgs" onChange={(value) => update('maxMessagesPerHour', value)} />
            <NumericField label="Pausa após limite" value={draft.cooldownSeconds} min={1} max={86400} suffix="seg" onChange={(value) => update('cooldownSeconds', value)} />
            <NumericField label="Sem resposta" value={draft.maxConsecutiveWithoutReply} min={1} max={20} suffix="msgs" onChange={(value) => update('maxConsecutiveWithoutReply', value)} />
          </div>
          <div className="mt-4">
            <SafetyToggle label="Somente API oficial" description="Invariante da arquitetura nativa. Não há transporte não oficial para mensagens comerciais." checked={draft.officialApiOnly} disabled onCheckedChange={() => undefined} />
            <SafetyToggle label="Parar em bloqueio ou opt-out" description="Impede nova mensagem automatizada quando o contato bloqueia, recusa ou entra em supressão." checked={draft.stopOnBlockOrOptOut} onCheckedChange={(checked) => update('stopOnBlockOrOptOut', checked)} />
            <SafetyToggle label="Exigir consentimento válido" description="Mantém o gate de consentimento obrigatório para o adaptador de entrega externo." checked={draft.requireValidConsent} onCheckedChange={(checked) => update('requireValidConsent', checked)} />
            <SafetyToggle label="Horário silencioso" description={`Adia mensagens das ${String(draft.quietHoursStart).padStart(2, '0')}:00 às ${String(draft.quietHoursEnd).padStart(2, '0')}:00.`} checked={draft.quietHoursEnabled} onCheckedChange={(checked) => update('quietHoursEnabled', checked)} />
          </div>
          {draft.quietHoursEnabled ? <div className="mt-3 grid grid-cols-2 gap-3"><NumericField label="Início" value={draft.quietHoursStart} min={0} max={23} suffix="hora" onChange={(value) => update('quietHoursStart', value)} /><NumericField label="Fim" value={draft.quietHoursEnd} min={0} max={23} suffix="hora" onChange={(value) => update('quietHoursEnd', value)} /></div> : null}
        </section>
      </div>

      <section className="border-t border-border p-4 sm:p-5">
        <div className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-[#0057d8]" /><h3 className="text-sm font-semibold">Prévia sem envio</h3></div>
        <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
          <Textarea value={previewText} onChange={(event) => setPreviewText(event.target.value)} className="min-h-20 text-2xs" aria-label="Mensagem para simular cadência" />
          <div className="flex flex-col justify-between gap-3 border-l-2 border-primary bg-secondary px-3 py-2.5">
            {preview ? <><div className="grid grid-cols-2 gap-2 text-2xs"><span className="text-muted-foreground">Antes de digitar</span><strong className="text-right">{seconds(preview.preDelayMs)}</strong><span className="text-muted-foreground">Digitando</span><strong className="text-right">{seconds(preview.typingDurationMs)}</strong><span className="text-muted-foreground">Total</span><strong className="text-right">{seconds(preview.totalDelayMs)}</strong></div><p className="text-2xs leading-3 text-muted-foreground">{preview.reason}</p></> : <p className="flex items-center gap-2 text-2xs text-muted-foreground"><Bot className="h-3.5 w-3.5" />Simule como o cliente verá o tempo do bot.</p>}
            <Button variant="outline" size="sm" className="h-8 text-2xs" disabled={!previewText.trim() || previewMutation.isPending} onClick={() => previewMutation.mutate()}>{previewMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Gauge className="mr-1.5 h-3.5 w-3.5" />}Simular</Button>
          </div>
        </div>
      </section>

      <div className="flex flex-col gap-3 border-t border-border bg-secondary/50 p-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="flex items-start gap-2 text-2xs leading-4 text-muted-foreground"><CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0b7435]" />O cálculo fica salvo na mensagem e o despachante deve respeitar `notBefore`. Os gates externos continuam fechados localmente.</p>
        <Button className="shrink-0" disabled={saveMutation.isPending} onClick={() => saveMutation.mutate({ ...draft, officialApiOnly: true })}>{saveMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}Salvar política</Button>
      </div>
    </KlmPanel>
  );
}

export default MessagingCadenceSettingsPanel;
