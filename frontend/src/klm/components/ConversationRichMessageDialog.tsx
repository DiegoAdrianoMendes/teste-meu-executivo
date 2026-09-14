import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2,
  FileText,
  FileUp,
  LayoutList,
  Loader2,
  MessageSquareText,
  MousePointerClick,
  Paperclip,
  Plus,
  Route,
  Send,
  Trash2,
  Workflow,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { KlmPill } from '@/klm/components/Primitives';
import {
  klmApi,
  sha256File,
  type KlmDocument,
  type KlmMessage,
  type KlmOmnichannelContext,
  type KlmOmnichannelMessageRoute,
  type KlmRichMessageCommand,
  type KlmRichMessageContentType,
} from '@/klm/api';
import { cn } from '@/lib/utils';
import { fetchTemplatePicker } from '@/services/campaigns';
import type { TemplatePickerResponse } from '@/types';

type RichMode = 'media' | 'template' | 'buttons' | 'list' | 'flow';
type RichRoute = KlmOmnichannelContext['channels'][number];
type ReplyButton = { id: string; title: string };
const KNOWN_TEMPLATE_LANGUAGES = ['pt_BR', 'en_US', 'es'];
type ListRow = { id: string; title: string; description: string };

const modeDefinitions: Array<{
  id: RichMode;
  label: string;
  icon: typeof Paperclip;
  whatsappOnly?: boolean;
}> = [
  { id: 'media', label: 'Arquivo', icon: Paperclip },
  { id: 'template', label: 'Modelo oficial', icon: MessageSquareText, whatsappOnly: true },
  { id: 'buttons', label: 'Botões', icon: MousePointerClick, whatsappOnly: true },
  { id: 'list', label: 'Lista', icon: LayoutList, whatsappOnly: true },
  { id: 'flow', label: 'WhatsApp Flow', icon: Workflow, whatsappOnly: true },
];

const channelLabels: Record<string, string> = {
  whatsapp: 'WhatsApp',
  instagram: 'Instagram',
  messenger: 'Messenger',
  facebook: 'Facebook',
  tiktok: 'TikTok',
  email: 'E-mail',
  webchat: 'Chat do site',
  audio: 'Áudio',
};

function capabilityState(route: RichRoute | null | undefined, mode: RichMode) {
  if (!route) return { available: false, reason: 'Escolha uma rota endereçável antes de montar o conteúdo.' };
  if (mode !== 'media' && route.channel !== 'whatsapp') {
    return { available: false, reason: 'Este formato interativo exige uma rota WhatsApp.' };
  }
  const required = mode === 'template'
    ? 'templates'
    : mode === 'flow'
      ? 'flows'
      : mode === 'media' && route.channel === 'whatsapp'
        ? 'media'
        : 'outbound_messages';
  const capabilities = route.capabilities ?? {};
  const raw = capabilities[required]
    ?? (mode === 'media' ? capabilities.outbound_messages : undefined)
    ?? capabilities.messages;
  if (typeof raw === 'boolean') {
    return raw
      ? { available: true, reason: 'Capacidade declarada pela rota.' }
      : { available: false, reason: `A capacidade ${required.replaceAll('_', ' ')} está desativada.` };
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { available: false, reason: `A rota não declarou a capacidade ${required.replaceAll('_', ' ')}.` };
  }
  const value = raw as Record<string, unknown>;
  if (value.supported === false) return { available: false, reason: String(value.note || 'Formato não suportado por esta rota.') };
  if (value.enabled === false) return { available: false, reason: String(value.note || 'Capacidade desativada nesta rota.') };
  return { available: true, reason: String(value.note || 'Capacidade declarada pela rota.') };
}

function mediaType(document: KlmDocument) {
  const mime = (document.mimeType ?? '').toLowerCase();
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('audio/')) return 'audio';
  if (mime.startsWith('video/')) return 'video';
  return 'document';
}

function routeLabel(route: RichRoute | null | undefined) {
  if (!route) return 'Sem rota selecionada';
  const transport = route.transportMode === 'coexistence'
    ? 'Cloud API + App'
    : route.transportMode === 'cloud_api'
      ? 'Cloud API'
      : route.transportMode?.replaceAll('_', ' ') || 'local';
  return `${channelLabels[route.channel] ?? route.channel} · ${transport} · ${route.connectionName ?? route.connectionKey}`;
}

function cleanIdentifier(value: string, fallback: string) {
  const normalized = value.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '');
  return normalized || fallback;
}

export function ConversationRichMessageDialog({
  open,
  onOpenChange,
  threadId,
  matterId,
  route,
  routeCommand,
  initialBody,
  authorLabel,
  canEdit,
  externalGateOpen,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  threadId: string | null | undefined;
  matterId: string | null | undefined;
  route: RichRoute | null | undefined;
  routeCommand: KlmOmnichannelMessageRoute;
  initialBody: string;
  authorLabel: string;
  canEdit: boolean;
  externalGateOpen: boolean;
  onCreated: (message: KlmMessage, summary: string) => void;
}) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<RichMode>('media');
  const [body, setBody] = useState(initialBody);
  const [header, setHeader] = useState('');
  const [footer, setFooter] = useState('');
  const [selectedDocumentId, setSelectedDocumentId] = useState('');
  const [uploadedDocument, setUploadedDocument] = useState<KlmDocument | null>(null);
  const [templateName, setTemplateName] = useState('');
  const [templateLanguage, setTemplateLanguage] = useState('pt_BR');
  const [selectedTemplateId, setSelectedTemplateId] = useState('');
  const [templateParameters, setTemplateParameters] = useState<string[]>([]);
  const [replyButtons, setReplyButtons] = useState<ReplyButton[]>([
    { id: 'agendar', title: 'Agendar' },
    { id: 'falar_advogado', title: 'Falar com advogado' },
  ]);
  const [listButtonText, setListButtonText] = useState('Ver opções');
  const [listSectionTitle, setListSectionTitle] = useState('Como deseja continuar?');
  const [listRows, setListRows] = useState<ListRow[]>([
    { id: 'agendar', title: 'Agendar reunião', description: 'Escolher um horário disponível' },
    { id: 'enviar_documentos', title: 'Enviar documentos', description: 'Continuar a análise do caso' },
  ]);
  const [flowId, setFlowId] = useState('');
  const [flowToken, setFlowToken] = useState('');
  const [flowCta, setFlowCta] = useState('Preencher');
  const [flowAction, setFlowAction] = useState<'navigate' | 'data_exchange'>('navigate');
  const [flowMode, setFlowMode] = useState<'published' | 'draft'>('published');
  const [flowScreen, setFlowScreen] = useState('');
  const [notice, setNotice] = useState<string | null>(null);

  const matterQuery = useQuery({
    queryKey: ['klm', 'matter', matterId],
    queryFn: () => klmApi.matter(matterId!),
    enabled: open && Boolean(matterId),
    retry: false,
  });
  // V27: o nome do modelo era só texto livre; a lista aprovada é a mesma do "Escolher modelo"
  // (mesma chave de cache). Só roda na aba de modelo, em rota WhatsApp.
  const pickerQuery = useQuery<TemplatePickerResponse>({
    queryKey: ['whatsapp-template-picker', threadId, matterId ?? null],
    queryFn: () => fetchTemplatePicker({ threadId, matterId }),
    enabled: open && mode === 'template' && route?.channel === 'whatsapp',
    staleTime: 30_000,
    retry: false,
  });
  const approvedTemplates = useMemo(
    () => (pickerQuery.data?.cards ?? []).filter((card) => String(card.status ?? '').toLowerCase() === 'approved'),
    [pickerQuery.data?.cards],
  );
  const documents = useMemo(() => {
    const existing = (matterQuery.data?.documents ?? []).map((document) => ({
      ...document,
      matterId: matterId ?? '',
      url: null,
      size: document.size ?? null,
      mimeType: document.mimeType ?? null,
    } satisfies KlmDocument));
    if (!uploadedDocument || existing.some((item) => item.id === uploadedDocument.id)) return existing;
    return [uploadedDocument, ...existing];
  }, [matterId, matterQuery.data?.documents, uploadedDocument]);
  const selectedDocument = documents.find((item) => item.id === selectedDocumentId) ?? null;
  const eligibility = capabilityState(route, mode);

  useEffect(() => {
    if (!open) return;
    setMode('media');
    setBody(initialBody);
    setHeader('');
    setFooter('');
    setSelectedDocumentId('');
    setUploadedDocument(null);
    setNotice(null);
  }, [initialBody, open, threadId]);

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      if (!matterId) throw new Error('Vincule a conversa a um caso antes de anexar um arquivo.');
      const checksumSha256 = await sha256File(file);
      const upload = await klmApi.createDocumentUpload(matterId, {
        name: file.name,
        checksumSha256,
        docType: 'outro',
        status: 'aguardando',
        fileSize: String(file.size),
        mimeType: file.type || 'application/octet-stream',
      });
      return klmApi.completeDocumentUpload(matterId, upload, file);
    },
    onSuccess: (document) => {
      setUploadedDocument(document);
      setSelectedDocumentId(document.id);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'matter', matterId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'funnel'] });
      setNotice('Arquivo armazenado no caso e selecionado para esta mensagem.');
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível armazenar o arquivo.'),
  });

  const buildCommand = (): { command: KlmRichMessageCommand; summary: string } => {
    const cleanBody = body.trim();
    if (!eligibility.available) throw new Error(eligibility.reason);
    if (!route) throw new Error('Escolha uma rota antes de preparar o envio.');

    let contentType: KlmRichMessageContentType;
    let templateId: string | null = null;
    let language: string | null = null;
    let attachments: Array<Record<string, unknown>> = [];
    let structuredContent: Record<string, unknown>;
    let previewBody = cleanBody;

    if (mode === 'media') {
      if (!selectedDocument) throw new Error('Selecione ou envie um arquivo do caso.');
      const kind = mediaType(selectedDocument);
      previewBody = cleanBody || `Arquivo: ${selectedDocument.name}`;
      attachments = [{
        documentId: selectedDocument.id,
        name: selectedDocument.name,
        filename: selectedDocument.name,
        fileType: selectedDocument.mimeType || selectedDocument.type || 'arquivo',
        mediaType: kind,
        type: kind,
        caption: kind === 'audio' ? undefined : cleanBody || undefined,
        source: 'matter_document',
      }];
      structuredContent = {
        kind: 'media',
        type: kind,
        documentId: selectedDocument.id,
        filename: selectedDocument.name,
        caption: kind === 'audio' ? undefined : cleanBody || undefined,
      };
      contentType = 'media';
    } else if (mode === 'template') {
      const name = templateName.trim();
      if (!name) throw new Error('Informe o nome exato do modelo aprovado no provedor.');
      if (!cleanBody) throw new Error('Inclua a prévia humana do conteúdo do modelo.');
      const parameters = templateParameters.map((value) => value.trim()).filter(Boolean);
      structuredContent = {
        kind: 'template',
        name,
        language: templateLanguage,
        components: parameters.length ? [{
          type: 'body',
          parameters: parameters.map((text) => ({ type: 'text', text })),
        }] : [],
      };
      contentType = 'template';
      templateId = name;
      language = templateLanguage;
    } else if (mode === 'buttons') {
      if (!cleanBody) throw new Error('Escreva o texto principal da mensagem.');
      const buttons = replyButtons.map((item, index) => ({
        id: cleanIdentifier(item.id, `opcao_${index + 1}`),
        title: item.title.trim(),
      }));
      if (!buttons.length || buttons.some((item) => !item.title)) throw new Error('Preencha de 1 a 3 botões.');
      structuredContent = { kind: 'reply_buttons', body: cleanBody, header: header.trim() || null, footer: footer.trim() || null, buttons };
      contentType = 'interactive_buttons';
    } else if (mode === 'list') {
      if (!cleanBody) throw new Error('Escreva o texto principal da lista.');
      const rows = listRows.map((item, index) => ({
        id: cleanIdentifier(item.id, `opcao_${index + 1}`),
        title: item.title.trim(),
        description: item.description.trim() || undefined,
      }));
      if (!rows.length || rows.some((item) => !item.title)) throw new Error('Preencha de 1 a 10 opções da lista.');
      structuredContent = {
        kind: 'list',
        body: cleanBody,
        header: header.trim() || null,
        footer: footer.trim() || null,
        buttonText: listButtonText.trim(),
        sections: [{ title: listSectionTitle.trim() || null, rows }],
      };
      contentType = 'interactive_list';
    } else {
      if (!cleanBody) throw new Error('Escreva o texto principal do WhatsApp Flow.');
      if (!flowId.trim() || !flowToken.trim() || !flowCta.trim()) throw new Error('Informe ID, token e chamada do Flow.');
      structuredContent = {
        kind: 'flow',
        body: cleanBody,
        header: header.trim() || null,
        footer: footer.trim() || null,
        flowId: flowId.trim(),
        flowToken: flowToken.trim(),
        flowCta: flowCta.trim(),
        action: flowAction,
        actionPayload: flowScreen.trim() ? { screen: flowScreen.trim() } : {},
        mode: flowMode,
      };
      contentType = 'whatsapp_flow';
    }

    return {
      command: {
        body: previewBody,
        contentType,
        templateId,
        language,
        messageMetadata: {
          attachments,
          structuredContent,
          richComposerMode: mode,
          externalActionsExecuted: 0,
        },
      },
      summary: modeDefinitions.find((item) => item.id === mode)?.label ?? 'Conteúdo rico',
    };
  };

  const sendMutation = useMutation({
    mutationFn: () => {
      if (!threadId || !route) throw new Error('A conversa ainda não possui uma rota endereçável.');
      const built = buildCommand();
      return klmApi.sendRichMessage(
        threadId,
        built.command,
        route.channel,
        authorLabel,
        routeCommand,
      ).then((message) => ({ message, summary: built.summary }));
    },
    onSuccess: ({ message, summary }) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'messages', threadId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'omnichannel-context', threadId] });
      onCreated(message, summary);
      onOpenChange(false);
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível registrar o conteúdo.'),
  });

  const previewRows = mode === 'buttons'
    ? replyButtons.map((item) => item.title.trim()).filter(Boolean)
    : mode === 'list'
      ? listRows.map((item) => item.title.trim()).filter(Boolean)
      : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[94vh] flex-col overflow-hidden p-0 sm:max-w-[960px]">
        <DialogHeader className="border-b border-border px-5 py-4 text-left">
          <div className="mb-1 flex h-9 w-9 items-center justify-center rounded-md bg-[#031121] text-primary dark:bg-white dark:text-[#0057d8]"><MessageSquareText className="h-4 w-4" /></div>
          <DialogTitle>Conteúdo avançado da mensagem</DialogTitle>
          <DialogDescription>Monte arquivo, modelo ou interação na própria conversa. O payload é validado para a rota selecionada antes de entrar no histórico.</DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 scrollbar-thin sm:px-5">
          <div className="mb-4 flex flex-wrap items-center gap-2 border-b border-border pb-3 text-2xs">
            <Route className="h-3.5 w-3.5 text-[#0057d8]" />
            <strong className="min-w-0 truncate">{routeLabel(route)}</strong>
            <KlmPill tone={externalGateOpen ? 'green' : 'gold'}>{externalGateOpen ? 'entrega habilitada' : 'rascunho local'}</KlmPill>
          </div>

          <Tabs value={mode} onValueChange={(value) => { setMode(value as RichMode); setNotice(null); }}>
            <TabsList className="grid h-auto w-full grid-cols-2 gap-1 bg-secondary p-1 sm:grid-cols-5">
              {modeDefinitions.map((item) => {
                const Icon = item.icon;
                const unavailable = item.whatsappOnly && route?.channel !== 'whatsapp';
                return <TabsTrigger key={item.id} value={item.id} disabled={unavailable} title={unavailable ? 'Disponível em rotas WhatsApp' : item.label} className="h-9 gap-1.5 px-2 text-2xs"><Icon className="h-3.5 w-3.5" />{item.label}</TabsTrigger>;
              })}
            </TabsList>

            <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
              <div className="min-w-0 space-y-4">
                {!eligibility.available ? <div role="alert" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-2xs leading-4 text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100">{eligibility.reason}</div> : null}

                <TabsContent value="media" className="mt-0 space-y-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <input ref={fileInputRef} type="file" className="sr-only" accept=".pdf,image/*,audio/*,video/*,.doc,.docx,.txt" onChange={(event) => { const file = event.target.files?.[0]; if (file) uploadMutation.mutate(file); event.currentTarget.value = ''; }} />
                    <Button type="button" variant="outline" onClick={() => fileInputRef.current?.click()} disabled={!canEdit || !matterId || uploadMutation.isPending}>
                      {uploadMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FileUp className="mr-2 h-4 w-4" />}
                      {uploadMutation.isPending ? 'Armazenando' : 'Enviar novo arquivo'}
                    </Button>
                    {!matterId ? <span className="text-2xs text-amber-700">Vincule um caso para usar anexos.</span> : <span className="text-2xs text-muted-foreground">O arquivo entra no card e no RAG antes da mensagem.</span>}
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="rich-document">Arquivo do caso</Label>
                    <Select value={selectedDocumentId} onValueChange={setSelectedDocumentId} disabled={!canEdit || !documents.length}>
                      <SelectTrigger id="rich-document" aria-label="Arquivo do caso para a mensagem"><SelectValue placeholder={matterQuery.isPending ? 'Carregando arquivos' : 'Selecione um arquivo persistido'} /></SelectTrigger>
                      <SelectContent>{documents.map((document) => <SelectItem key={document.id} value={document.id}>{document.name} · {document.mimeType || document.type || 'arquivo'}</SelectItem>)}</SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5"><Label htmlFor="rich-media-caption">Legenda ou contexto</Label><Textarea id="rich-media-caption" value={body} onChange={(event) => setBody(event.target.value)} maxLength={1024} className="min-h-24 resize-y text-xs" placeholder="Explique ao cliente o que está sendo enviado" /></div>
                </TabsContent>

                <TabsContent value="template" className="mt-0 space-y-4">
                  <div className="space-y-1.5">
                    <Label>Modelo aprovado</Label>
                    {pickerQuery.isError ? (
                      <p role="status" aria-label="Erro ao carregar modelos aprovados" className="text-2xs text-destructive">
                        Não foi possível carregar os modelos aprovados.{' '}
                        <button type="button" className="underline" onClick={() => { void pickerQuery.refetch(); }}>Tentar novamente</button>
                      </p>
                    ) : (
                      <Select
                        value={selectedTemplateId}
                        disabled={pickerQuery.isLoading || !approvedTemplates.length}
                        onValueChange={(id) => {
                          const card = approvedTemplates.find((item) => item.id === id);
                          if (!card) return;
                          setSelectedTemplateId(id);
                          setTemplateName(card.name);
                          setTemplateLanguage(card.language || 'pt_BR');
                        }}
                      >
                        <SelectTrigger aria-label="Modelo aprovado">
                          <SelectValue placeholder={pickerQuery.isLoading ? 'Carregando modelos aprovados...' : approvedTemplates.length ? 'Escolha um modelo aprovado' : 'Nenhum modelo aprovado disponível'} />
                        </SelectTrigger>
                        <SelectContent>
                          {approvedTemplates.map((card) => <SelectItem key={card.id} value={card.id}>{card.name} · {card.language}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    )}
                    {pickerQuery.isSuccess && !approvedTemplates.length ? <p className="text-2xs text-muted-foreground">Nenhum modelo aprovado disponível; digite o nome exato abaixo.</p> : null}
                  </div>
                  <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_150px]">
                    <div className="space-y-1.5"><Label htmlFor="rich-template-name">Nome exato do modelo aprovado</Label><Input id="rich-template-name" value={templateName} onChange={(event) => { setTemplateName(event.target.value); setSelectedTemplateId(''); }} placeholder="nome_exato_do_modelo" /></div>
                    <div className="space-y-1.5"><Label>Idioma</Label><Select value={templateLanguage} onValueChange={setTemplateLanguage}><SelectTrigger aria-label="Idioma do modelo"><SelectValue /></SelectTrigger><SelectContent>{!KNOWN_TEMPLATE_LANGUAGES.includes(templateLanguage) ? <SelectItem value={templateLanguage}>{templateLanguage}</SelectItem> : null}<SelectItem value="pt_BR">Português (BR)</SelectItem><SelectItem value="en_US">Inglês (EUA)</SelectItem><SelectItem value="es">Espanhol</SelectItem></SelectContent></Select></div>
                  </div>
                  <div className="space-y-1.5"><Label htmlFor="rich-template-preview">Prévia humana no histórico</Label><Textarea id="rich-template-preview" value={body} onChange={(event) => setBody(event.target.value)} className="min-h-24 resize-y text-xs" placeholder="Texto que permite revisar o conteúdo antes do envio" /></div>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between"><Label>Parâmetros do corpo</Label><Button type="button" variant="outline" size="sm" className="h-7 text-2xs" disabled={templateParameters.length >= 10} onClick={() => setTemplateParameters((current) => [...current, ''])}><Plus className="mr-1 h-3 w-3" />Parâmetro</Button></div>
                    {templateParameters.map((value, index) => <div key={`template-param-${index}`} className="flex gap-2"><Input aria-label={`Parâmetro ${index + 1} do modelo`} value={value} onChange={(event) => setTemplateParameters((current) => current.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} placeholder={`Valor de {{${index + 1}}}`} /><Button type="button" variant="ghost" size="icon" aria-label={`Remover parâmetro ${index + 1}`} onClick={() => setTemplateParameters((current) => current.filter((_, itemIndex) => itemIndex !== index))}><Trash2 className="h-3.5 w-3.5" /></Button></div>)}
                    {!templateParameters.length ? <p className="text-2xs text-muted-foreground">Sem variáveis dinâmicas. O nome e o idioma ainda serão validados pelo provedor na fase de conexão.</p> : null}
                  </div>
                </TabsContent>

                <TabsContent value="buttons" className="mt-0 space-y-4">
                  <CommonInteractiveFields body={body} onBody={setBody} header={header} onHeader={setHeader} footer={footer} onFooter={setFooter} />
                  <div className="space-y-2">
                    <div className="flex items-center justify-between"><Label>Botões de resposta</Label><Button type="button" variant="outline" size="sm" className="h-7 text-2xs" disabled={replyButtons.length >= 3} onClick={() => setReplyButtons((current) => [...current, { id: `opcao_${current.length + 1}`, title: '' }])}><Plus className="mr-1 h-3 w-3" />Botão</Button></div>
                    {replyButtons.map((button, index) => <div key={`reply-button-${index}`} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_36px] gap-2"><Input aria-label={`Título do botão ${index + 1}`} value={button.title} maxLength={20} onChange={(event) => setReplyButtons((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, title: event.target.value } : item))} placeholder="Texto visível" /><Input aria-label={`ID do botão ${index + 1}`} value={button.id} maxLength={256} onChange={(event) => setReplyButtons((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, id: event.target.value } : item))} placeholder="id_interno" /><Button type="button" variant="ghost" size="icon" aria-label={`Remover botão ${index + 1}`} disabled={replyButtons.length === 1} onClick={() => setReplyButtons((current) => current.filter((_, itemIndex) => itemIndex !== index))}><Trash2 className="h-3.5 w-3.5" /></Button></div>)}
                  </div>
                </TabsContent>

                <TabsContent value="list" className="mt-0 space-y-4">
                  <CommonInteractiveFields body={body} onBody={setBody} header={header} onHeader={setHeader} footer={footer} onFooter={setFooter} />
                  <div className="grid gap-3 sm:grid-cols-2"><div className="space-y-1.5"><Label htmlFor="rich-list-button">Texto para abrir</Label><Input id="rich-list-button" value={listButtonText} maxLength={20} onChange={(event) => setListButtonText(event.target.value)} /></div><div className="space-y-1.5"><Label htmlFor="rich-list-section">Título da seção</Label><Input id="rich-list-section" value={listSectionTitle} maxLength={24} onChange={(event) => setListSectionTitle(event.target.value)} /></div></div>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between"><Label>Opções da lista</Label><Button type="button" variant="outline" size="sm" className="h-7 text-2xs" disabled={listRows.length >= 10} onClick={() => setListRows((current) => [...current, { id: `opcao_${current.length + 1}`, title: '', description: '' }])}><Plus className="mr-1 h-3 w-3" />Opção</Button></div>
                    {listRows.map((row, index) => <div key={`list-row-${index}`} className="grid gap-2 rounded-md border border-border p-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_36px]"><div className="space-y-1"><Input aria-label={`Título da opção ${index + 1}`} value={row.title} maxLength={24} onChange={(event) => setListRows((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, title: event.target.value } : item))} placeholder="Título" /><Input aria-label={`ID da opção ${index + 1}`} value={row.id} maxLength={200} onChange={(event) => setListRows((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, id: event.target.value } : item))} placeholder="id_interno" /></div><Input aria-label={`Descrição da opção ${index + 1}`} value={row.description} maxLength={72} onChange={(event) => setListRows((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, description: event.target.value } : item))} placeholder="Descrição opcional" /><Button type="button" variant="ghost" size="icon" aria-label={`Remover opção ${index + 1}`} disabled={listRows.length === 1} onClick={() => setListRows((current) => current.filter((_, itemIndex) => itemIndex !== index))}><Trash2 className="h-3.5 w-3.5" /></Button></div>)}
                  </div>
                </TabsContent>

                <TabsContent value="flow" className="mt-0 space-y-4">
                  <CommonInteractiveFields body={body} onBody={setBody} header={header} onHeader={setHeader} footer={footer} onFooter={setFooter} />
                  <div className="grid gap-3 sm:grid-cols-2"><div className="space-y-1.5"><Label htmlFor="rich-flow-id">ID do Flow</Label><Input id="rich-flow-id" value={flowId} onChange={(event) => setFlowId(event.target.value)} /></div><div className="space-y-1.5"><Label htmlFor="rich-flow-token">Token do Flow</Label><Input id="rich-flow-token" value={flowToken} onChange={(event) => setFlowToken(event.target.value)} /></div><div className="space-y-1.5"><Label htmlFor="rich-flow-cta">Chamada</Label><Input id="rich-flow-cta" value={flowCta} maxLength={20} onChange={(event) => setFlowCta(event.target.value)} /></div><div className="space-y-1.5"><Label htmlFor="rich-flow-screen">Tela inicial opcional</Label><Input id="rich-flow-screen" value={flowScreen} onChange={(event) => setFlowScreen(event.target.value)} placeholder="INTAKE" /></div><div className="space-y-1.5"><Label>Ação</Label><Select value={flowAction} onValueChange={(value) => setFlowAction(value as typeof flowAction)}><SelectTrigger aria-label="Ação do WhatsApp Flow"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="navigate">Abrir tela</SelectItem><SelectItem value="data_exchange">Trocar dados</SelectItem></SelectContent></Select></div><div className="space-y-1.5"><Label>Modo</Label><Select value={flowMode} onValueChange={(value) => setFlowMode(value as typeof flowMode)}><SelectTrigger aria-label="Modo do WhatsApp Flow"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="published">Publicado</SelectItem><SelectItem value="draft">Rascunho</SelectItem></SelectContent></Select></div></div>
                </TabsContent>
              </div>

              <aside className="min-w-0 border-l-0 border-border lg:border-l lg:pl-5" aria-label="Prévia da mensagem avançada">
                <div className="sticky top-0">
                  <div className="flex items-center justify-between"><p className="text-2xs font-semibold uppercase text-muted-foreground">Prévia</p><KlmPill tone="muted">{modeDefinitions.find((item) => item.id === mode)?.label}</KlmPill></div>
                  <div className="mt-3 rounded-md border border-[#1a9948]/40 bg-[#d9fdd3] p-3 text-[#102417] shadow-sm dark:border-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-50">
                    {header.trim() ? <p className="mb-1 text-2xs font-semibold">{header.trim()}</p> : null}
                    <p className="whitespace-pre-wrap text-xs leading-[17px]">{body.trim() || (mode === 'media' && selectedDocument ? `Arquivo: ${selectedDocument.name}` : 'Preencha o conteúdo para visualizar.')}</p>
                    {mode === 'media' && selectedDocument ? <div className="mt-2 flex items-center gap-2 rounded-md border border-current/15 bg-white/60 p-2 text-2xs dark:bg-black/20"><FileText className="h-3.5 w-3.5" /><span className="min-w-0 flex-1 truncate">{selectedDocument.name}</span><span>{mediaType(selectedDocument)}</span></div> : null}
                    {previewRows.length ? <div className="mt-2 space-y-1 border-t border-current/15 pt-2">{previewRows.map((value, index) => <div key={`${mode}-preview-${index}`} className="rounded border border-current/20 bg-white/60 px-2 py-1.5 text-center text-2xs font-semibold dark:bg-black/20">{value}</div>)}</div> : null}
                    {mode === 'template' && templateName.trim() ? <p className="mt-2 text-2xs opacity-70">Modelo: {templateName.trim()} · {templateLanguage}</p> : null}
                    {mode === 'flow' && flowCta.trim() ? <div className="mt-2 rounded border border-current/20 bg-white/60 px-2 py-1.5 text-center text-2xs font-semibold dark:bg-black/20">{flowCta.trim()}</div> : null}
                    {footer.trim() ? <p className="mt-2 text-2xs opacity-65">{footer.trim()}</p> : null}
                  </div>
                  <div className="mt-3 space-y-2 border-t border-border pt-3 text-2xs leading-4 text-muted-foreground">
                    <p className="flex gap-2"><CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#1a9948]" />Limites e estrutura são validados pelo contrato nativo antes de persistir.</p>
                    <p className="flex gap-2"><FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0057d8]" />Arquivos permanecem ligados ao card e disponíveis para o RAG do caso.</p>
                  </div>
                </div>
              </aside>
            </div>
          </Tabs>

          {notice ? <p aria-live="polite" className={cn('mt-4 rounded-md border p-2.5 text-2xs leading-4', notice.includes('armazenado') ? 'border-[#1a9948]/30 bg-[#1a9948]/8' : 'border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100')}>{notice}</p> : null}
        </div>

        <DialogFooter className="border-t border-border px-5 py-4 sm:justify-between">
          <p className="max-w-md text-left text-2xs leading-4 text-muted-foreground">{externalGateOpen ? 'A entrega seguirá a rota oficial e a política de janela aplicável.' : 'Gate local fechado: o registro será criado como rascunho, sem chamada ao provedor.'}</p>
          <div className="flex gap-2"><Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancelar</Button><Button type="button" className="bg-[#031121] text-white hover:bg-[#031121]" disabled={!canEdit || !eligibility.available || sendMutation.isPending} onClick={() => sendMutation.mutate()}>{sendMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Send className="mr-2 h-4 w-4" />}{sendMutation.isPending ? 'Validando' : 'Preparar envio'}</Button></div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CommonInteractiveFields({
  body,
  onBody,
  header,
  onHeader,
  footer,
  onFooter,
}: {
  body: string;
  onBody: (value: string) => void;
  header: string;
  onHeader: (value: string) => void;
  footer: string;
  onFooter: (value: string) => void;
}) {
  return <div className="space-y-3"><div className="grid gap-3 sm:grid-cols-2"><div className="space-y-1.5"><Label htmlFor="rich-header">Cabeçalho opcional</Label><Input id="rich-header" value={header} maxLength={60} onChange={(event) => onHeader(event.target.value)} /></div><div className="space-y-1.5"><Label htmlFor="rich-footer">Rodapé opcional</Label><Input id="rich-footer" value={footer} maxLength={60} onChange={(event) => onFooter(event.target.value)} /></div></div><div className="space-y-1.5"><Label htmlFor="rich-body">Mensagem principal</Label><Textarea id="rich-body" value={body} maxLength={1024} onChange={(event) => onBody(event.target.value)} className="min-h-24 resize-y text-xs" /></div></div>;
}

export default ConversationRichMessageDialog;
