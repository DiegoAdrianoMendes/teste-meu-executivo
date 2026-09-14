import { useMemo, useState, useEffect, useRef, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  Banknote,
  CalendarClock,
  ChevronRight,
  CircleAlert,
  ClipboardCheck,
  Database,
  Download,
  ExternalLink,
  FileCheck2,
  FileSignature,
  FileText,
  FolderOpen,
  History,
  LoaderCircle,
  Mail,
  PencilLine,
  Phone,
  Pill as MedicationIcon,
  Scale,
  ShieldCheck,
  Sparkles,
  UserRoundCheck,
  Workflow,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { klmApi, type KlmMatterDetail } from '@/klm/api';
import { KlmPill, KlmProgress } from '@/klm/components/Primitives';
import { Case360QuickEditor } from '@/klm/components/Case360QuickEditor';
import { CaseFinanceSection } from '@/klm/components/CaseFinanceSection';
import {
  InlineChoiceField,
  InlineCompoundChoiceField,
  InlineTagsField,
  InlineTextField,
  type InlineChoiceOption,
} from '@/klm/components/Case360InlineFields';
import { ConversationAuditPanel } from '@/klm/components/ConversationAuditPanel';
import { MatterFlowJourney } from '@/klm/components/MatterFlowJourney';
import { SdrQualificationPanel } from '@/klm/components/SdrQualificationPanel';
import { formatBRL } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import { useAppStore } from '@/store/app-context';

type ContactSummary = {
  name: string;
  score: number;
  subtheme: string;
  object: string;
  sensitiveDataHidden?: boolean;
};

type ConversationCasePanelProps = {
  threadId?: string | null;
  matterId?: string | null;
  contact: ContactSummary;
  internalEntries: Array<[string, string]>;
  onTool: (tool: string) => void;
};

function Section({
  title,
  icon: Icon,
  action,
  children,
}: {
  title: string;
  icon: typeof FileText;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="border-t border-border py-4 first:border-t-0 first:pt-0">
      <div className="mb-3 flex min-w-0 items-center gap-2">
        <Icon className="h-3.5 w-3.5 shrink-0 text-[#0057d8] dark:text-primary" />
        <h4 className="min-w-0 flex-1 truncate text-2xs font-semibold">{title}</h4>
        {action}
      </div>
      {children}
    </section>
  );
}

function InlineAction({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="inline-flex items-center gap-1 text-2xs font-semibold text-[#0057d8] hover:underline dark:text-primary">
      {children}<ChevronRight className="h-3 w-3" />
    </button>
  );
}

function Field({ label, value, danger = false }: { label: string; value: ReactNode; danger?: boolean }) {
  return (
    <div className="grid grid-cols-[88px_minmax(0,1fr)] gap-2 py-1.5 text-2xs">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn('min-w-0 break-words font-medium', danger && 'text-[#a53e3c] dark:text-red-400')}>{value || '—'}</span>
    </div>
  );
}

function statusTone(status: string): 'green' | 'gold' | 'red' | 'blue' | 'muted' {
  const value = status.toLocaleLowerCase('pt-BR');
  if (/(completo|assinado|pago|conclu|active|scheduled)/.test(value)) return 'green';
  if (/(erro|recus|venc|bloque|cancel|pendente)/.test(value)) return 'red';
  if (/(parcial|aguard|rascunho|pending)/.test(value)) return 'gold';
  if (/(index|ocr|enviado)/.test(value)) return 'blue';
  return 'muted';
}

const STAGES: InlineChoiceOption[] = [
  ['novo_lead', 'Novo lead'], ['triagem_sdr', 'Triagem SDR'], ['documentos', 'Documentos'],
  ['reuniao', 'Reunião'], ['proposta', 'Proposta'], ['contrato', 'Contrato'],
  ['pagamento', 'Pagamento'], ['ganho', 'Ganho'], ['perdido', 'Perdido'],
  ['entrada', 'Entrada'], ['qualificacao', 'Qualificação'], ['proposta_fechamento', 'Proposta e fechamento'],
  ['coleta_dados', 'Coleta de dados'], ['triagem_juridica', 'Triagem jurídica'],
  ['pesquisa_validacao', 'Pesquisa e validação'], ['redacao_peca', 'Redação da peça'],
  ['aprovacao_pendente', 'Aprovação pendente'], ['pronto_protocolo', 'Pronto para protocolo'],
  ['migrar_sistema', 'Migrar sistema'], ['assinado', 'Assinado'],
].map(([value, label]) => ({ value, label }));

const MICRO_STATUSES: InlineChoiceOption[] = [
  'lead_novo', 'sem_resposta', 'triagem_incompleta', 'qualificado', 'proposta_enviada',
  'assinatura_pendente', 'pagamento_pendente', 'aguardando_docs', 'docs_parciais',
  'docs_invalidos', 'docs_completos', 'tese_preliminar', 'risco_probatorio',
  'pesquisa_em_andamento', 'validacao_aprovada', 'validacao_reprovada',
  'minuta_em_elaboracao', 'revisao_interna', 'ajustes_solicitados', 'pdf_gerado',
  'checklist_final', 'migracao_pendente', 'contrato_assinado', 'reuniao_agendada',
  'reuniao_confirmada', 'reuniao_realizada', 'no_show', 'proposta_em_revisao', 'contrato_enviado',
].map((value) => ({ value, label: value.replaceAll('_', ' ') }));

const PRIORITIES: InlineChoiceOption[] = ['alta', 'media', 'baixa'].map((value) => ({ value, label: value === 'media' ? 'Média' : `${value[0].toUpperCase()}${value.slice(1)}` }));
const URGENCIES: InlineChoiceOption[] = ['urgente', 'normal', 'baixa'].map((value) => ({ value, label: `${value[0].toUpperCase()}${value.slice(1)}` }));
const SOURCES: InlineChoiceOption[] = [
  ['google_ads', 'Google Ads'], ['meta_ads', 'Meta Ads'], ['instagram', 'Instagram'],
  ['tiktok_ads', 'TikTok Ads'], ['indicacao', 'Indicação'], ['organico', 'Orgânico'],
  ['whatsapp', 'WhatsApp direto'], ['email', 'E-mail'], ['site', 'Site'],
].map(([value, label]) => ({ value, label }));
const CHANNELS: InlineChoiceOption[] = [
  ['whatsapp', 'WhatsApp'], ['instagram', 'Instagram'], ['facebook', 'Facebook'],
  ['messenger', 'Messenger'], ['tiktok', 'TikTok'], ['email', 'E-mail'],
  ['form', 'Formulário'], ['webchat', 'Chat do site'], ['google_search', 'Busca Google'],
].map(([value, label]) => ({ value, label }));
const DEMANDS: InlineChoiceOption[] = [
  ['negativa_plano_saude', 'Negativa de plano de saúde'], ['cobertura_medicamento', 'Cobertura de medicamento'],
  ['home_care', 'Home care'], ['cirurgia', 'Cirurgia'], ['exame', 'Exame'], ['tratamento', 'Tratamento'],
].map(([value, label]) => ({ value, label }));
const SUBTHEMES: InlineChoiceOption[] = [
  ['medicamento_alto_custo', 'Medicamento de alto custo'], ['tratamento', 'Tratamento'],
  ['home_care', 'Home care'], ['cirurgia', 'Cirurgia'], ['exame', 'Exame'],
  ['opme', 'OPME'], ['terapia', 'Terapia'],
].map(([value, label]) => ({ value, label }));
const PRACTICE_AREAS: InlineChoiceOption[] = [
  ['saude_suplementar', 'Saúde'], ['erro_medico', 'Erro médico'], ['bancario', 'Bancário'],
  ['trabalhista', 'Trabalhista'], ['previdenciario', 'Previdenciário'], ['civel', 'Cível'],
  ['consumidor', 'Consumidor'], ['digital', 'Digital'], ['propriedade_intelectual', 'Propriedade intelectual'],
  ['seguros', 'Seguros'], ['tributario', 'Tributário'], ['empresarial', 'Empresarial'],
  ['familia', 'Família'], ['imobiliario', 'Imobiliário'],
].map(([value, label]) => ({ value, label }));

function MatterContent({
  threadId,
  matter,
  contact,
  internalEntries,
  onTool,
}: {
  threadId?: string | null;
  matter: KlmMatterDetail;
  contact: ContactSummary;
  internalEntries: Array<[string, string]>;
  onTool: (tool: string) => void;
}) {
  const { can } = useAppStore();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [savingInlineField, setSavingInlineField] = useState<string | null>(null);
  const canViewLeadData = can('lead_data', 'view');
  const canViewFinance = can('finance', 'view');
  const canManageFinance = can('finance', 'manage');
  const canSendMessages = can('mensagens', 'send_message');
  const meetingsQuery = useQuery({
    queryKey: ['klm', 'meetings', matter.id],
    queryFn: () => klmApi.meetingsForMatter(matter.id),
  });
  const bookingLinksQuery = useQuery({
    queryKey: ['klm', 'booking-links', matter.id],
    queryFn: () => klmApi.bookingLinksForMatter(matter.id),
  });
  const financeQuery = useQuery({
    queryKey: ['klm', 'finance-entries', matter.id],
    queryFn: () => klmApi.financeEntries(matter.id),
    enabled: canViewFinance,
  });
  const downloadMutation = useMutation({
    mutationFn: async (documentId: string) => ({
      documentId,
      result: await klmApi.documentDownloadUrl(documentId),
    }),
    onMutate: setDownloadingId,
    onSuccess: ({ result }) => window.open(result.downloadUrl, '_blank', 'noopener,noreferrer'),
    onError: (error: Error) => toast({
      variant: 'destructive',
      title: 'Arquivo indisponível',
      description: error.message,
    }),
    onSettled: () => setDownloadingId(null),
  });

  const upcomingMeeting = useMemo(() => (meetingsQuery.data ?? [])
    .filter((meeting) => meeting.status === 'scheduled' && new Date(meeting.startsAt).getTime() >= Date.now())
    .sort((left, right) => left.startsAt.localeCompare(right.startsAt))[0], [meetingsQuery.data]);
  const contractDocuments = matter.documents.filter((document) => /contrato|procura[cç][aã]o/.test(`${document.type ?? ''} ${document.name}`.toLocaleLowerCase('pt-BR')));
  const signedContract = contractDocuments.find((document) => /assinado|signed|completo/.test(document.status.toLocaleLowerCase('pt-BR')));
  const contractShortcutLabel = signedContract ? 'Contrato assinado' : contractDocuments.length ? 'Contrato pré-preenchido' : 'Preparar contrato';
  const activeLinks = (bookingLinksQuery.data ?? []).filter((link) => link.isActive);
  const financeTotal = (financeQuery.data ?? []).reduce((sum, entry) => sum + entry.amount, 0);
  const auditEntries = [
    ...internalEntries.map(([agent, action], index) => ({ id: `message-${index}`, agent, action })),
    ...(matter.agentRuns ?? []).slice(0, 6).map((run) => ({ id: run.id, agent: run.agentKey ?? 'Agente', action: `execução ${run.status}` })),
  ];
  const canEditCase = can('kanban', 'edit') && can('lead_data', 'edit') && !matter.client.sensitiveDataHidden && !contact.sensitiveDataHidden;
  const saveInline = async (field: string, action: () => Promise<KlmMatterDetail>) => {
    setSavingInlineField(field);
    try {
      await queryClient.cancelQueries({ queryKey: ['klm', 'matter', matter.id] });
      const updated = await action();
      queryClient.setQueryData(['klm', 'matter', matter.id], updated);
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] }),
        queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] }),
        queryClient.invalidateQueries({ queryKey: ['klm', 'kpis'] }),
      ]);
      toast({
        title: 'Dado atualizado',
        description: `Revisão manual v${updated.manualContextRevision} disponível para os agentes.`,
      });
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Não foi possível salvar',
        description: error instanceof Error ? error.message : 'Revise o valor e tente novamente.',
      });
      throw error;
    } finally {
      setSavingInlineField(null);
    }
  };
  const saveMatter = (field: string, payload: Parameters<typeof klmApi.updateCase360>[1]['matter']) => saveInline(
    field,
    () => klmApi.updateCase360(matter.id, { matter: payload }),
  );
  const saveClient = (field: string, payload: NonNullable<Parameters<typeof klmApi.updateCase360>[1]['client']>) => saveInline(
    field,
    () => klmApi.updateCase360(matter.id, { client: payload, matter: {} }),
  );
  const saveAttribution = (field: string, payload: NonNullable<Parameters<typeof klmApi.updateCase360>[1]['attribution']>) => saveInline(
    field,
    () => klmApi.updateCase360(matter.id, { attribution: payload, matter: {} }),
  );
  const savedMatter = (updated: KlmMatterDetail) => {
    queryClient.setQueryData(['klm', 'matter', matter.id], updated);
    void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
    void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
  };

  return (
    <div className="min-h-0 overflow-y-auto p-4 scrollbar-thin" data-testid="conversation-case-360">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-2xs font-semibold uppercase text-[#0057d8] dark:text-primary">Dossiê vinculado</p>
          <h3 className="mt-1 truncate text-base font-medium">Caso 360</h3>
          <p className="mt-1 truncate text-2xs text-muted-foreground">{matter.client.name}</p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Case360QuickEditor
            matter={matter}
            onSaved={savedMatter}
          />
          <div className="w-[116px]" title="Potencial comercial do lead, de 0 a 100. Pode ser ajustado manualmente conforme as evidências do caso.">
            <InlineTextField
              label="Potencial do lead"
              value={matter.leadScore}
              type="number"
              disabled={!canEditCase}
              saving={savingInlineField === 'leadScore'}
              display={<KlmPill tone={matter.leadScore >= 80 ? 'green' : matter.leadScore >= 60 ? 'gold' : 'muted'}><PencilLine className="mr-1 h-3 w-3" />Potencial {matter.leadScore}/100</KlmPill>}
              nullable={false}
              validate={(value) => Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) <= 100 ? null : 'Use um potencial de 0 a 100.'}
              onSave={(value) => saveMatter('leadScore', { leadScore: Number(value) })}
            />
          </div>
        </div>
      </div>
      <div className="mt-3"><KlmProgress value={matter.leadScore} tone={matter.leadScore >= 80 ? 'green' : 'gold'} /></div>

      {(() => {
        const area = matter.legalAreaKey ?? matter.practiceArea;
        const product = matter.legalProductCustom ?? matter.legalProductKey ?? matter.legalObjectName ?? matter.healthObjectName;
        const topics = [area, matter.demandType, matter.healthSubthemeCustom ?? matter.healthSubtheme, product]
          .filter((value): value is string => Boolean(value?.trim()))
          .map((value) => value.replaceAll('_', ' '));
        const summary = matter.executiveSummary?.trim() || matter.conversationInsight?.summary?.trim();
        const reportedHistory = (matter.conversations ?? [])
          .filter((entry) => entry.message.trim())
          .slice(-3)
          .reverse();

        return (
          <section className="mt-4 rounded-md border border-[#d9e6f8] bg-[#f7fbff] p-3 dark:border-border dark:bg-muted/20" aria-label="Resumo factual do cliente e do caso">
            <div className="flex items-center gap-2">
              <History className="h-3.5 w-3.5 shrink-0 text-[#0057d8] dark:text-primary" />
              <h4 className="text-xs font-semibold">Resumo factual do cliente e do caso</h4>
            </div>
            <p className="mt-2 text-2xs leading-4 text-foreground">{summary || 'O resumo factual ainda não foi preenchido. Registre aqui o que aconteceu com a pessoa, sua necessidade e os fatos relevantes do caso.'}</p>
            <div className="mt-3 grid gap-2 text-2xs sm:grid-cols-2">
              <div className="min-w-0">
                <p className="font-semibold text-[#0057d8] dark:text-primary">Histórico relatado</p>
                {reportedHistory.length ? (
                  <ul className="mt-1 space-y-1 text-muted-foreground">
                    {reportedHistory.map((entry) => <li key={entry.id} className="break-words leading-4">{entry.message}</li>)}
                  </ul>
                ) : <p className="mt-1 text-muted-foreground">Ainda não há fatos relatados em texto.</p>}
              </div>
              <div className="min-w-0">
                <p className="font-semibold text-[#0057d8] dark:text-primary">Tópicos do caso</p>
                {topics.length ? <div className="mt-1 flex flex-wrap gap-1">{topics.map((topic) => <KlmPill key={topic} tone="blue">{topic}</KlmPill>)}</div> : <p className="mt-1 text-muted-foreground">Área e produto ainda não informados.</p>}
              </div>
            </div>
          </section>
        );
      })()}

      <div className="mt-3 grid grid-cols-3 gap-1.5" aria-label="Acesso rápido ao dossiê">
        <Button type="button" variant="outline" size="sm" className="h-auto min-h-8 min-w-0 whitespace-normal px-2 py-1.5 text-2xs leading-3" onClick={() => onTool('Cliente 360')}>
          <History className="mr-1.5 h-3.5 w-3.5 shrink-0" />Resumo e histórico
        </Button>
        <Button type="button" variant="outline" size="sm" className="h-auto min-h-8 min-w-0 whitespace-normal px-2 py-1.5 text-2xs leading-3" onClick={() => onTool('RAG contextual')}>
          <FolderOpen className="mr-1.5 h-3.5 w-3.5 shrink-0" />Documentos ({matter.documents.length})
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-auto min-h-8 min-w-0 whitespace-normal px-2 py-1.5 text-2xs leading-3"
          onClick={() => signedContract ? downloadMutation.mutate(signedContract.id) : onTool('Contratos')}
          disabled={Boolean(signedContract) && (!canViewLeadData || downloadMutation.isPending)}
        >
          <FileSignature className="mr-1.5 h-3.5 w-3.5 shrink-0" />{contractShortcutLabel}
        </Button>
      </div>

      <Section title="Dados do cliente e card" icon={ClipboardCheck} action={<InlineAction onClick={() => onTool('Cliente 360')}>Abrir cliente 360</InlineAction>}>
        {matter.client.sensitiveDataHidden || contact.sensitiveDataHidden ? (
          <div className="mb-2 flex items-start gap-2 border-l-2 border-primary pl-2 text-2xs leading-4 text-muted-foreground">
            <ShieldCheck className="mt-0.5 h-3 w-3 shrink-0" />Dados pessoais ocultos pelas permissões do administrador.
          </div>
        ) : null}
        <Field label="Nome" value={<InlineTextField label="Nome" value={matter.client.name} disabled={!canEditCase} saving={savingInlineField === 'client.name'} nullable={false} validate={(value) => value.length >= 2 ? null : 'Informe o nome do cliente.'} onSave={(value) => saveClient('client.name', { name: value ?? '' })} />} />
        <Field label="WhatsApp" value={<InlineTextField label="WhatsApp" value={matter.client.phone} type="tel" disabled={!canEditCase} saving={savingInlineField === 'client.phone'} display={matter.client.phone ? <span className="inline-flex items-center gap-1"><Phone className="h-3 w-3" />{matter.client.phone}</span> : '—'} validate={(value) => !value || value.replace(/\D/g, '').length >= 10 ? null : 'Informe DDD e número.'} onSave={(value) => saveClient('client.phone', { phone: value })} />} />
        <Field label="E-mail" value={<InlineTextField label="E-mail" value={matter.client.email} type="email" disabled={!canEditCase} saving={savingInlineField === 'client.email'} display={matter.client.email ? <span className="inline-flex items-center gap-1"><Mail className="h-3 w-3" />{matter.client.email}</span> : '—'} validate={(value) => !value || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) ? null : 'Informe um e-mail válido.'} onSave={(value) => saveClient('client.email', { email: value })} />} />
        <Field label="Área" value={<InlineChoiceField label="Área jurídica" value={matter.legalAreaKey ?? matter.practiceArea} options={PRACTICE_AREAS} disabled={!canEditCase} saving={savingInlineField === 'legalAreaKey'} onSave={(value, custom) => { const area = (value === '__custom__' ? custom : value) ?? null; return saveMatter('legalAreaKey', { practiceArea: area, legalAreaKey: area }); }} />} />
        <Field label="Subárea" value={<InlineChoiceField label="Subárea" value={matter.healthSubtheme} customValue={matter.healthSubthemeCustom ?? contact.subtheme} options={SUBTHEMES} disabled={!canEditCase} saving={savingInlineField === 'healthSubtheme'} onSave={(value, custom) => saveMatter('healthSubtheme', { healthSubtheme: value === '__custom__' ? 'outro' : value, healthSubthemeCustom: value === '__custom__' ? custom : null })} />} />
        <Field label="Produto" value={<InlineTextField label="Produto jurídico" value={matter.legalProductCustom ?? matter.legalObjectName ?? matter.healthObjectName ?? contact.object} disabled={!canEditCase} saving={savingInlineField === 'legalProductCustom'} onSave={(value) => saveMatter('legalProductCustom', { legalProductCustom: value, legalObjectName: value, healthObjectName: value })} />} />
        <Field label="Etapa" value={<InlineCompoundChoiceField label="Etapa e microstatus" firstValue={matter.columnId} secondValue={matter.microStatus} firstOptions={STAGES} secondOptions={MICRO_STATUSES} display={`${matter.columnId.replaceAll('_', ' ')} · ${matter.microStatus.replaceAll('_', ' ')}`} disabled={!canEditCase} saving={savingInlineField === 'stage'} onSave={(columnId, microStatus) => saveInline('stage', () => klmApi.moveMatter(matter.id, { columnId, microStatus }))} />} />
        <Field label="Prioridade" value={<InlineCompoundChoiceField label="Prioridade e urgência" firstValue={matter.priority ?? 'media'} secondValue={matter.urgency ?? 'normal'} firstOptions={PRIORITIES} secondOptions={URGENCIES} display={`${matter.priority ?? '—'} · ${matter.urgency ?? '—'}`} disabled={!canEditCase} saving={savingInlineField === 'priority'} onSave={(priority, urgency) => saveMatter('priority', { priority, urgency })} />} danger={matter.urgency === 'urgente'} />
        <Field label="Próxima ação" value={<InlineTextField label="Próxima ação" value={matter.nextAction} disabled={!canEditCase} saving={savingInlineField === 'nextAction'} onSave={(value) => saveMatter('nextAction', { nextAction: value })} />} />
        <Field label="Prazo" value={<InlineTextField label="Prazo" value={matter.nextDeadline ? matter.nextDeadline.slice(0, 16) : null} type="datetime-local" disabled={!canEditCase} saving={savingInlineField === 'nextDeadline'} display={matter.nextDeadline ? new Date(matter.nextDeadline).toLocaleString('pt-BR') : '—'} onSave={(value) => saveMatter('nextDeadline', { nextDeadline: value ? new Date(value).toISOString() : null })} />} danger={Boolean(matter.nextDeadline)} />
        <Field label="Revisão humana" value={matter.manualContextRevision ? `v${matter.manualContextRevision} · ${matter.manualContextUpdatedAt ? new Date(matter.manualContextUpdatedAt).toLocaleString('pt-BR') : 'registrada'}` : 'sem correção manual'} />
      </Section>

      <Section title="Origem e atribuição" icon={Activity} action={<InlineAction onClick={() => onTool('Rastreamento')}>Rastreamento</InlineAction>}>
        <Field label="Origem" value={<InlineChoiceField label="Origem" value={matter.leadSource ?? matter.campaignAttribution?.source} options={SOURCES} disabled={!canEditCase} saving={savingInlineField === 'leadSource'} onSave={(value, custom) => { const source = value === '__custom__' ? custom : value; return saveInline('leadSource', () => klmApi.updateCase360(matter.id, { attribution: { source }, matter: { leadSource: source } })); }} />} />
        <Field label="Canal" value={<InlineChoiceField label="Canal" value={matter.campaignAttribution?.channel ?? matter.campaignAttribution?.medium} options={CHANNELS} disabled={!canEditCase} saving={savingInlineField === 'attribution.channel'} onSave={(value, custom) => saveAttribution('attribution.channel', { channel: value === '__custom__' ? custom : value })} />} />
        <Field label="Mídia" value={<InlineTextField label="Mídia" value={matter.campaignAttribution?.medium} disabled={!canEditCase} saving={savingInlineField === 'attribution.medium'} placeholder="Ex.: cpc, orgânico, indicação" onSave={(value) => saveAttribution('attribution.medium', { medium: value })} />} />
        <Field label="Campanha" value={<InlineTextField label="Campanha" value={matter.campaignAttribution?.campaign} disabled={!canEditCase} saving={savingInlineField === 'attribution.campaign'} onSave={(value) => saveAttribution('attribution.campaign', { campaign: value })} />} />
      </Section>

      <Section title="Jornada dos fluxos" icon={Workflow} action={<InlineAction onClick={() => onTool('Ativar fluxo')}>Gerir fluxos</InlineAction>}>
        <MatterFlowJourney matterId={matter.id} />
      </Section>

      <Section title="Saúde e estratégia jurídica" icon={Scale} action={<InlineAction onClick={() => onTool('CRM')}>Editar CRM</InlineAction>}>
        <Field label="Demanda" value={<InlineChoiceField label="Demanda" value={matter.demandType} options={DEMANDS} disabled={!canEditCase} saving={savingInlineField === 'demandType'} onSave={(value, custom) => saveMatter('demandType', { demandType: value === '__custom__' ? custom ?? '' : value })} />} />
        <Field label="Subtema" value={<InlineChoiceField label="Subtema" value={matter.healthSubtheme} customValue={matter.healthSubthemeCustom} options={SUBTHEMES} disabled={!canEditCase} saving={savingInlineField === 'healthSubtheme'} onSave={(value, custom) => saveMatter('healthSubtheme', { healthSubtheme: value === '__custom__' ? 'outro' : value, healthSubthemeCustom: value === '__custom__' ? custom : null })} />} />
        <Field label="Tags" value={<InlineTagsField label="Tags" tags={matter.tags ?? []} disabled={!canEditCase} saving={savingInlineField === 'tags'} onSave={(tags) => saveMatter('tags', { tags })} />} />
        <Field label="Objeto" value={<InlineTextField label="Objeto" value={matter.healthObjectName ?? contact.object} disabled={!canEditCase} saving={savingInlineField === 'healthObjectName'} onSave={(value) => saveMatter('healthObjectName', { healthObjectName: value })} />} />
        <Field label="Operadora" value={<InlineTextField label="Operadora" value={matter.healthInsurer} disabled={!canEditCase} saving={savingInlineField === 'healthInsurer'} onSave={(value) => saveMatter('healthInsurer', { healthInsurer: value })} />} />
        <Field label="Negativa" value={<InlineTextField label="Motivo da negativa" value={matter.healthDenialReason} multiline disabled={!canEditCase} saving={savingInlineField === 'healthDenialReason'} onSave={(value) => saveMatter('healthDenialReason', { healthDenialReason: value })} />} />
        <Field label="Urgência" value={<InlineTextField label="Urgência médica em horas" value={matter.medicalUrgencyHours} type="number" disabled={!canEditCase} saving={savingInlineField === 'medicalUrgencyHours'} display={matter.medicalUrgencyHours ? `${matter.medicalUrgencyHours} horas` : '—'} validate={(value) => !value || Number.isInteger(Number(value)) && Number(value) >= 1 && Number(value) <= 8760 ? null : 'Use um número inteiro entre 1 e 8760.'} onSave={(value) => saveMatter('medicalUrgencyHours', { medicalUrgencyHours: value ? Number(value) : null })} />} danger={Boolean(matter.medicalUrgencyHours && matter.medicalUrgencyHours <= 72)} />
        <Field label="Resumo" value={<InlineTextField label="Resumo executivo" value={matter.executiveSummary} multiline disabled={!canEditCase} saving={savingInlineField === 'executiveSummary'} onSave={(value) => saveMatter('executiveSummary', { executiveSummary: value })} />} />
        <Field label="Estratégia" value={<InlineTextField label="Estratégia" value={matter.strategyNotes} multiline disabled={!canEditCase} saving={savingInlineField === 'strategyNotes'} onSave={(value) => saveMatter('strategyNotes', { strategyNotes: value })} />} />
        {matter.risks?.length ? <div className="mt-2 space-y-1">{matter.risks.map((risk) => <p key={risk} className="flex gap-1.5 text-2xs leading-4 text-[#a53e3c]"><CircleAlert className="mt-0.5 h-3 w-3 shrink-0" />{risk}</p>)}</div> : null}
      </Section>

      <Section title={`Arquivos e documentos (${matter.documents.length})`} icon={FolderOpen} action={<InlineAction onClick={() => onTool('RAG contextual')}>Documentos e RAG</InlineAction>}>
        {matter.documents.length ? <div className="divide-y divide-border">{matter.documents.map((document) => (
          <div key={document.id} className="flex min-w-0 items-center gap-2 py-2">
            <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1"><p className="truncate text-2xs font-medium" title={document.name}>{document.name}</p><p className="mt-0.5 text-2xs text-muted-foreground">{document.type?.replaceAll('_', ' ') ?? 'documento'} · {document.size ?? new Date(document.createdAt).toLocaleDateString('pt-BR')}</p></div>
            <KlmPill tone={statusTone(document.status)}>{document.status.replaceAll('_', ' ')}</KlmPill>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-7 w-7 shrink-0"
              aria-label={`Baixar ${document.name}`}
              title={canViewLeadData ? `Abrir ${document.name}` : 'Sem permissão para abrir arquivos'}
              disabled={!canViewLeadData || downloadMutation.isPending}
              onClick={() => downloadMutation.mutate(document.id)}
            >{downloadingId === document.id ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}</Button>
          </div>
        ))}</div> : <p className="text-2xs text-muted-foreground">Nenhum documento anexado a este card.</p>}
        <div className="mt-3 flex items-center justify-between text-2xs text-muted-foreground"><span>Índice documental</span><strong>{matter.docScore}%</strong></div>
        <div className="mt-1"><KlmProgress value={matter.docScore} tone="gold" /></div>
      </Section>

      <Section title="Contrato, procuração e proposta" icon={FileSignature} action={<InlineAction onClick={() => onTool('Contratos')}>Contratos</InlineAction>}>
        <Field label="ZapSign" value={matter.zapsignStatus?.replaceAll('_', ' ') ?? 'não iniciado'} />
        <Field label="Arquivos" value={contractDocuments.length ? `${contractDocuments.length} documento(s)` : 'nenhum contrato anexado'} />
        <Field label="Proposta" value={<button type="button" className="inline-flex items-center gap-1 text-[#0057d8] hover:underline dark:text-primary" onClick={() => onTool('Proposta existente')}>Abrir proposta vertical<ExternalLink className="h-3 w-3" /></button>} />
        {matter.zapsignSignUrl ? <Button asChild variant="outline" size="sm" className="mt-2 h-8 w-full text-2xs"><a href={matter.zapsignSignUrl} target="_blank" rel="noreferrer"><FileCheck2 className="mr-1.5 h-3.5 w-3.5" />Abrir assinatura</a></Button> : null}
      </Section>

      <Section title={`Agenda (${meetingsQuery.data?.length ?? 0})`} icon={CalendarClock} action={<InlineAction onClick={() => onTool('Agenda')}>Abrir agenda</InlineAction>}>
        {meetingsQuery.isPending ? <p className="flex items-center gap-2 text-2xs text-muted-foreground"><LoaderCircle className="h-3 w-3 animate-spin" />Carregando reuniões</p> : upcomingMeeting ? (
          <div className="space-y-1 text-2xs"><p className="font-semibold">{upcomingMeeting.title}</p><p className="text-muted-foreground">{new Date(upcomingMeeting.startsAt).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}</p>{upcomingMeeting.meetingUrl ? <a href={upcomingMeeting.meetingUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-[#0b7435] hover:underline dark:text-emerald-400">Entrar na sala<ExternalLink className="h-3 w-3" /></a> : <p className="text-[#a53e3c]">Sala on-line ainda não informada</p>}</div>
        ) : <p className="text-2xs text-muted-foreground">Nenhuma reunião futura agendada.</p>}
        <div className="mt-3 flex items-center justify-between text-2xs text-muted-foreground"><span>Links ativos para o lead</span><strong>{activeLinks.length}</strong></div>
        <Button type="button" variant="outline" size="sm" className="mt-2 h-8 w-full text-2xs" onClick={() => onTool('Agenda')}><CalendarClock className="mr-1.5 h-3.5 w-3.5" />Ver disponibilidades</Button>
      </Section>

      <Section title="Honorários e financeiro" icon={Banknote} action={canViewFinance ? <InlineAction onClick={() => onTool('Financeiro')}>Financeiro</InlineAction> : undefined}>
        <Field label="Entrada" value={formatBRL(matter.honorariumEstimate?.fixedFee)} />
        <Field label="Êxito" value={matter.honorariumEstimate?.quotaLitisPercentage != null ? `${matter.honorariumEstimate.quotaLitisPercentage}%` : '—'} />
        <Field label="Valor anual" value={formatBRL(matter.economicValueAnnual)} />
        <Field label="Projeção" value={formatBRL(matter.revenueProjection?.projectedRevenue ?? matter.honorariumEstimate?.totalEstimatedValue)} />
        {canViewFinance ? <><Field label="Lançamentos" value={financeQuery.isPending ? 'carregando' : String(financeQuery.data?.length ?? 0)} /><Field label="Total" value={formatBRL(financeTotal)} /></> : <p className="mt-2 flex items-start gap-2 text-2xs leading-4 text-muted-foreground"><ShieldCheck className="mt-0.5 h-3 w-3 shrink-0" />Seu perfil não possui acesso ao financeiro.</p>}
      </Section>

      <Section title="Financeiro do caso" icon={Banknote} action={canViewFinance ? <InlineAction onClick={() => onTool('Financeiro')}>Financeiro</InlineAction> : undefined}>
        <CaseFinanceSection matter={matter} canViewFinance={canViewFinance} canManageFinance={canManageFinance} />
      </Section>

      <Section title={`Medicamento e tratamento (${matter.treatmentEstimates?.length ?? 0})`} icon={MedicationIcon} action={<InlineAction onClick={() => onTool('Calcular tratamento')}>Calcular</InlineAction>}>
        {matter.treatmentEstimates?.length ? <div className="space-y-3">{matter.treatmentEstimates.map((estimate) => <div key={estimate.id} className="border-l-2 border-primary pl-2 text-2xs"><div className="flex items-start justify-between gap-2"><strong className="min-w-0 truncate">{estimate.medication.productName}</strong><KlmPill tone={estimate.reviewStatus === 'aprovado' ? 'green' : estimate.reviewStatus === 'bloqueado' ? 'red' : 'gold'}>{estimate.reviewStatus}</KlmPill></div><p className="mt-1 line-clamp-2 leading-4 text-muted-foreground">{estimate.medication.cmedPresentation}</p><p className="mt-1 text-muted-foreground">{estimate.presentationsPerCycle.toLocaleString('pt-BR')} apresentação(ões) × {estimate.cycleCount.toLocaleString('pt-BR')} ciclo(s)</p><p className="mt-1 font-semibold tabular-nums text-[#0057d8] dark:text-primary">{formatBRL(estimate.totalTreatmentCost)}</p>{estimate.prescriptionReference ? <p className="mt-1 text-2xs text-muted-foreground">Fonte: {estimate.prescriptionReference}</p> : null}</div>)}</div> : <p className="text-2xs leading-4 text-muted-foreground">Ainda não há cálculo vinculado. O preço CMED isolado não será tratado como custo do tratamento.</p>}
      </Section>

      <Section title={`RAG do caso (${matter.knowledgeChunks.length})`} icon={Database} action={<InlineAction onClick={() => onTool('RAG contextual')}>Consultar fontes</InlineAction>}>
        {matter.conversationInsight?.summary ? <div className="mb-3 border-l-2 border-primary pl-2"><p className="text-2xs font-semibold text-[#0057d8] dark:text-primary">Síntese da conversa</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">{matter.conversationInsight.summary}</p></div> : null}
        {matter.knowledgeChunks.length ? <div className="space-y-2">{matter.knowledgeChunks.slice(0, 6).map((chunk) => <div key={chunk.id} className="text-2xs"><div className="flex items-center justify-between gap-2"><p className="truncate font-medium">{chunk.title ?? chunk.sourceType}</p><span className="shrink-0 text-2xs text-muted-foreground">{['conversation_message', 'whatsapp_message'].includes(chunk.sourceType) ? 'conversa' : 'dossiê'}</span></div><p className="mt-0.5 line-clamp-2 leading-4 text-muted-foreground">{chunk.summary ?? chunk.contentText}</p></div>)}</div> : <p className="text-2xs text-muted-foreground">Nenhum documento ou trecho da conversa foi indexado.</p>}
      </Section>

      <Section title={`Tarefas e pendências (${matter.tasks?.length ?? 0})`} icon={Workflow} action={<InlineAction onClick={() => onTool('Cliente 360')}>Abrir card</InlineAction>}>
        {matter.tasks?.length ? <div className="space-y-2">{matter.tasks.slice(0, 8).map((task) => <div key={task.id} className="flex items-start justify-between gap-2 text-2xs"><span className="min-w-0 flex-1 leading-4">{task.title}</span><KlmPill tone={statusTone(task.status)}>{task.status.replaceAll('_', ' ')}</KlmPill></div>)}</div> : <p className="text-2xs text-muted-foreground">Nenhuma tarefa aberta no card.</p>}
      </Section>

      <Section title="Auditor independente" icon={ShieldCheck} action={<InlineAction onClick={() => onTool('Qualidade')}>Central de qualidade</InlineAction>}>
        {threadId ? (
          <ConversationAuditPanel threadId={threadId} canAct={canSendMessages} />
        ) : (
          <p className="text-2xs leading-4 text-muted-foreground">Vincule a conversa canônica para acompanhar cobertura, respostas e integridade de entrega em tempo real.</p>
        )}
      </Section>

      <Section title="Log interno dos agentes" icon={Sparkles} action={<InlineAction onClick={() => onTool('Auditoria')}>Auditoria</InlineAction>}>
        {auditEntries.length ? <div className="space-y-2.5">{auditEntries.slice(0, 12).map((entry, index) => <div key={entry.id} className="grid grid-cols-[76px_minmax(0,1fr)] gap-2 text-2xs"><span className={cn('truncate font-semibold', index % 2 ? 'text-muted-foreground' : 'text-[#0057d8] dark:text-primary')}>{entry.agent}</span><span className="break-words leading-4 text-muted-foreground">{entry.action}</span></div>)}</div> : <p className="text-2xs text-muted-foreground">Nenhum registro interno deste caso.</p>}
      </Section>

      <Section title="Linha do tempo" icon={History} action={<InlineAction onClick={() => onTool('Auditoria')}>Histórico completo</InlineAction>}>
        {matter.timeline.length ? <div className="space-y-2.5">{matter.timeline.slice(0, 8).map((event) => <div key={event.id} className="border-l border-border pl-2 text-2xs"><p className="font-medium">{event.title}</p><p className="mt-0.5 text-2xs text-muted-foreground">{new Date(event.timestamp).toLocaleString('pt-BR')}</p>{event.description ? <p className="mt-1 line-clamp-2 leading-4 text-muted-foreground">{event.description}</p> : null}</div>)}</div> : <p className="text-2xs text-muted-foreground">Ainda não há eventos no histórico.</p>}
      </Section>

      <Section title="Qualificação do SDR" icon={UserRoundCheck}>
        <SdrQualificationPanel
          matterId={matter.id}
          canViewSensitiveLeadData={canViewLeadData}
          canReprocess={can('kanban', 'edit')}
        />
      </Section>

      <div className="grid grid-cols-2 gap-2 border-t border-border pt-4">
        {[
          ['Funil', 'Funil'],
          ['Fluxos', 'Ativar fluxo'],
          ['Petições', 'Petição contextual'],
          ['Auditoria', 'Auditoria'],
        ].map(([label, tool]) => <Button key={label} type="button" variant="outline" size="sm" className="h-8 justify-start text-2xs" onClick={() => onTool(tool)}>{label}<ChevronRight className="ml-auto h-3 w-3" /></Button>)}
      </div>
    </div>
  );
}

export function ConversationCasePanel({ threadId, matterId, contact, internalEntries, onTool }: ConversationCasePanelProps) {
  const incompleteDocumentRetries = useRef(0);
  const matterQuery = useQuery({
    queryKey: ['klm', 'matter', matterId],
    queryFn: () => klmApi.matter(matterId!),
    enabled: Boolean(matterId),
    refetchOnMount: 'always',
    retry: 2,
    structuralSharing: (previous: KlmMatterDetail | undefined, incoming: KlmMatterDetail) => (
      previous && (previous.manualContextRevision ?? 0) > (incoming.manualContextRevision ?? 0)
        ? previous
        : incoming
    ),
  });
  const { data: matter, isPending, isError, refetch } = matterQuery;

  useEffect(() => {
    if (!matter || matter.documents.length > 0 || matter.docScore <= 0 || incompleteDocumentRetries.current >= 3) return;
    incompleteDocumentRetries.current += 1;
    const timer = window.setTimeout(() => void refetch(), 250 * incompleteDocumentRetries.current);
    return () => window.clearTimeout(timer);
  }, [matter, refetch]);

  if (!matterId) {
    return <div className="p-5 text-center"><CircleAlert className="mx-auto h-5 w-5 text-[#0057d8]" /><p className="mt-3 text-2xs font-semibold">Conversa sem card vinculado</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">Vincule o atendimento no CRM para carregar cliente, documentos, contratos, agenda, financeiro e RAG.</p><Button className="mt-4 h-8 text-2xs" onClick={() => onTool('CRM')}><ClipboardCheck className="mr-1.5 h-3.5 w-3.5" />Abrir CRM</Button></div>;
  }
  if (isPending) return <div className="flex h-full items-center justify-center gap-2 p-6 text-2xs text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />Carregando o dossiê do card</div>;
  if (isError || !matter) return <div className="p-5 text-center"><CircleAlert className="mx-auto h-5 w-5 text-[#a53e3c]" /><p className="mt-3 text-2xs font-semibold">Não foi possível abrir o card</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">O painel não exibirá dados de outro cliente como fallback.</p><Button variant="outline" className="mt-4 h-8 text-2xs" onClick={() => void refetch()}>Tentar novamente</Button></div>;
  return <MatterContent threadId={threadId} matter={matter} contact={contact} internalEntries={internalEntries} onTool={onTool} />;
}

export default ConversationCasePanel;
