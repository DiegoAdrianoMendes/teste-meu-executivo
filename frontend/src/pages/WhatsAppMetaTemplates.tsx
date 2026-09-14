import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Bold,
  Braces,
  CheckCircle2,
  Code2,
  Clock,
  FileEdit,
  Italic,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  SmilePlus,
  Strikethrough,
  Trash2,
  TrendingUp,
  XCircle,
} from 'lucide-react';

import { SectionHeader } from '@/components/common/SectionHeader';
import { UtilityTemplateLibraryPanel } from '@/components/whatsapp/UtilityTemplateLibraryPanel';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  approveTemplateInternalSubmission,
  cloneRejectedTemplateAsDraft,
  createTemplateDraft,
  deleteTemplateDraft,
  analyzeMetaTemplateDraft,
  fetchPendingTemplateInternalApprovals,
  fetchUnifiedTemplates,
  removeLocalTemplateRecord,
  syncMetaTemplates,
  submitTemplateDraft,
  updateTemplateDraft,
} from '@/services/campaigns';
import { fetchWhatsAppConnections, fetchWhatsAppExternalDeliveryGate } from '@/services/whatsapp';
import type { MetaTemplateAnalysis, TemplateButton, TemplateCategory, TemplateComponentsInput, UnifiedTemplate } from '@/types';

// W2.2 — motivos de rejeição da Meta traduzidos + o que corrigir. Mantido em
// sincronia com REJECTED_REASON_TRANSLATIONS no backend
// (app/services/whatsapp/webhook_service.py), mas com o texto acionável que
// só faz sentido no builder.
const REJECTION_REASON_GUIDANCE: Record<string, { label: string; fix: string }> = {
  INVALID_FORMAT: {
    label: 'Formato inválido',
    fix: 'Revise os placeholders ({{1}}, {{2}}...) e a estrutura dos componentes — eles precisam seguir exatamente o padrão da Meta.',
  },
  ABUSIVE_CONTENT: {
    label: 'Conteúdo sinalizado como abusivo',
    fix: 'Remova linguagem agressiva, ameaças ou termos que possam soar coercitivos ao destinatário.',
  },
  INCORRECT_CATEGORY: {
    label: 'Categoria incorreta',
    fix: 'A categoria escolhida não corresponde ao conteúdo. Troque para marketing/utility/authentication conforme a finalidade real da mensagem.',
  },
  SCAM: {
    label: 'Sinalizado como golpe/fraude',
    fix: 'Evite pedir dados sensíveis ou criar urgência artificial; deixe claro quem está enviando e por quê.',
  },
  PROMOTIONAL: {
    label: 'Conteúdo promocional em template de utilidade',
    fix: 'Templates utility não podem ter tom promocional. Mude para categoria marketing ou remova o apelo comercial.',
  },
  TAG_CONTENT_MISMATCH: {
    label: 'Conteúdo não corresponde à categoria declarada',
    fix: 'Alinhe o texto do corpo à categoria escolhida — a Meta compara automaticamente.',
  },
  INVALID_LENGTH: {
    label: 'Tamanho fora do limite',
    fix: 'Reduza o texto: cabeçalho até 60, corpo até 1024 e rodapé até 60 caracteres.',
  },
};

const TEMPLATE_CATEGORIES: TemplateCategory[] = ['utility', 'marketing', 'authentication'];

/** V31: a Meta devolve UTILITY em maiúsculas; o select e o PATCH trabalham em minúsculas. */
function normalizeTemplateCategory(value?: string | null): TemplateCategory {
  const lower = (value ?? '').trim().toLowerCase() as TemplateCategory;
  return TEMPLATE_CATEGORIES.includes(lower) ? lower : 'utility';
}

function rejectionGuidance(rawReason?: string | null) {
  const key = (rawReason || 'NONE').trim().toUpperCase();
  return (
    REJECTION_REASON_GUIDANCE[key] ?? {
      label: key === 'NONE' ? 'Motivo não informado pela Meta' : `Motivo "${key}" (não catalogado)`,
      fix: 'Revise o conteúdo do template contra as diretrizes da Meta antes de reenviar.',
    }
  );
}

const CATEGORY_LABEL: Record<string, string> = {
  marketing: 'Marketing (~7x custo)',
  utility: 'Utility',
  authentication: 'Autenticação',
};

const CATEGORY_BADGE_CLASS: Record<string, string> = {
  marketing: 'border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-200',
  utility: 'border-sky-300 text-sky-800 dark:border-sky-800 dark:text-sky-200',
  authentication: 'border-violet-300 text-violet-800 dark:border-violet-800 dark:text-violet-200',
};

const STATUS_BADGE: Record<string, { label: string; className: string; icon: typeof CheckCircle2 }> = {
  approved: { label: 'Aprovado', className: 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200', icon: CheckCircle2 },
  pending: { label: 'Pendente na Meta', className: 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200', icon: Clock },
  rejected: { label: 'Rejeitado', className: 'border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200', icon: XCircle },
  paused: { label: 'Pausado', className: 'border-orange-300 bg-orange-50 text-orange-800 dark:border-orange-800 dark:bg-orange-950 dark:text-orange-200', icon: AlertTriangle },
  disabled: { label: 'Desativado', className: 'border-zinc-300 bg-zinc-100 text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300', icon: XCircle },
};

const LOCAL_STATUS_BADGE: Record<string, { label: string; className: string }> = {
  draft: { label: 'Rascunho', className: 'border-zinc-300 bg-zinc-100 text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300' },
  submission_prepared: { label: 'Preparado p/ Meta', className: 'border-blue-300 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200' },
  pending_internal_approval: { label: 'Aguarda aprovação interna', className: 'border-purple-300 bg-purple-50 text-purple-800 dark:border-purple-800 dark:bg-purple-950 dark:text-purple-200' },
  // Estados do provisionamento pelo catálogo (V1): sem entrada aqui a tela inteira caía com TypeError.
  catalog_queued: { label: 'Na fila do catálogo', className: 'border-blue-300 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200' },
  catalog_pending_gate: { label: 'Catálogo aguarda o gate', className: 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200' },
  catalog_failed: { label: 'Falhou no catálogo', className: 'border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200' },
  catalog_name_reserved: { label: 'Nome reservado na Meta', className: 'border-orange-300 bg-orange-50 text-orange-800 dark:border-orange-800 dark:bg-orange-950 dark:text-orange-200' },
};

const QUALITY_LABEL: Record<string, string> = { green: 'Boa', yellow: 'Média', red: 'Baixa' };
const QUALITY_BADGE_CLASS: Record<string, string> = {
  green: 'border-emerald-300 text-emerald-800 dark:border-emerald-800 dark:text-emerald-200',
  yellow: 'border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-200',
  red: 'border-red-300 text-red-800 dark:border-red-800 dark:text-red-200',
};

// Estes limites espelham os limites já aplicados pelo serviço de gestão de
// templates. Mantê-los nomeados aqui evita contadores e validação divergentes.
const TEMPLATE_LIMITS = {
  header: 60,
  body: 1024,
  footer: 60,
  buttonText: 25,
  totalButtons: 10,
  quickReplyButtons: 3,
  urlButtons: 2,
  phoneButtons: 1,
} as const;

const FORMAT_ACTIONS = [
  { label: 'Negrito', prefix: '*', suffix: '*', Icon: Bold },
  { label: 'Itálico', prefix: '_', suffix: '_', Icon: Italic },
  { label: 'Riscado', prefix: '~', suffix: '~', Icon: Strikethrough },
  { label: 'Monoespaçado', prefix: '```', suffix: '```', Icon: Code2 },
] as const;

const EMPTY_COMPONENTS: TemplateComponentsInput = {
  headerType: 'NONE',
  headerText: '',
  headerExample: '',
  bodyText: '',
  bodyExamples: [],
  footerText: '',
  buttons: [],
};

function StatusBadge({ template }: { template: UnifiedTemplate }) {
  if (!template.externalTemplateId && template.isDraft) {
    // Estado local desconhecido (backend mais novo que o bundle) cai no badge de rascunho em vez de derrubar a tela.
    const meta = LOCAL_STATUS_BADGE[template.localStatus ?? 'draft'] ?? LOCAL_STATUS_BADGE.draft;
    return <Badge variant="outline" className={meta.className}>{meta.label}</Badge>;
  }
  const meta = STATUS_BADGE[template.status] ?? STATUS_BADGE.pending;
  const Icon = meta.icon;
  return (
    <Badge variant="outline" className={meta.className}>
      <Icon className="mr-1 h-3 w-3" />
      {meta.label}
    </Badge>
  );
}

function extractPlaceholderIssue(text: string): string | null {
  const numbers = Array.from(text.matchAll(/\{\{\s*(\d+)\s*\}\}/g)).map((m) => Number(m[1]));
  if (!numbers.length) return null;
  const unique = Array.from(new Set(numbers)).sort((a, b) => a - b);
  const expected = unique.map((_, index) => index + 1);
  if (JSON.stringify(unique) !== JSON.stringify(expected)) {
    return `As variáveis precisam ser sequenciais a partir de {{1}}. Encontrado: ${unique.join(', ')}.`;
  }
  return null;
}

function placeholderCount(text: string): number {
  return Math.max(0, ...Array.from(text.matchAll(/\{\{\s*(\d+)\s*\}\}/g)).map((match) => Number(match[1])));
}

export function WhatsAppBubblePreview({
  headerType,
  headerText,
  headerExample,
  bodyText,
  bodyExamples,
  footerText,
  buttons,
  variableValues,
  editable = false,
  onVariableChange,
}: TemplateComponentsInput & {
  variableValues?: Record<number, string>;
  editable?: boolean;
  onVariableChange?: (position: number, value: string) => void;
}) {
  const valueFor = (position: number, fallback?: string) => variableValues?.[position] ?? fallback ?? `[valor ${position}]`;
  const renderText = (text: string, scope: 'body' | 'header') => {
    const pieces = text.split(/(\{\{\s*\d+\s*\}\})/g);
    return pieces.map((piece, index) => {
      const match = piece.match(/^\{\{\s*(\d+)\s*\}\}$/);
      if (!match) return <span key={`${scope}-text-${index}`}>{piece}</span>;
      const position = Number(match[1]);
      const fallback = scope === 'body' ? bodyExamples?.[position - 1] : headerExample || undefined;
      if (!editable) return <span key={`${scope}-value-${position}`}>{valueFor(position, fallback)}</span>;
      return (
        <input
          key={`${scope}-input-${position}`}
          aria-label={`Variável ${position}`}
          className="mx-0.5 inline-block min-w-[54px] max-w-[130px] rounded border border-[#0a7cff]/40 bg-white/80 px-1 py-0.5 text-[12px] text-[#102116] outline-none focus:border-[#0a7cff]"
          value={valueFor(position, fallback)}
          onChange={(event) => onVariableChange?.(position, event.target.value)}
        />
      );
    });
  };
  const sampleBody = bodyText || 'Escreva o corpo da mensagem...';
  const sampleHeader = headerText || '';

  return (
    <div className="rounded-xl bg-[#e5ddd5] p-4 dark:bg-[#0b141a]">
      <div className="ml-auto max-w-[280px] rounded-lg border border-emerald-200 bg-[#dcf8c6] p-3 text-[#102116] shadow-sm dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-50">
        {headerType && headerType !== 'NONE' && (
          <div className="mb-1.5 text-[13px] font-semibold">
            {headerType === 'TEXT' ? (sampleHeader ? renderText(sampleHeader, 'header') : 'Cabeçalho') : `[${headerType.toLowerCase()}]`}
          </div>
        )}
        <p data-testid="template-preview-body" className="whitespace-pre-wrap text-[13px] leading-5">{renderText(sampleBody, 'body')}</p>
        {footerText && <p className="mt-1.5 text-[11px] text-[#4a5a4d] dark:text-emerald-300/70">{footerText}</p>}
        <p className="mt-1.5 text-right text-[10px] text-[#4a5a4d] dark:text-emerald-300/70">12:00</p>
        {buttons.length > 0 && (
          <div className="mt-2 space-y-1 border-t border-emerald-200/70 pt-2 dark:border-emerald-900/70">
            {buttons.map((button, index) => (
              <div
                key={index}
                className="rounded-md border border-emerald-300/60 bg-white/60 py-1 text-center text-[12px] font-medium text-[#0a7cff] dark:border-emerald-800/60 dark:bg-emerald-900/40"
              >
                {button.text || 'Botão'}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

interface DraftFormState {
  whatsappConnectionId: string;
  name: string;
  language: string;
  category: TemplateCategory;
  components: TemplateComponentsInput;
}

const EMPTY_FORM: DraftFormState = {
  whatsappConnectionId: '',
  name: '',
  language: 'pt_BR',
  category: 'utility',
  components: EMPTY_COMPONENTS,
};

export default function WhatsAppMetaTemplates() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [builderOpen, setBuilderOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [copyingTemplateId, setCopyingTemplateId] = useState<string | null>(null);
  const [form, setForm] = useState<DraftFormState>(EMPTY_FORM);
  const [recordToRemove, setRecordToRemove] = useState<UnifiedTemplate | null>(null);
  const bodyTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [templateAnalysis, setTemplateAnalysis] = useState<MetaTemplateAnalysis | null>(null);
  const [oabAckRequiredFor, setOabAckRequiredFor] = useState<string | null>(null);

  const connectionsQuery = useQuery({ queryKey: ['whatsapp-connections-for-templates'], queryFn: fetchWhatsAppConnections });
  const templatesQuery = useQuery({
    queryKey: ['whatsapp-unified-templates'],
    queryFn: () => fetchUnifiedTemplates(null, true),
  });
  const externalDeliveryGateQuery = useQuery({
    queryKey: ['whatsapp-external-delivery-gate'],
    queryFn: fetchWhatsAppExternalDeliveryGate,
  });
  const pendingApprovalsQuery = useQuery({
    queryKey: ['whatsapp-template-pending-internal-approvals'],
    queryFn: fetchPendingTemplateInternalApprovals,
  });

  const allTemplates = templatesQuery.data ?? [];
  const templates = allTemplates.filter((template) => !template.removedAtSourceAt);
  const removedAtSourceTemplates = allTemplates.filter((template) => Boolean(template.removedAtSourceAt));
  const pendingApprovals = pendingApprovalsQuery.data ?? [];
  const gateClosed = externalDeliveryGateQuery.isSuccess && externalDeliveryGateQuery.data === false;
  const gateUnknown = externalDeliveryGateQuery.isError;

  const invalidateTemplateQueries = () => {
    queryClient.invalidateQueries({ queryKey: ['whatsapp-unified-templates'] });
    queryClient.invalidateQueries({ queryKey: ['whatsapp-template-pending-internal-approvals'] });
  };

  const approveInternalMutation = useMutation({
    mutationFn: (templateId: string) => approveTemplateInternalSubmission(templateId),
    onSuccess: (result) => {
      toast({ title: 'Submissão aprovada', description: result.message });
      invalidateTemplateQueries();
    },
    onError: (error: unknown) => {
      const detail = (error as { data?: { message?: string } })?.data;
      toast({
        title: 'Não foi possível aprovar',
        description: detail?.message || (error instanceof Error ? error.message : 'Tente novamente.'),
        variant: 'destructive',
      });
    },
  });

  const cloneRejectedMutation = useMutation({
    mutationFn: (templateId: string) => cloneRejectedTemplateAsDraft(templateId),
    onSuccess: (clone) => {
      toast({ title: 'Rascunho criado a partir do template rejeitado', description: `Corrija e reenvie: ${clone.name}` });
      invalidateTemplateQueries();
    },
    onError: (error: unknown) => {
      const detail = (error as { data?: { message?: string } })?.data;
      toast({
        title: 'Não foi possível clonar o template',
        description: detail?.message || (error instanceof Error ? error.message : 'Tente novamente.'),
        variant: 'destructive',
      });
    },
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createTemplateDraft({
        whatsappConnectionId: form.whatsappConnectionId,
        name: form.name,
        language: form.language,
        category: form.category,
        components: form.components,
      }),
    onSuccess: () => {
      toast({ title: 'Rascunho de template criado.' });
      queryClient.invalidateQueries({ queryKey: ['whatsapp-unified-templates'] });
      setBuilderOpen(false);
      setCopyingTemplateId(null);
      setForm(EMPTY_FORM);
    },
    onError: (error: unknown) => {
      const detail = (error as { data?: { errors?: string[]; message?: string } })?.data;
      toast({
        title: 'Não foi possível salvar o rascunho',
        description: detail?.errors?.join(' ') || detail?.message || (error instanceof Error ? error.message : 'Revise os campos.'),
        variant: 'destructive',
      });
    },
  });

  const updateMutation = useMutation({
    mutationFn: () =>
      updateTemplateDraft(editingId as string, {
        category: form.category,
        language: form.language,
        components: form.components,
      }),
    onSuccess: () => {
      toast({ title: 'Rascunho atualizado.' });
      queryClient.invalidateQueries({ queryKey: ['whatsapp-unified-templates'] });
      setBuilderOpen(false);
      setEditingId(null);
      setCopyingTemplateId(null);
      setForm(EMPTY_FORM);
    },
    onError: (error: unknown) => {
      const detail = (error as { data?: { errors?: string[]; message?: string } })?.data;
      toast({
        title: 'Não foi possível atualizar o rascunho',
        description: detail?.errors?.join(' ') || detail?.message || (error instanceof Error ? error.message : 'Revise os campos.'),
        variant: 'destructive',
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (templateId: string) => deleteTemplateDraft(templateId),
    onSuccess: () => {
      toast({ title: 'Rascunho excluído.' });
      queryClient.invalidateQueries({ queryKey: ['whatsapp-unified-templates'] });
    },
  });

  const removeLocalRecordMutation = useMutation({
    mutationFn: (templateId: string) => removeLocalTemplateRecord(templateId),
    onSuccess: () => {
      toast({ title: 'Registro local removido.' });
      setRecordToRemove(null);
      invalidateTemplateQueries();
    },
    onError: (error: unknown) => {
      const detail = (error as { data?: { message?: string } })?.data;
      toast({
        title: 'Não foi possível remover o registro local',
        description: detail?.message || (error instanceof Error ? error.message : 'Tente novamente.'),
        variant: 'destructive',
      });
    },
  });

  const syncMutation = useMutation({
    mutationFn: () => syncMetaTemplates(),
    onSuccess: () => {
      toast({ title: 'Templates reconciliados com a Meta.' });
      invalidateTemplateQueries();
    },
    onError: (error: unknown) => {
      const detail = (error as { data?: { message?: string } })?.data;
      toast({
        title: 'Não foi possível sincronizar com a Meta',
        description: detail?.message || (error instanceof Error ? error.message : 'Tente novamente.'),
        variant: 'destructive',
      });
    },
  });

  const analyzeMutation = useMutation({
    mutationFn: async () => {
      let templateId = editingId;
      if (!templateId) {
        const created = await createTemplateDraft({
          whatsappConnectionId: form.whatsappConnectionId,
          name: form.name,
          language: form.language,
          category: form.category,
          components: form.components,
        });
        templateId = created.id;
        setEditingId(created.id);
        queryClient.invalidateQueries({ queryKey: ['whatsapp-unified-templates'] });
      }
      return analyzeMetaTemplateDraft({
        templateId,
        name: form.name,
        categoryIntent: form.category,
        body: form.components.bodyText,
        sampleVariables: (form.components.bodyExamples ?? []).join(', '),
      });
    },
    onSuccess: (analysis) => setTemplateAnalysis(analysis),
    onError: () => setTemplateAnalysis(null),
  });

  const submitMutation = useMutation({
    mutationFn: ({ templateId, acknowledged }: { templateId: string; acknowledged?: boolean }) => acknowledged
      ? submitTemplateDraft(templateId, true)
      : submitTemplateDraft(templateId),
    onSuccess: (result) => {
      queryClient.setQueryData<UnifiedTemplate[]>(['whatsapp-unified-templates'], (current = []) =>
        current.map((template) => (template.id === result.template.id ? result.template : template)),
      );
      toast({
        title: result.submittedToMeta
          ? 'Template submetido à Meta'
          : result.template.localStatus === 'pending_internal_approval'
            ? 'Aguardando aprovação interna'
            : 'Rascunho preparado',
        description: result.message,
      });
      invalidateTemplateQueries();
    },
    onError: (error: unknown, variables) => {
      const detail = (error as { data?: { errors?: string[]; message?: string; code?: string }; detail?: { code?: string } });
      const errorCode = detail.data?.code ?? detail.detail?.code;
      if (errorCode === 'WA-TPL-OAB-ACK-REQUIRED') {
        setOabAckRequiredFor(variables.templateId);
        return;
      }
      toast({
        title: 'Não foi possível submeter',
        description: detail.data?.errors?.join(' ') || detail.data?.message || (error instanceof Error ? error.message : 'Tente novamente.'),
        variant: 'destructive',
      });
    },
  });

  const inlineIssues = useMemo(() => {
    const issues: string[] = [];
    if (!form.name.trim()) issues.push('Informe um nome para o template.');
    if (!form.components.bodyText.trim()) issues.push('O corpo da mensagem é obrigatório.');
    if (form.components.bodyText.length > TEMPLATE_LIMITS.body) issues.push(`O corpo excede ${TEMPLATE_LIMITS.body} caracteres.`);
    if ((form.components.headerText ?? '').length > TEMPLATE_LIMITS.header) issues.push(`O cabeçalho excede ${TEMPLATE_LIMITS.header} caracteres.`);
    if ((form.components.footerText ?? '').length > TEMPLATE_LIMITS.footer) issues.push(`O rodapé excede ${TEMPLATE_LIMITS.footer} caracteres.`);
    const headerIssue = form.components.headerType === 'TEXT' ? extractPlaceholderIssue(form.components.headerText || '') : null;
    const bodyIssue = extractPlaceholderIssue(form.components.bodyText);
    if (headerIssue) issues.push(`Cabeçalho: ${headerIssue}`);
    if (bodyIssue) issues.push(`Corpo: ${bodyIssue}`);
    const headerVariables = form.components.headerType === 'TEXT' ? placeholderCount(form.components.headerText || '') : 0;
    if (headerVariables && !form.components.headerExample?.trim()) {
      issues.push('Informe um exemplo para a variável do cabeçalho.');
    }
    const bodyVariables = placeholderCount(form.components.bodyText);
    if (bodyVariables && (form.components.bodyExamples ?? []).length !== bodyVariables) {
      issues.push(`Informe ${bodyVariables} exemplo(s) para as variáveis do corpo.`);
    }
    if ((form.components.bodyExamples ?? []).some((example) => !example.trim())) {
      issues.push('Os exemplos das variáveis não podem ficar vazios.');
    }
    if (form.components.footerText && /\{\{\s*\d+\s*\}\}/.test(form.components.footerText)) {
      issues.push('O rodapé não pode ter variáveis.');
    }
    const urlButtons = form.components.buttons.filter((b) => b.type === 'URL');
    const quickReplyButtons = form.components.buttons.filter((b) => b.type === 'QUICK_REPLY');
    const phoneButtons = form.components.buttons.filter((b) => b.type === 'PHONE_NUMBER');
    if (form.components.buttons.length > TEMPLATE_LIMITS.totalButtons) issues.push(`No máximo ${TEMPLATE_LIMITS.totalButtons} botões por template.`);
    if (quickReplyButtons.length > TEMPLATE_LIMITS.quickReplyButtons) issues.push(`No máximo ${TEMPLATE_LIMITS.quickReplyButtons} botões de resposta rápida.`);
    if (urlButtons.length > TEMPLATE_LIMITS.urlButtons) issues.push(`No máximo ${TEMPLATE_LIMITS.urlButtons} botões de URL.`);
    if (phoneButtons.length > TEMPLATE_LIMITS.phoneButtons) issues.push(`No máximo ${TEMPLATE_LIMITS.phoneButtons} botão de telefone.`);
    form.components.buttons.forEach((button, index) => {
      if (button.text.length > TEMPLATE_LIMITS.buttonText) issues.push(`Botão ${index + 1}: o texto excede ${TEMPLATE_LIMITS.buttonText} caracteres.`);
    });
    urlButtons.forEach((button, index) => {
      const url = button.url || '';
      if (/\{\{\s*\d+\s*\}\}/.test(url) && !url.trim().endsWith('{{1}}')) {
        issues.push(`Botão de URL ${index + 1}: a variável dinâmica só pode ser {{1}} no final do link.`);
      }
    });
    return issues;
  }, [form]);

  const openCreateBuilder = () => {
    setEditingId(null);
    setCopyingTemplateId(null);
    setTemplateAnalysis(null);
    setForm({ ...EMPTY_FORM, whatsappConnectionId: connectionsQuery.data?.[0]?.id ?? '' });
    setBuilderOpen(true);
  };

  const openEditBuilder = (template: UnifiedTemplate) => {
    setEditingId(template.id);
    setCopyingTemplateId(null);
    setTemplateAnalysis(null);
    setForm({
      whatsappConnectionId: template.whatsappConnectionId,
      name: template.name,
      language: template.language,
      category: normalizeTemplateCategory(template.category),
      components: buildComponentsFromMeta(template.components),
    });
    setBuilderOpen(true);
  };

  const openCopyBuilder = (template: UnifiedTemplate) => {
    setEditingId(null);
    setCopyingTemplateId(template.id);
    setTemplateAnalysis(null);
    setForm({
      whatsappConnectionId: template.whatsappConnectionId,
      name: `${template.name.replace(/_v\d+$/, '')}_v2`,
      language: template.language,
      category: normalizeTemplateCategory(template.category),
      components: buildComponentsFromMeta(template.components),
    });
    setBuilderOpen(true);
  };

  // Deep links from conversation/sequence authoring land here with create=1
  // or copy=<template-id>. Consume the flag once so a refresh does not reopen
  // a dismissed dialog while retaining any explicit return URL.
  useEffect(() => {
    if (typeof window === 'undefined' || builderOpen) return;
    const params = new URLSearchParams(window.location.search);
    if (params.has('create')) {
      openCreateBuilder();
      params.delete('create');
      const next = new URL(window.location.href);
      next.search = params.toString();
      window.history.replaceState({}, '', next.toString());
      return;
    }
    const copyId = params.get('copy');
    const source = copyId
      ? templates.find((template) => template.id === copyId || template.externalTemplateId === copyId)
      : null;
    if (!source) return;
    openCopyBuilder(source);
    params.delete('copy');
    const next = new URL(window.location.href);
    next.search = params.toString();
    window.history.replaceState({}, '', next.toString());
  // The builder open/edit callbacks are intentionally invoked only for a
  // matching deep-link and are stable for the lifetime of this effect.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [builderOpen, templates]);

  const updateButton = (index: number, patch: Partial<TemplateButton>) => {
    setForm((current) => ({
      ...current,
      components: {
        ...current.components,
        buttons: current.components.buttons.map((button, i) => (i === index ? { ...button, ...patch } : button)),
      },
    }));
  };

  const insertBodyText = (value: string, selectInserted = false) => {
    const textarea = bodyTextareaRef.current;
    const source = form.components.bodyText;
    const start = textarea?.selectionStart ?? source.length;
    const end = textarea?.selectionEnd ?? start;
    const next = `${source.slice(0, start)}${value}${source.slice(end)}`;
    setForm((current) => ({ ...current, components: { ...current.components, bodyText: next } }));
    window.requestAnimationFrame(() => {
      if (!textarea) return;
      textarea.focus();
      const cursor = start + value.length;
      textarea.setSelectionRange(selectInserted ? start : cursor, selectInserted ? cursor : cursor);
    });
  };

  const formatBodyText = (prefix: string, suffix: string) => {
    const textarea = bodyTextareaRef.current;
    const source = form.components.bodyText;
    const start = textarea?.selectionStart ?? source.length;
    const end = textarea?.selectionEnd ?? start;
    const selected = source.slice(start, end) || 'texto';
    insertBodyText(`${prefix}${selected}${suffix}`, !source.slice(start, end));
  };

  const bodyNeedsQuickReply = /follow[ -]?up|reativa[cç][aã]o/i.test(`${form.name} ${form.components.bodyText}`)
    && form.components.buttons.length === 0;

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Templates WhatsApp Meta"
        description="Crie, valide e submeta templates oficiais da Meta. O status de aprovação real chega pelo sync/webhook — aqui você só prepara e envia."
      />

      {gateClosed && (
        <Alert>
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Submissão real à Meta ainda não está liberada neste ambiente</AlertTitle>
          <AlertDescription>
            Rascunhos validados ficam marcados como "preparado para a Meta" e serão enviados de fato assim que as
            entregas externas forem habilitadas neste ambiente. Nenhum template é marcado como aprovado por aqui.
          </AlertDescription>
        </Alert>
      )}

      {gateUnknown && (
        <Alert className="border-amber-400 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/30" role="alert">
          <AlertTriangle className="h-4 w-4 text-amber-700 dark:text-amber-300" />
          <AlertTitle>Não foi possível confirmar se as entregas externas estão liberadas</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center gap-2">
            <span>
              A consulta ao gate do ambiente falhou. A sincronização com a Meta fica desabilitada até a consulta
              responder; os rascunhos continuam editáveis.
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={externalDeliveryGateQuery.isFetching}
              onClick={() => {
                void externalDeliveryGateQuery.refetch();
              }}
            >
              Tentar novamente
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {oabAckRequiredFor && (
        <Alert className="border-amber-400 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/30">
          <AlertTriangle className="h-4 w-4 text-amber-700 dark:text-amber-300" />
          <AlertTitle>Revisão ética necessária antes do reenvio</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center gap-2">
            O conteúdo contém sinais de risco OAB alto. Confirme que você revisou o alerta e deseja prosseguir.
            <Button size="sm" variant="outline" onClick={() => { submitMutation.mutate({ templateId: oabAckRequiredFor, acknowledged: true }); setOabAckRequiredFor(null); }}>
              Confirmo ciência e reenviar
            </Button>
          </AlertDescription>
        </Alert>
      )}

      <UtilityTemplateLibraryPanel />

      {pendingApprovals.length > 0 && (
        <Card className="border-purple-300 bg-purple-50/60 dark:border-purple-800 dark:bg-purple-950/30">
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-purple-700 dark:text-purple-300" />
              <CardTitle className="text-sm">
                {pendingApprovals.length} template(s) aguardando sua aprovação antes do envio à Meta
              </CardTitle>
            </div>
            <CardDescription>
              Quem pediu não tem permissão de aprovador. Revise o conteúdo e aprove para liberar a submissão.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {pendingApprovals.map((item) => (
              <div
                key={item.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-purple-200 bg-white p-2 dark:border-purple-900 dark:bg-purple-950/40"
              >
                <div className="text-sm">
                  <span className="font-medium">{item.name}</span>
                  <span className="ml-2 text-xs text-muted-foreground">{item.category ?? 'sem categoria'} · {item.language}</span>
                </div>
                <Button
                  size="sm"
                  onClick={() => approveInternalMutation.mutate(item.id)}
                  disabled={approveInternalMutation.isPending}
                >
                  <ShieldCheck className="mr-1.5 h-3.5 w-3.5" />
                  Aprovar submissão
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {templates.length} template(s) — rascunhos locais e sincronizados da Meta, em uma lista só.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => syncMutation.mutate()}
            disabled={syncMutation.isPending || gateClosed || gateUnknown}
          >
            <RefreshCw className={`mr-1.5 h-4 w-4 ${syncMutation.isPending ? 'animate-spin' : ''}`} />
            Sincronizar com a Meta
          </Button>
          <Button onClick={openCreateBuilder}>
            <Plus className="mr-1.5 h-4 w-4" />
            Novo rascunho
          </Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {templatesQuery.isLoading && (
          <Card><CardContent className="py-6 text-sm text-muted-foreground">Carregando templates...</CardContent></Card>
        )}
        {!templatesQuery.isLoading && templates.length === 0 && (
          <Card className="md:col-span-2 xl:col-span-3">
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              Nenhum template ainda. Crie um rascunho ou sincronize os aprovados na Meta em Campanhas.
            </CardContent>
          </Card>
        )}
        {templates.map((template) => (
          <Card key={template.id}>
            <CardHeader className="space-y-2">
              <div className="flex items-start justify-between gap-2">
                <CardTitle className="text-sm">{template.name}</CardTitle>
                <StatusBadge template={template} />
              </div>
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="outline" className={CATEGORY_BADGE_CLASS[template.category ?? ''] ?? ''}>
                  {CATEGORY_LABEL[template.category ?? ''] ?? template.category ?? 'sem categoria'}
                </Badge>
                <Badge variant="outline">{template.language}</Badge>
                {template.qualityRating && (
                  <Badge variant="outline" className={QUALITY_BADGE_CLASS[template.qualityRating.toLowerCase()] ?? ''}>
                    qualidade {QUALITY_LABEL[template.qualityRating.toLowerCase()] ?? template.qualityRating}
                  </Badge>
                )}
              </div>
              <CardDescription>
                {template.externalTemplateId
                  ? `Enviado à Meta · ID ${template.externalTemplateId}`
                  : template.isDraft
                    ? 'Rascunho local'
                    : 'Sincronizado da Meta'}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {template.validationErrors.length > 0 && (
                <ul className="list-disc space-y-0.5 pl-4 text-xs text-red-700 dark:text-red-300">
                  {template.validationErrors.map((error, index) => (
                    <li key={index}>{error}</li>
                  ))}
                </ul>
              )}

              {template.status === 'rejected' && Boolean(template.externalTemplateId) && (
                <Alert variant="destructive" className="py-2">
                  <XCircle className="h-4 w-4" />
                  <AlertTitle className="text-xs">
                    Rejeitado: {rejectionGuidance(template.rejectedReason).label}
                  </AlertTitle>
                  <AlertDescription className="text-xs">
                    {rejectionGuidance(template.rejectedReason).fix}
                  </AlertDescription>
                </Alert>
              )}

              {template.externalTemplateId && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-red-600 hover:text-red-700"
                  onClick={() => setRecordToRemove(template)}
                >
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                  Remover registro local
                </Button>
              )}

              {template.previousCategory && template.previousCategory !== template.category && (
                <Alert className="border-amber-300 bg-amber-50 py-2 dark:border-amber-800 dark:bg-amber-950/40">
                  <TrendingUp className="h-4 w-4 text-amber-700 dark:text-amber-300" />
                  <AlertTitle className="text-xs">
                    A Meta recategorizou este template: {CATEGORY_LABEL[template.previousCategory] ?? template.previousCategory} → {CATEGORY_LABEL[template.category ?? ''] ?? template.category}
                  </AlertTitle>
                  <AlertDescription className="text-xs">
                    {template.category === 'marketing'
                      ? 'Impacto de custo: mensagens deste template agora cobram na faixa marketing, cerca de 7x mais caro que utility.'
                      : 'A tarifa por mensagem deste template muda a partir de agora conforme a nova categoria.'}
                  </AlertDescription>
                </Alert>
              )}

              {template.status === 'rejected' && Boolean(template.externalTemplateId) && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => cloneRejectedMutation.mutate(template.id)}
                  disabled={cloneRejectedMutation.isPending}
                >
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                  Corrigir e reenviar
                </Button>
              )}

              {template.isDraft && !template.externalTemplateId && (
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => openEditBuilder(template)}
                    disabled={template.localStatus !== 'draft'}
                  >
                    <FileEdit className="mr-1.5 h-3.5 w-3.5" />
                    Editar
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => submitMutation.mutate({ templateId: template.id })}
                    disabled={submitMutation.isPending || template.localStatus !== 'draft'}
                  >
                    <Send className="mr-1.5 h-3.5 w-3.5" />
                    {template.localStatus === 'pending_internal_approval'
                      ? 'Aguardando aprovação'
                      : template.localStatus === 'submission_prepared'
                        ? 'Já preparado'
                        : 'Submeter'}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-red-600 hover:text-red-700"
                    onClick={() => deleteMutation.mutate(template.id)}
                    disabled={deleteMutation.isPending}
                  >
                    <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                    Excluir
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      {removedAtSourceTemplates.length > 0 && (
        <section className="space-y-3" aria-labelledby="removed-meta-templates-title">
          <div>
            <h2 id="removed-meta-templates-title" className="text-sm font-semibold">
              Histórico — removidos na Meta
            </h2>
            <p className="text-xs text-muted-foreground">
              Estes registros não existem mais na WABA. Nada foi apagado localmente sem sua confirmação.
            </p>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {removedAtSourceTemplates.map((template) => (
              <Card key={template.id} className="border-dashed opacity-90">
                <CardHeader className="space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle className="text-sm">{template.name}</CardTitle>
                    <Badge variant="outline">Removido na Meta</Badge>
                  </div>
                  <CardDescription>
                    {template.externalTemplateId ? `ID Meta ${template.externalTemplateId}` : 'Sem ID Meta'}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-red-600 hover:text-red-700"
                    onClick={() => setRecordToRemove(template)}
                  >
                    <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                    Remover registro local
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
        </section>
      )}

      <AlertDialog open={recordToRemove !== null} onOpenChange={(open) => { if (!open) setRecordToRemove(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remover o registro local?</AlertDialogTitle>
            <AlertDialogDescription>
              O histórico deste template deixará de aparecer no Meu Executivo. Esta ação não exclui nem altera o
              template na Meta. Se ele continuar na WABA, poderá reaparecer na próxima sincronização. Para excluir
              na origem, use o WhatsApp Manager e sincronize novamente.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => recordToRemove && removeLocalRecordMutation.mutate(recordToRemove.id)}
              disabled={removeLocalRecordMutation.isPending}
              className="bg-red-600 text-white hover:bg-red-700"
            >
              Confirmar remoção local
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={builderOpen} onOpenChange={setBuilderOpen}>
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{copyingTemplateId ? 'Criar cópia de template' : editingId ? 'Editar rascunho de template' : 'Novo rascunho de template'}</DialogTitle>
            <DialogDescription>
              Preencha o conteúdo. As variáveis usam o formato {'{{1}}'}, {'{{2}}'}... e precisam ser sequenciais.
              {copyingTemplateId ? ' Esta cópia é um novo rascunho: altere o nome se necessário e envie para nova aprovação da Meta.' : editingId && ' Editar cria uma nova versão: confirme o nome com sufixo _v2 antes de enviar.'}
            </DialogDescription>
          </DialogHeader>

          {(editingId || copyingTemplateId) && (
            <Alert className="border-amber-300 bg-amber-50 py-2 dark:border-amber-800 dark:bg-amber-950/30">
              <AlertTriangle className="h-4 w-4 text-amber-700 dark:text-amber-300" />
              <AlertDescription className="text-xs">
                Templates enviados não são editados na Meta. Esta operação cria uma nova cópia; a Meta precisa aprová-la antes de qualquer envio.
              </AlertDescription>
            </Alert>
          )}

          <div className="grid gap-6 md:grid-cols-2">
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label>Número oficial</Label>
                <Select
                  value={form.whatsappConnectionId || ''}
                  onValueChange={(value) => setForm((current) => ({ ...current, whatsappConnectionId: value }))}
                  disabled={Boolean(editingId)}
                >
                  <SelectTrigger><SelectValue placeholder="Escolha a conexão" /></SelectTrigger>
                  <SelectContent>
                    {(connectionsQuery.data ?? []).map((connection) => (
                      <SelectItem key={connection.id} value={connection.id}>
                        {connection.displayName || connection.displayPhoneNumber || connection.phoneNumber}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label>Nome do template</Label>
                  <Input
                    value={form.name}
                    onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                    placeholder="atualizacao_processo"
                    disabled={Boolean(editingId)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Idioma</Label>
                  <Input
                    value={form.language}
                    onChange={(event) => setForm((current) => ({ ...current, language: event.target.value }))}
                    placeholder="pt_BR"
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <Label>Categoria</Label>
                <Select value={form.category} onValueChange={(value) => setForm((current) => ({ ...current, category: value as TemplateCategory }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="utility">Utility</SelectItem>
                    <SelectItem value="marketing">Marketing (custa ~7x mais)</SelectItem>
                    <SelectItem value="authentication">Autenticação</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label>Cabeçalho</Label>
                <Select
                  value={form.components.headerType}
                  onValueChange={(value) =>
                    setForm((current) => ({ ...current, components: { ...current.components, headerType: value as TemplateComponentsInput['headerType'] } }))
                  }
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="NONE">Sem cabeçalho</SelectItem>
                    <SelectItem value="TEXT">Texto</SelectItem>
                    <SelectItem value="IMAGE">Imagem</SelectItem>
                    <SelectItem value="VIDEO">Vídeo</SelectItem>
                    <SelectItem value="DOCUMENT">Documento</SelectItem>
                  </SelectContent>
                </Select>
                {form.components.headerType === 'TEXT' && (
                  <div className="mt-2 space-y-2">
                    <Input
                      value={form.components.headerText ?? ''}
                      onChange={(event) => setForm((current) => ({ ...current, components: { ...current.components, headerText: event.target.value } }))}
                      placeholder="Olá {{1}}"
                      maxLength={TEMPLATE_LIMITS.header}
                    />
                    <p className="text-xs text-muted-foreground">{(form.components.headerText ?? '').length}/{TEMPLATE_LIMITS.header} caracteres</p>
                    {placeholderCount(form.components.headerText || '') > 0 && (
                      <Input
                        value={form.components.headerExample ?? ''}
                        onChange={(event) => setForm((current) => ({ ...current, components: { ...current.components, headerExample: event.target.value } }))}
                        placeholder="Exemplo para {{1}}: Kleist"
                        aria-label="Exemplo da variável do cabeçalho"
                      />
                    )}
                  </div>
                )}
              </div>

              <div className="space-y-1.5">
                <Label>Corpo da mensagem</Label>
                <div className="flex flex-wrap items-center gap-1 rounded-md border bg-muted/20 p-1" aria-label="Formatação WhatsApp">
                  {FORMAT_ACTIONS.map(({ label, prefix, suffix, Icon }) => (
                    <Button key={label} type="button" size="icon" variant="ghost" className="h-8 w-8" title={label} aria-label={label} onClick={() => formatBodyText(prefix, suffix)}>
                      <Icon className="h-4 w-4" />
                    </Button>
                  ))}
                  <Button type="button" size="icon" variant="ghost" className="h-8 w-8" title="Inserir emoji" aria-label="Inserir emoji" onClick={() => insertBodyText('🙂')}>
                    <SmilePlus className="h-4 w-4" />
                  </Button>
                  <Button type="button" size="icon" variant="ghost" className="h-8 w-8" title="Inserir variável" aria-label="Inserir variável" onClick={() => insertBodyText(`{{${placeholderCount(form.components.bodyText) + 1}}}`)}>
                    <Braces className="h-4 w-4" />
                  </Button>
                  <span className="ml-auto px-1 text-[11px] text-muted-foreground">WhatsApp: *negrito* _itálico_ ~riscado~ ```mono```</span>
                </div>
                <Textarea
                  ref={bodyTextareaRef}
                  value={form.components.bodyText}
                  onChange={(event) => setForm((current) => ({ ...current, components: { ...current.components, bodyText: event.target.value } }))}
                  placeholder="Seu processo {{1}} teve atualização em {{2}}."
                  rows={4}
                  maxLength={TEMPLATE_LIMITS.body}
                />
                <p className="text-xs text-muted-foreground">{form.components.bodyText.length}/{TEMPLATE_LIMITS.body} caracteres</p>
                <Button type="button" variant="outline" size="sm" onClick={() => analyzeMutation.mutate()} disabled={analyzeMutation.isPending || form.components.bodyText.trim().length < 10}>
                  {analyzeMutation.isPending ? 'Salvando e analisando…' : 'Analisar categoria e risco OAB'}
                </Button>
                {templateAnalysis && (
                  <Alert className={templateAnalysis.oabRisk.level === 'high' ? 'border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950/30' : 'border-sky-300 bg-sky-50 dark:border-sky-800 dark:bg-sky-950/30'}>
                    <AlertTriangle className="h-4 w-4" />
                    <AlertTitle className="text-xs">Sugestão Meta: {templateAnalysis.metaCategorySuggested ?? templateAnalysis.predictedCategory} · risco OAB {templateAnalysis.oabRisk.level}</AlertTitle>
                    <AlertDescription className="text-xs">
                      {templateAnalysis.marketingSignals.length > 0 && <span className="block">Sinais de marketing: {templateAnalysis.marketingSignals.join(', ')}</span>}
                      {templateAnalysis.oabRisk.signals.length > 0 && <span className="block">Sinais éticos: {templateAnalysis.oabRisk.signals.join(', ')}</span>}
                      {templateAnalysis.recommendation}
                    </AlertDescription>
                  </Alert>
                )}
                {Array.from({ length: placeholderCount(form.components.bodyText) }, (_, index) => (
                  <Input
                    key={`body-example-${index + 1}`}
                    value={form.components.bodyExamples?.[index] ?? ''}
                    onChange={(event) => setForm((current) => {
                      const examples = [...(current.components.bodyExamples ?? [])];
                      examples[index] = event.target.value;
                      return { ...current, components: { ...current.components, bodyExamples: examples.slice(0, placeholderCount(current.components.bodyText)) } };
                    })}
                    placeholder={`Exemplo para {{${index + 1}}}`}
                    aria-label={`Exemplo da variável ${index + 1} do corpo`}
                  />
                ))}
              </div>

              <div className="space-y-1.5">
                <Label>Rodapé (opcional)</Label>
                <Input
                  value={form.components.footerText ?? ''}
                  onChange={(event) => setForm((current) => ({ ...current, components: { ...current.components, footerText: event.target.value } }))}
                  placeholder="MV Advocacia"
                  maxLength={TEMPLATE_LIMITS.footer}
                />
                <p className="text-xs text-muted-foreground">{(form.components.footerText ?? '').length}/{TEMPLATE_LIMITS.footer} caracteres</p>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label>Botões (opcional)</Label>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={form.components.buttons.length >= TEMPLATE_LIMITS.totalButtons}
                    onClick={() =>
                      setForm((current) => ({
                        ...current,
                        components: {
                          ...current.components,
                          buttons: [...current.components.buttons, { type: 'QUICK_REPLY', text: '' }],
                        },
                      }))
                    }
                  >
                    <Plus className="mr-1 h-3 w-3" />
                    Adicionar
                  </Button>
                </div>
                {form.components.buttons.map((button, index) => (
                  <div key={index} className="flex flex-wrap items-center gap-2 rounded-md border p-2">
                    <Select value={button.type} onValueChange={(value) => updateButton(index, { type: value as TemplateButton['type'] })}>
                      <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="QUICK_REPLY">Resposta rápida</SelectItem>
                        <SelectItem value="URL">Link</SelectItem>
                        <SelectItem value="PHONE_NUMBER">Telefone</SelectItem>
                        <SelectItem value="COPY_CODE">Copiar código</SelectItem>
                      </SelectContent>
                    </Select>
                    <Input
                      className="w-32"
                      value={button.text}
                      onChange={(event) => updateButton(index, { text: event.target.value })}
                      placeholder="Texto do botão"
                      maxLength={TEMPLATE_LIMITS.buttonText}
                    />
                    {button.type === 'URL' && (
                      <Input
                        className="min-w-[180px] flex-1"
                        value={button.url ?? ''}
                        onChange={(event) => updateButton(index, { url: event.target.value })}
                        placeholder="https://mv.adv.br/caso/{{1}}"
                      />
                    )}
                    {button.type === 'URL' && (
                      <span className="basis-full text-[11px] text-muted-foreground">
                        Sufixo dinâmico opcional: termine a URL com {'{{1}}'} para preencher o link por contato.
                      </span>
                    )}
                    {button.type === 'PHONE_NUMBER' && (
                      <Input
                        className="w-40"
                        value={button.phoneNumber ?? ''}
                        onChange={(event) => updateButton(index, { phoneNumber: event.target.value })}
                        placeholder="+55 61 90000-0000"
                      />
                    )}
                    <Button
                      size="icon"
                      variant="ghost"
                      className="text-red-600"
                      onClick={() =>
                        setForm((current) => ({
                          ...current,
                          components: { ...current.components, buttons: current.components.buttons.filter((_, i) => i !== index) },
                        }))
                      }
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
                {bodyNeedsQuickReply && (
                  <Alert className="border-sky-300 bg-sky-50 py-2 dark:border-sky-800 dark:bg-sky-950/30">
                    <SmilePlus className="h-4 w-4 text-sky-700 dark:text-sky-300" />
                    <AlertTitle className="text-xs">Sugestão para follow-up/reativação</AlertTitle>
                    <AlertDescription className="flex flex-wrap items-center gap-2 text-xs">
                      Uma resposta rápida facilita a retomada sem exigir que a pessoa digite.
                      <Button type="button" size="sm" variant="outline" onClick={() => setForm((current) => ({ ...current, components: { ...current.components, buttons: [{ type: 'QUICK_REPLY', text: 'Quero continuar' }] } }))}>
                        Adicionar “Quero continuar”
                      </Button>
                    </AlertDescription>
                  </Alert>
                )}
              </div>

              {inlineIssues.length > 0 && (
                <Alert variant="destructive">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertTitle>Antes de salvar, ajuste:</AlertTitle>
                  <AlertDescription>
                    <ul className="list-disc space-y-0.5 pl-4">
                      {inlineIssues.map((issue, index) => (
                        <li key={index}>{issue}</li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              )}
            </div>

            <div className="space-y-2">
              <Label>Prévia do balão no WhatsApp</Label>
              <WhatsAppBubblePreview {...form.components} />
              <p className="text-xs text-muted-foreground">
                A submissão real à Meta só é liberada quando as entregas externas estão habilitadas neste
                ambiente. Até lá, o rascunho fica marcado como "preparado para a Meta".
              </p>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setBuilderOpen(false)}>Cancelar</Button>
            <Button
              onClick={() => (editingId ? updateMutation.mutate() : createMutation.mutate())}
              disabled={
                inlineIssues.length > 0 ||
                !form.whatsappConnectionId ||
                createMutation.isPending ||
                updateMutation.isPending
              }
            >
              {editingId ? 'Salvar alterações' : 'Salvar rascunho'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function buildComponentsFromMeta(components: Array<Record<string, unknown>>): TemplateComponentsInput {
  const header = components.find((c) => (c.type as string)?.toUpperCase() === 'HEADER');
  const body = components.find((c) => (c.type as string)?.toUpperCase() === 'BODY');
  const footer = components.find((c) => (c.type as string)?.toUpperCase() === 'FOOTER');
  const buttonsComponent = components.find((c) => (c.type as string)?.toUpperCase() === 'BUTTONS');
  const rawButtons = (buttonsComponent?.buttons as Array<Record<string, unknown>>) || [];

  return {
    headerType: (header?.format as TemplateComponentsInput['headerType']) ?? 'NONE',
    headerText: (header?.text as string) ?? '',
    headerExample: (((header?.example as { header_text?: string[] } | undefined)?.header_text ?? [])[0]) ?? '',
    bodyText: (body?.text as string) ?? '',
    bodyExamples: (((body?.example as { body_text?: string[][] } | undefined)?.body_text ?? [])[0]) ?? [],
    footerText: (footer?.text as string) ?? '',
    buttons: rawButtons.map((button) => ({
      type: (button.type as TemplateButton['type']) ?? 'QUICK_REPLY',
      text: (button.text as string) ?? '',
      url: (button.url as string) ?? null,
      phoneNumber: (button.phone_number as string) ?? null,
    })),
  };
}
