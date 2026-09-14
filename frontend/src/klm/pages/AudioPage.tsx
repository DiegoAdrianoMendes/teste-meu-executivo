import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  Bot,
  Check,
  CircleAlert,
  FileAudio,
  FileSearch,
  Headphones,
  LockKeyhole,
  Mic,
  Radio,
  Save,
  Send,
  Sparkles,
} from 'lucide-react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { KlmMetricCard, KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { klmApi, type KlmMessage } from '@/klm/api';
import { trustedAudioSource } from '@/klm/audio-media';
import { useAppStore } from '@/store/app-context';

function metadataNumber(message: KlmMessage | undefined, keys: string[]) {
  if (!message) return null;
  for (const key of keys) {
    const value = message.messageMetadata[key];
    if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return value;
  }
  return null;
}

function formatDuration(seconds: number | null) {
  if (seconds === null) return 'duração não informada';
  const rounded = Math.round(seconds);
  return `${String(Math.floor(rounded / 60)).padStart(2, '0')}:${String(rounded % 60).padStart(2, '0')}`;
}

function metadataText(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

type AudioPageProps = {
  embedded?: boolean;
  threadId?: string | null;
  matterId?: string | null;
  onOpenSources?: () => void;
};

export default function AudioPage({
  embedded = false,
  threadId: embeddedThreadId = null,
  matterId: embeddedMatterId = null,
  onOpenSources,
}: AudioPageProps = {}) {
  const { can, state } = useAppStore();
  const canSendMessages = can('mensagens', 'send_message');
  const canManageIntegrations = can('integrations', 'manage');
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const threadsQuery = useQuery({ queryKey: ['klm', 'threads'], queryFn: klmApi.threads });
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel });
  const [transcription, setTranscription] = useState(true);
  const [summary, setSummary] = useState(true);
  const [voiceReply, setVoiceReply] = useState(false);
  const [durationSeconds, setDurationSeconds] = useState(60);
  const [transcriptDraft, setTranscriptDraft] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const requestedThreadId = embeddedThreadId ?? searchParams.get('thread');
  const requestedMatterId = embeddedMatterId ?? searchParams.get('matter');
  const requestedThread = threadsQuery.data?.find((thread) => thread.id === requestedThreadId);
  const audioThread = useMemo(() => {
    const threads = threadsQuery.data ?? [];
    if (requestedThreadId) {
      if (!requestedThread || (requestedMatterId && requestedThread.matterId !== requestedMatterId)) return undefined;
      return requestedThread;
    }
    if (requestedMatterId) {
      const matterThreads = threads.filter((thread) => thread.matterId === requestedMatterId);
      return matterThreads.find((thread) => thread.primaryChannel === 'audio') ?? matterThreads.at(0);
    }
    return undefined;
  }, [requestedMatterId, requestedThread, requestedThreadId, threadsQuery.data]);
  const contextMatterId = requestedMatterId ?? audioThread?.matterId ?? null;
  const matter = funnelQuery.data?.cards.find((card) => card.id === contextMatterId);
  const messagesQuery = useQuery({
    queryKey: ['klm', 'messages', audioThread?.id],
    queryFn: () => klmApi.messages(audioThread!.id),
    enabled: Boolean(audioThread),
  });
  const intentsQuery = useQuery({
    queryKey: ['klm', 'external-intents'],
    queryFn: klmApi.externalIntents,
    enabled: Boolean(audioThread),
  });
  const transcript = useMemo(() => [...(messagesQuery.data ?? [])]
    .sort((left, right) => left.occurredAt.localeCompare(right.occurredAt))
    .slice(-20), [messagesQuery.data]);
  const audioSource = useMemo(() => {
    for (const message of [...transcript].reverse()) {
      const value = message.messageMetadata.audioUrl ?? message.messageMetadata.mediaUrl;
      const source = trustedAudioSource(value);
      if (source) return source;
    }
    return null;
  }, [transcript]);
  const latestAudioRequest = useMemo(() => [...transcript].reverse().find((message) => (
    message.messageMetadata.source === 'operator_audio_gate'
    && typeof message.messageMetadata.requestCount === 'number'
  )), [transcript]);
  const persistedRequestCount = metadataNumber(latestAudioRequest, ['requestCount']) ?? 0;
  const latestVoiceIntent = useMemo(() => [...(intentsQuery.data ?? [])]
    .filter((intent) => intent.actionType === 'voice_assist' && intent.payload.threadId === audioThread?.id)
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt))[0], [audioThread?.id, intentsQuery.data]);
  const persistedGatePassed = latestAudioRequest?.messageMetadata.voiceGatePassed === true || Boolean(latestVoiceIntent);
  const duration = formatDuration(metadataNumber(
    [...transcript].reverse().find((message) => metadataNumber(message, ['durationSeconds', 'audioDurationSeconds']) !== null),
    ['durationSeconds', 'audioDurationSeconds'],
  ));
  const contactLabel = matter?.name
    ?? metadataText(audioThread?.threadMetadata.contactName)
    ?? metadataText(audioThread?.threadMetadata.clientName)
    ?? 'Atendimento sem card vinculado';
  const subjectLabel = (matter?.subject ? matter.subject.replaceAll('_', ' ') : null)
    ?? audioThread?.subject
    ?? audioThread?.objectName
    ?? 'Assunto não registrado';
  const ownerLabel = audioThread?.ownerAgentKey?.replaceAll('_', ' ') ?? 'responsável não atribuído';
  const isTranscribed = transcript.length > 0 || latestAudioRequest?.messageMetadata.transcriptionEnabled === true;
  const controls = [
    ['Transcrição vinculada', `${transcript.length} mensagem(ns) no atendimento`, transcript.length > 0],
    ['Documentos do caso', matter ? `${matter.documents} documento(s) no card` : 'Atendimento sem card vinculado', Boolean(matter?.documents)],
    ['Próxima ação', matter?.microStatus ? matter.microStatus.replaceAll('_', ' ') : 'Definir no card do cliente', Boolean(matter?.microStatus)],
    ['Escalar humano', audioThread?.status === 'waiting_human' ? 'Aguardando humano' : 'Não solicitado', audioThread?.status === 'waiting_human'],
  ] as const;

  const audioMutation = useMutation({
    mutationFn: () => {
      if (!audioThread) throw new Error('Atendimento local indisponível');
      if (!transcriptDraft.trim()) throw new Error('Transcrição local obrigatória');
      return klmApi.audioRequest({
        threadId: audioThread.id,
        transcript: transcriptDraft.trim(),
        durationSeconds,
        summary: summary ? `Pedido de áudio registrado para revisão: ${transcriptDraft.trim().slice(0, 320)}` : undefined,
        transcriptionEnabled: transcription,
        summaryEnabled: summary,
        voiceReplyRequested: voiceReply,
      });
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'messages', audioThread?.id] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'threads'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'external-intents'] });
      setTranscriptDraft('');
      setNotice(result.gatePassed
        ? `Gate confirmado. Intenção ElevenLabs registrada como ${result.externalIntent?.status ?? 'bloqueada'}, sem chamada ao provedor.`
        : 'Primeiro pedido registrado com transcrição; nenhuma intenção de voz externa foi criada.');
    },
    onError: () => setNotice('Não foi possível registrar o pedido agora. Nenhum provedor foi chamado.'),
  });

  const voiceIntentMutation = useMutation({
    mutationFn: (decision: 'approve' | 'reject') => {
      if (!latestVoiceIntent) throw new Error('Intenção de voz indisponível');
      return decision === 'approve'
        ? klmApi.approveExternalIntentLocally(latestVoiceIntent.id)
        : klmApi.rejectExternalIntentLocally(latestVoiceIntent.id);
    },
    onSuccess: (intent) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'external-intents'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
      setNotice(intent.status === 'approved_local'
        ? 'Intenção aprovada apenas no ambiente local. Nenhum áudio foi enviado ao provedor.'
        : 'Intenção de voz cancelada. Nenhum áudio foi enviado.');
    },
    onError: () => setNotice('Não foi possível registrar a decisão local sobre a intenção de voz.'),
  });

  const evidenceMutation = useMutation({
    mutationFn: () => {
      if (!audioThread) throw new Error('Atendimento local indisponível');
      return klmApi.sendInternalMessage(
        audioThread.id,
        'Evidência de áudio preservada localmente: transcrição, autoria e resumo vinculados ao atendimento.',
        state?.currentUser?.name?.trim() || state?.currentUser?.email || 'Usuário do workspace',
        { source: 'voice_agent' },
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'messages', audioThread?.id] });
      setNotice('Evidência de áudio registrada como nota interna e vinculada ao atendimento local.');
    },
    onError: () => setNotice('Não foi possível registrar a evidência no atendimento local.'),
  });

  const contextParams = new URLSearchParams();
  if (contextMatterId) contextParams.set('matter', contextMatterId);
  if (audioThread?.id) contextParams.set('thread', audioThread.id);
  const contextQuery = contextParams.toString();
  const conversationsPath = `/app/conversas${contextQuery ? `?${contextQuery}` : ''}`;

  const openSources = () => {
    if (onOpenSources) {
      onOpenSources();
      return;
    }
    if (matter) {
      const params = new URLSearchParams(contextParams);
      params.set('matter', matter.id);
      params.set('panel', 'rag');
      navigate(`/app/leads?${params.toString()}`);
    }
    else setNotice('Aguardando o vínculo do áudio com um card para abrir as fontes do RAG.');
  };

  return (
    <div className={embedded ? 'space-y-4' : 'mx-auto max-w-[1220px] space-y-5'}>
      {!embedded && <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"><div><p className="text-2xs font-semibold uppercase text-[#0057d8]">Meu Executivo / Operação jurídica</p><h1 className="mt-2 text-2xl font-semibold">Atendimento por áudio</h1><p className="mt-1 max-w-3xl text-xs leading-4 text-muted-foreground">Voz assistida somente quando o lead insiste; transcrição, resumo e humano permanecem no controle.</p></div><div className="flex flex-wrap items-center gap-2"><KlmPill tone="gold">ElevenLabs: conector bloqueado</KlmPill><KlmPill tone={persistedGatePassed ? 'green' : 'gold'}>{persistedRequestCount > 0 ? `${persistedGatePassed ? 'gatilho confirmado' : 'aguardando insistência'}: ${persistedRequestCount} pedido${persistedRequestCount > 1 ? 's' : ''}` : 'sem pedido registrado'}</KlmPill><Button asChild variant="outline" className="h-9 text-xs"><Link to={conversationsPath}><ArrowLeft className="mr-2 h-3.5 w-3.5" />Voltar à conversa</Link></Button></div></header>}
      {embedded && <div className="flex flex-wrap items-center justify-between gap-2 border border-primary/30 bg-primary/5 p-3"><div><strong className="block text-xs">Áudio vinculado a {contactLabel}</strong><span className="mt-1 block text-2xs text-muted-foreground">Transcrição, gate e evidência permanecem anexados a esta conversa.</span></div><div className="flex flex-wrap gap-2"><KlmPill tone="gold">conector externo bloqueado</KlmPill><KlmPill tone={persistedGatePassed ? 'green' : 'gold'}>{persistedGatePassed ? 'gate confirmado' : 'gate pendente'}</KlmPill></div></div>}

      {notice && <div className="flex items-center justify-between gap-3 rounded-lg border border-primary/35 bg-primary/10 px-3 py-2 text-2xs"><span>{notice}</span><Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setNotice(null)}>Fechar</Button></div>}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4"><KlmMetricCard label="Pedidos por voz" value={String(persistedRequestCount)} delta="último registro persistido" trend="neutral" /><KlmMetricCard label="Gate de insistência" value={persistedGatePassed ? 'Aprovado' : 'Pendente'} delta="mínimo 2 pedidos" trend={persistedGatePassed ? 'up' : 'neutral'} /><KlmMetricCard label="Mensagens vinculadas" value={String(transcript.length)} delta="registrado" trend="up" /><KlmMetricCard label="Arquivo de áudio" value={audioSource ? 'Disponível' : 'Ausente'} delta="sem reprodução simulada" trend="neutral" /></div>

      <div className="grid gap-4 xl:grid-cols-[1.15fr_.85fr]">
        <KlmPanel tone="black" className="overflow-hidden">
          <div className="border-b border-white/10 p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2 text-2xs font-semibold uppercase text-primary"><Radio className="h-3.5 w-3.5" />Sessão assistida local</div><h2 className="mt-2 text-lg font-semibold">{contactLabel} · {subjectLabel}</h2><p className="mt-1 text-2xs text-white/50">{audioThread?.primaryChannel === 'audio' ? 'Canal de áudio' : 'WhatsApp áudio'} · {duration} · {ownerLabel}</p></div><KlmPill tone={isTranscribed ? 'green' : 'muted'}>{isTranscribed ? 'transcrito' : 'sem transcrição'}</KlmPill></div></div>
          <div className="p-5"><div className="flex min-h-28 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] px-5">{audioSource ? <audio className="w-full" controls preload="none" src={audioSource}>Seu navegador não suporta reprodução de áudio.</audio> : <div className="text-center"><FileAudio className="mx-auto h-6 w-6 text-primary" /><p className="mt-3 text-2xs text-white/60">Nenhum arquivo de áudio está vinculado às mensagens desta conversa.</p><p className="mt-1 text-2xs text-white/35">A página não fabrica duração, onda ou reprodução.</p></div>}</div><div className="mt-4 flex justify-end"><Button variant="outline" size="sm" className="h-9 border-white/15 bg-transparent text-2xs text-white hover:bg-white/10" title={!canSendMessages ? 'Permissão mensagens.send_message necessária' : 'Salvar evidência local'} disabled={!canSendMessages || !audioThread || evidenceMutation.isPending} onClick={() => evidenceMutation.mutate()}><Save className="mr-2 h-3.5 w-3.5" />Salvar evidência</Button></div></div>
          <div className="border-t border-white/10 p-5"><div className="mb-4 flex items-center justify-between"><div><h3 className="text-sm font-medium">Transcrição e histórico</h3><p className="mt-1 text-2xs text-white/45">Mensagens persistidas com autoria e horário reais</p></div><KlmPill tone="muted">Salvo</KlmPill></div><div className="space-y-3">{messagesQuery.isPending && <p className="text-2xs text-white/45">Carregando mensagens...</p>}{transcript.map((line) => <div key={line.id} className="grid grid-cols-[92px_1fr] gap-3 text-2xs"><span className="text-white/40"><strong className={line.authorType === 'client' ? 'block text-white' : 'block text-primary'}>{line.authorLabel}</strong>{new Date(line.occurredAt).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}</span><div><p className="whitespace-pre-wrap leading-5 text-white/70">{line.body}</p>{line.messageMetadata.source === 'operator_audio_gate' && <p className="mt-1 text-2xs leading-4 text-white/40">Fala atribuída a {metadataText(line.messageMetadata.spokenByLabel) ?? 'interlocutor não identificado'}; transcrição registrada por {line.authorLabel}.</p>}</div></div>)}{!messagesQuery.isPending && !transcript.length && <p className="text-2xs text-white/45">Nenhuma mensagem persistida nesta conversa.</p>}</div></div>
        </KlmPanel>

        <div className="space-y-4">
          <KlmPanel className="p-4"><KlmSectionTitle title="Controle do Meu Executivo" description="Extração, próximos passos e gate humano." action={<Bot className="h-4 w-4 text-[#0057d8]" />} /><div className="mt-4 space-y-2">{controls.map(([label, detail, done]) => <div key={String(label)} className="flex items-start gap-3 rounded-lg border p-3">{done ? <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" /> : <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-primary" />}<div><strong className="block text-2xs">{label}</strong><span className="mt-1 block text-2xs text-muted-foreground">{detail}</span></div></div>)}</div></KlmPanel>
          <KlmPanel className="p-4"><KlmSectionTitle title="Pipeline de áudio" description="A reiteração é contada pelo histórico persistido deste atendimento." /><div className="mt-4 rounded-lg border px-3 py-2 text-2xs"><span className="font-semibold">Próximo registro: </span>{persistedRequestCount === 0 ? 'primeiro pedido' : `reiteração ${persistedRequestCount + 1}`}</div><div className="mt-4 grid gap-3 sm:grid-cols-[1fr_140px]"><div><label className="block text-2xs font-semibold" htmlFor="audio-transcript">Transcrição recebida</label><Textarea id="audio-transcript" value={transcriptDraft} onChange={(event) => setTranscriptDraft(event.target.value)} disabled={!canSendMessages} className="mt-2 min-h-24 text-xs" placeholder="Cole ou revise a transcrição do áudio recebido antes de registrar o gate." /></div><div><label className="block text-2xs font-semibold" htmlFor="audio-duration">Duração (segundos)</label><Input id="audio-duration" type="number" min="1" max="3600" step="1" value={durationSeconds} onChange={(event) => setDurationSeconds(Math.min(3600, Math.max(1, Number(event.target.value) || 1)))} disabled={!canSendMessages} className="mt-2" /></div></div><div className="mt-3 divide-y rounded-lg border">{[
            { label: 'Transcrever', detail: 'Áudio para texto', value: transcription, set: setTranscription, icon: Headphones },
            { label: 'Resumo no CRM', detail: 'Dados e pendências', value: summary, set: setSummary, icon: Sparkles },
            { label: 'Responder por voz', detail: 'Sempre manual nesta fase', value: voiceReply, set: setVoiceReply, icon: Mic },
          ].map((item) => <div key={item.label} className="flex items-center gap-3 p-3"><item.icon className="h-4 w-4 text-[#0057d8]" /><span className="flex-1"><strong className="block text-2xs">{item.label}</strong><span className="mt-1 block text-2xs text-muted-foreground">{item.detail}</span></span><Switch checked={item.value} onCheckedChange={item.set} disabled={!canSendMessages} aria-label={`Ativar ${item.label}`} /></div>)}</div></KlmPanel>
          <KlmPanel className="p-4"><div className="flex items-start gap-3"><LockKeyhole className="mt-0.5 h-5 w-5 text-[#0057d8]" /><div><h3 className="text-sm font-semibold">Gate de voz externa</h3><p className="mt-1 text-2xs leading-4 text-muted-foreground">A transcrição e a intenção ficam persistidas. Credencial, voz, retenção e entrega externa continuam bloqueadas.</p>{latestVoiceIntent && <p className="mt-2 text-2xs font-semibold text-[#0057d8]">Intenção local: {latestVoiceIntent.status.replaceAll('_', ' ')}</p>}</div></div><div className="mt-4 grid grid-cols-2 gap-2"><Button variant="outline" className="text-xs" onClick={openSources} disabled={threadsQuery.isPending || !matter}><FileSearch className="mr-2 h-3.5 w-3.5" />{threadsQuery.isPending ? 'Carregando fontes' : 'Abrir fontes'}</Button><Button onClick={() => audioMutation.mutate()} disabled={!canSendMessages || !audioThread || !transcriptDraft.trim() || audioMutation.isPending} title={!canSendMessages ? 'Permissão mensagens.send_message necessária' : 'Registrar gate de áudio'} className="text-xs"><Save className="mr-2 h-3.5 w-3.5" />{audioMutation.isPending ? 'Registrando' : 'Registrar gate'}</Button></div>{latestVoiceIntent && !['approved_local', 'cancelled'].includes(latestVoiceIntent.status) && <div className="mt-2 grid grid-cols-2 gap-2"><Button variant="outline" className="text-xs" disabled={!canManageIntegrations || voiceIntentMutation.isPending} title={!canManageIntegrations ? 'Permissão integrations.manage necessária' : 'Rejeitar intenção local'} onClick={() => voiceIntentMutation.mutate('reject')}>Rejeitar intenção</Button><Button className="text-xs" disabled={!canManageIntegrations || voiceIntentMutation.isPending} title={!canManageIntegrations ? 'Permissão integrations.manage necessária' : 'Aprovar intenção apenas localmente'} onClick={() => voiceIntentMutation.mutate('approve')}>Aprovar localmente</Button></div>}<Button disabled className="mt-2 w-full text-xs" title="Envio de voz em breve: depende de API externa de voz autorizada" aria-label="Envio de voz em breve: depende de API externa de voz autorizada"><Send className="mr-2 h-3.5 w-3.5" />Enviar voz: aguardando API autorizada</Button></KlmPanel>
        </div>
      </div>
    </div>
  );
}
