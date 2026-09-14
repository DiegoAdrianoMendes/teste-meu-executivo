import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, Archive, ArrowUpRight, Bot, Check, ChevronRight, CircleAlert, Copy, Crown, Database,
  FileText, LoaderCircle, LockKeyhole, MessageSquarePlus, ShieldCheck,
  PanelRight, Search, Send, Share2, Sparkles, Target, UsersRound, X,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import {
  klmApi,
  type ExecutiveKey,
  type ExecutiveMessage,
  type ExecutiveProfile,
  type ExecutiveReport,
  type ExecutiveShareCandidate,
  type ExecutiveSource,
  type ExecutiveThread,
} from '@/klm/api';
import { useAppStore } from '@/store/app-context';
import { cn } from '@/lib/utils';

const executiveTone: Record<ExecutiveKey, string> = {
  ceo: 'bg-[#111827] text-white',
  coo: 'bg-[#0f766e] text-white',
  cfo: 'bg-[#166534] text-white',
  cro: 'bg-[#9a3412] text-white',
  cpo: 'bg-[#7e22ce] text-white',
  cto: 'bg-[#1d4ed8] text-white',
  cio: 'bg-[#334155] text-white',
  chro: 'bg-[#be185d] text-white',
};

const reportStatusLabel: Record<ExecutiveReport['status'], string> = {
  scheduled: 'Agendado', collecting_data: 'Coletando dados', generating: 'Gerando',
  published: 'Publicado', published_with_gaps: 'Publicado com gaps', partial_failure: 'Parcial',
  failed: 'Falhou', superseded: 'Substituído',
};

const starterQuestions = [
  { label: 'Prioridades da semana', prompt: '@CEO quais decisões merecem minha atenção nesta semana?', icon: Target },
  { label: 'Saúde financeira', prompt: '@CFO quais riscos financeiros precisam de decisão humana agora?', icon: Database },
  { label: 'Gargalos da operação', prompt: '@COO onde a operação está travando e quais evidências sustentam isso?', icon: UsersRound },
];

function formatDate(value: string | null, includeTime = false) {
  if (!value) return 'Sem atividade';
  return new Intl.DateTimeFormat('pt-BR', includeTime
    ? { dateStyle: 'short', timeStyle: 'short' }
    : { dateStyle: 'medium' }).format(new Date(value));
}

function ExecutiveAvatar({ executiveKey, label, className }: { executiveKey: ExecutiveKey; label?: string; className?: string }) {
  return (
    <span className={cn('grid h-9 w-9 shrink-0 place-items-center rounded-lg text-[11px] font-bold', executiveTone[executiveKey], className)}>
      {(label ?? executiveKey).replace('@', '').toUpperCase()}
    </span>
  );
}

function EmptyError({ onRetry }: { onRetry: () => void }) {
  return (
    <KlmPanel className="mx-auto max-w-xl p-8 text-center">
      <CircleAlert className="mx-auto h-7 w-7 text-[#a53e3c]" />
      <h2 className="mt-4 text-base font-semibold">Não foi possível consultar o conselho</h2>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">
        Nenhum executivo ou resposta simulada será exibido como se viesse do runtime real.
      </p>
      <Button variant="outline" className="mt-5" onClick={onRetry}>Tentar novamente</Button>
    </KlmPanel>
  );
}

function SourcePanel({ sources, onClose }: { sources: ExecutiveSource[]; onClose?: () => void }) {
  return (
    <aside className="h-full border-l border-border bg-[#fbfcfe] p-5 dark:bg-card" aria-label="Contexto e fontes">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-[9px] font-semibold uppercase tracking-[.16em] text-[#0057d8]">Lastro da decisão</p>
          <h2 className="mt-1 text-base font-semibold">Contexto e fontes</h2>
        </div>
        {onClose && <Button size="icon" variant="ghost" onClick={onClose} aria-label="Fechar fontes"><X className="h-4 w-4" /></Button>}
      </div>
      <div className="mt-5 rounded-xl border border-[#0057d8]/15 bg-gradient-to-br from-[#0057d8]/10 to-transparent p-3.5">
        <div className="flex items-center gap-2 text-xs font-medium"><Database className="h-3.5 w-3.5 text-[#0057d8]" /> Conta inteira autorizada</div>
        <p className="mt-1 text-2xs leading-4 text-muted-foreground">O acesso seletivo será ativado somente após homologação do motor e do RAG. Esta conversa não amplia permissões.</p>
      </div>
      <div className="mt-4 space-y-3 overflow-y-auto">
        {sources.length === 0 ? (
          <div className="rounded-xl border border-dashed bg-card p-5 text-center"><FileText className="mx-auto h-5 w-5 text-muted-foreground" /><p className="mt-2 text-xs font-medium">Nenhuma fonte selecionada</p><p className="mt-1 text-[10px] leading-4 text-muted-foreground">Abra o lastro de uma resposta para conferir origem, data e atualidade.</p></div>
        ) : sources.map((source, index) => (
          <article key={`${source.sourceType}:${source.sourceRef ?? index}`} className="rounded-xl border bg-card p-3.5 shadow-sm">
            <div className="flex items-start justify-between gap-2">
              <p className="text-xs font-semibold">{source.title}</p>
              {source.stale && <KlmPill tone="gold">Desatualizada</KlmPill>}
            </div>
            <p className="mt-1 text-2xs text-muted-foreground">{source.sourceType} · {source.indexedAt ? `Indexado em ${formatDate(source.indexedAt, true)}` : 'Indexação indisponível'}</p>
            {source.excerpt && <p className="mt-2 line-clamp-5 text-xs leading-5 text-foreground/80">{source.excerpt}</p>}
          </article>
        ))}
      </div>
    </aside>
  );
}

function MessageCard({ message, profile, onSources }: { message: ExecutiveMessage; profile?: ExecutiveProfile; onSources: () => void }) {
  const isHuman = message.authorType === 'human';
  const isSystem = message.authorType === 'system';
  const key = message.executiveKey ?? 'ceo';
  const author = isHuman ? 'Você' : isSystem ? 'Sistema' : profile?.mention ?? `@${key.toUpperCase()}`;
  return (
    <article className={cn('flex gap-3 px-5 py-5', isHuman ? 'bg-[#f7f9fc] dark:bg-secondary/35' : isSystem ? 'bg-amber-50/60 dark:bg-amber-950/10' : 'bg-card')}>
      {isHuman
        ? <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-muted text-xs font-bold">EU</span>
        : isSystem
          ? <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-muted"><CircleAlert className="h-4 w-4" /></span>
          : <ExecutiveAvatar executiveKey={key} />}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold">{author}</span>
          {!isHuman && !isSystem && <span className="text-2xs text-muted-foreground">{profile?.role ?? 'Conselho executivo'}</span>}
          {message.messageKind === 'synthesis' && <Badge variant="outline" className="h-5 text-[9px]">Síntese da rodada</Badge>}
          <time className="text-[10px] text-muted-foreground">{formatDate(message.createdAt, true)}</time>
        </div>
        <div className={cn('mt-2 max-w-3xl whitespace-pre-wrap text-sm leading-6', isSystem && 'text-amber-950 dark:text-amber-100')}>{message.body}</div>
        {!isHuman && !isSystem && (
          <div className="mt-3 flex flex-wrap items-center gap-1">
            <Button size="sm" variant="ghost" className="h-7 px-2 text-2xs" onClick={onSources}><FileText className="mr-1 h-3 w-3" />Ver fontes</Button>
            <Button size="icon" variant="ghost" className="h-7 w-7" aria-label="Copiar resposta" onClick={() => void navigator.clipboard.writeText(message.body)}><Copy className="h-3 w-3" /></Button>
          </div>
        )}
      </div>
    </article>
  );
}

function NewThreadDialog({ open, profiles, onOpenChange, onCreate, pending }: {
  open: boolean;
  profiles: ExecutiveProfile[];
  onOpenChange: (open: boolean) => void;
  onCreate: (payload: { title?: string; mode: 'direct' | 'roundtable'; selectedExecutives: ExecutiveKey[] }) => void;
  pending: boolean;
}) {
  const [title, setTitle] = useState('');
  const [mode, setMode] = useState<'direct' | 'roundtable'>('direct');
  const [selected, setSelected] = useState<ExecutiveKey[]>(['ceo']);
  const toggle = (key: ExecutiveKey) => {
    if (key === 'ceo') return;
    setSelected((current) => mode === 'direct'
      ? ['ceo', key]
      : current.includes(key) ? current.filter((item) => item !== key) : [...current, key]);
  };
  const changeMode = (next: 'direct' | 'roundtable') => {
    setMode(next);
    setSelected((current) => next === 'direct' ? ['ceo', current.find((key) => key !== 'ceo') ?? 'cfo'] : current);
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl overflow-hidden p-0">
        <div className="bg-[#071426] px-6 py-5 text-white">
        <DialogHeader>
          <DialogTitle className="text-xl">Quem precisa estar nesta decisão?</DialogTitle>
          <DialogDescription className="text-slate-300">O CEO conduz a rodada, chama as especialidades necessárias e preserva qualquer divergência relevante.</DialogDescription>
        </DialogHeader>
        </div>
        <div className="space-y-5 px-6 py-5">
          <div><label className="mb-2 block text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Assunto da decisão <span className="font-normal normal-case">(opcional)</span></label><Input className="h-11 rounded-xl" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Ex.: Crescer sem pressionar o caixa" /></div>
          <div className="grid grid-cols-2 gap-2 rounded-xl bg-secondary/60 p-1">
            <Button className="rounded-lg" type="button" aria-pressed={mode === 'direct'} variant={mode === 'direct' ? 'default' : 'ghost'} onClick={() => changeMode('direct')}><Bot className="mr-2 h-4 w-4" />Consulta direta</Button>
            <Button className="rounded-lg" type="button" aria-pressed={mode === 'roundtable'} variant={mode === 'roundtable' ? 'default' : 'ghost'} onClick={() => changeMode('roundtable')}><UsersRound className="mr-2 h-4 w-4" />Mesa redonda</Button>
          </div>
          <div><p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Selecione os participantes</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {profiles.map((profile) => {
              const checked = selected.includes(profile.key);
              return (
                <button key={profile.key} type="button" aria-pressed={checked} aria-disabled={profile.key === 'ceo'} onClick={() => toggle(profile.key)} className={cn('flex items-start gap-3 rounded-xl border p-3.5 text-left transition', checked ? 'border-[#0057d8] bg-[#0057d8]/5 shadow-sm' : 'hover:border-foreground/20 hover:bg-muted/50')}>
                  <ExecutiveAvatar executiveKey={profile.key} className="h-8 w-8" />
                  <span className="min-w-0 flex-1"><span className="block text-xs font-semibold">{profile.mention} · {profile.role}</span><span className="mt-1 line-clamp-2 block text-2xs leading-4 text-muted-foreground">{profile.description}</span></span>
                  {checked && <Check className="h-4 w-4 text-[#0057d8]" />}
                  {profile.key === 'ceo' && <LockKeyhole className="h-3.5 w-3.5 text-muted-foreground" />}
                </button>
              );
            })}
          </div>
          </div>
          <p className="rounded-lg bg-secondary/60 px-3 py-2 text-2xs text-muted-foreground"><LockKeyhole className="mr-1 inline h-3 w-3" />A conversa nasce privada. O conselho analisa e recomenda, mas nunca executa ações.</p>
        </div>
        <DialogFooter className="border-t bg-secondary/30 px-6 py-4">
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button>
          <Button className="px-5" disabled={pending} onClick={() => onCreate({ title: title.trim() || undefined, mode, selectedExecutives: selected })}>
            {pending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Abrir a sala <ArrowUpRight className="ml-2 h-4 w-4" />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ShareThreadDialog({ open, thread, users, sharedUserIds, pending, onOpenChange, onToggle }: {
  open: boolean;
  thread: ExecutiveThread | undefined;
  users: ExecutiveShareCandidate[];
  sharedUserIds: Set<string>;
  pending: boolean;
  onOpenChange: (open: boolean) => void;
  onToggle: (userId: string, shared: boolean) => void;
}) {
  const eligible = users;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader><DialogTitle>Compartilhar conversa</DialogTitle><DialogDescription>Somente owners e administradores ativos podem receber acesso. Cada concessão ou revogação fica auditada.</DialogDescription></DialogHeader>
        <div className="max-h-80 space-y-2 overflow-y-auto py-2">
          {eligible.length === 0 ? <p className="rounded-lg border border-dashed p-4 text-xs text-muted-foreground">Nenhum outro owner ou administrador ativo está disponível.</p> : eligible.map((user) => {
            const shared = sharedUserIds.has(user.id);
            return <button key={user.id} type="button" disabled={pending} onClick={() => onToggle(user.id, shared)} className={cn('flex w-full items-center gap-3 rounded-lg border p-3 text-left', shared && 'border-[#0057d8] bg-[#0057d8]/5')}><span className="grid h-8 w-8 place-items-center rounded-full bg-muted text-xs font-semibold">{user.initials}</span><span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold">{user.name}</span><span className="block truncate text-2xs text-muted-foreground">{user.email ?? user.workspaceRole}</span></span>{shared ? <Check className="h-4 w-4 text-[#0057d8]" /> : <Share2 className="h-4 w-4 text-muted-foreground" />}</button>;
          })}
        </div>
        <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Concluir</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CouncilPresence({ profiles, selectedKeys }: { profiles: ExecutiveProfile[]; selectedKeys?: ExecutiveKey[] }) {
  const active = new Set(selectedKeys ?? profiles.map((profile) => profile.key));
  return (
    <div className="flex flex-wrap items-center gap-2" aria-label="Executivos do conselho">
      {profiles.map((profile) => (
        <div key={profile.key} className={cn('group flex items-center gap-2 rounded-full border bg-card py-1 pl-1 pr-2.5 shadow-sm transition', active.has(profile.key) ? 'border-border opacity-100' : 'opacity-35')}>
          <ExecutiveAvatar executiveKey={profile.key} className="h-7 w-7 rounded-full text-[8px]" />
          <span className="text-[10px] font-semibold">{profile.mention}</span>
          {profile.key === 'ceo' && <Crown className="h-3 w-3 text-[#b7791f]" />}
        </div>
      ))}
    </div>
  );
}

function ReportsView({ reports, pending, error, onRetry }: { reports: ExecutiveReport[]; pending: boolean; error: boolean; onRetry: () => void }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = reports.find((report) => report.id === selectedId) ?? reports[0];
  if (pending) return <div className="grid min-h-[420px] place-items-center text-xs text-muted-foreground"><LoaderCircle className="mr-2 inline h-4 w-4 animate-spin" />Carregando reports...</div>;
  if (error) return <EmptyError onRetry={onRetry} />;
  if (!selected) return <KlmPanel className="p-10 text-center"><FileText className="mx-auto h-7 w-7 text-muted-foreground" /><h2 className="mt-3 text-sm font-semibold">Nenhum report publicado</h2><p className="mt-1 text-xs text-muted-foreground">A agenda quinzenal ficará disponível quando o motor executivo e o scheduler forem homologados.</p></KlmPanel>;
  return (
    <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <KlmPanel className="overflow-hidden">
        <div className="border-b p-4"><p className="text-xs font-semibold">Histórico quinzenal</p><p className="mt-1 text-2xs text-muted-foreground">Reports institucionais do workspace</p></div>
        <div className="max-h-[680px] overflow-y-auto">
          {reports.map((report) => <button key={report.id} type="button" onClick={() => setSelectedId(report.id)} className={cn('w-full border-b px-4 py-3 text-left hover:bg-muted/50', report.id === selected.id && 'bg-[#0057d8]/5')}><div className="flex items-center justify-between gap-2"><span className="text-xs font-semibold">{report.title}</span><ChevronRight className="h-3.5 w-3.5" /></div><p className="mt-1 text-2xs text-muted-foreground">{formatDate(report.periodStart)} — {formatDate(report.periodEnd)}</p><Badge variant="outline" className="mt-2 text-[9px]">{reportStatusLabel[report.status]}</Badge></button>)}
        </div>
      </KlmPanel>
      <KlmPanel className="p-6">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b pb-5">
          <div><p className="text-2xs font-semibold uppercase text-[#0057d8]">@CEO · Report consolidado</p><h2 className="mt-1 text-xl font-semibold">{selected.title}</h2><p className="mt-1 text-xs text-muted-foreground">Período: {formatDate(selected.periodStart)} a {formatDate(selected.periodEnd)} · {reportStatusLabel[selected.status]}</p></div>
          <div className="flex -space-x-2">{selected.executiveParticipants.map((key) => <ExecutiveAvatar key={key} executiveKey={key} className="h-8 w-8 rounded-full border-2 border-card" />)}</div>
        </div>
        <div className="mt-5 whitespace-pre-wrap text-sm leading-7">{selected.body || 'Dados insuficientes para uma conclusão confiável nesta quinzena.'}</div>
      </KlmPanel>
    </div>
  );
}

export default function ExecutivesPage() {
  const { state } = useAppStore();
  const queryClient = useQueryClient();
  const activeRole = state.session?.activeRole ?? state.currentUser?.role ?? '';
  const allowed = ['workspace_owner', 'workspace_admin'].includes(activeRole);
  const [activeTab, setActiveTab] = useState('council');
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [newThreadOpen, setNewThreadOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [threadSearch, setThreadSearch] = useState('');
  const [draft, setDraft] = useState('');
  const [selectedSources, setSelectedSources] = useState<ExecutiveSource[]>([]);
  const [sourcesOpen, setSourcesOpen] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);

  const profilesQuery = useQuery({ queryKey: ['executives', 'profiles'], queryFn: klmApi.executiveProfiles, enabled: allowed, retry: false });
  const threadsQuery = useQuery({ queryKey: ['executives', 'threads'], queryFn: () => klmApi.executiveThreads('active'), enabled: allowed, retry: false });
  const reportsQuery = useQuery({ queryKey: ['executives', 'reports'], queryFn: klmApi.executiveReports, enabled: allowed && activeTab === 'reports', retry: false });
  const selectedThread = selectedThreadId
    ? threadsQuery.data?.find((thread) => thread.id === selectedThreadId)
    : threadsQuery.data?.[0];
  const messagesQuery = useQuery({ queryKey: ['executives', 'messages', selectedThread?.id], queryFn: () => klmApi.executiveMessages(selectedThread!.id), enabled: allowed && Boolean(selectedThread?.id), retry: false });
  const sourcesQuery = useQuery({ queryKey: ['executives', 'sources', selectedThread?.id], queryFn: () => klmApi.executiveSourceReceipts(selectedThread!.id), enabled: allowed && Boolean(selectedThread?.id), retry: false });
  const sharesQuery = useQuery({ queryKey: ['executives', 'shares', selectedThread?.id], queryFn: () => klmApi.executiveThreadShares(selectedThread!.id), enabled: allowed && Boolean(selectedThread?.id) && shareOpen, retry: false });
  const currentUserId = state.currentUser?.id;
  const canManageSelectedThread = Boolean(selectedThread && selectedThread.createdByUserId === currentUserId);
  const workspaceUsersQuery = useQuery({ queryKey: ['executives', 'share-candidates'], queryFn: klmApi.executiveShareCandidates, enabled: allowed && canManageSelectedThread && shareOpen, retry: false });

  const profiles = useMemo(() => profilesQuery.data ?? [], [profilesQuery.data]);
  const profilesByKey = useMemo(() => Object.fromEntries(profiles.map((profile) => [profile.key, profile])) as Partial<Record<ExecutiveKey, ExecutiveProfile>>, [profiles]);
  const filteredThreads = useMemo(() => (threadsQuery.data ?? []).filter((thread) => `${thread.title} ${thread.selectedExecutives.join(' ')}`.toLocaleLowerCase('pt-BR').includes(threadSearch.trim().toLocaleLowerCase('pt-BR'))), [threadSearch, threadsQuery.data]);
  useEffect(() => {
    setSelectedSources([]);
    setSourcesOpen(true);
  }, [selectedThread?.id]);

  const createThread = useMutation({
    mutationFn: klmApi.createExecutiveThread,
    onSuccess: (thread) => {
      queryClient.setQueryData<ExecutiveThread[]>(['executives', 'threads'], (current) => [
        thread,
        ...(current ?? []).filter((item) => item.id !== thread.id),
      ]);
      setNewThreadOpen(false);
      setSelectedThreadId(thread.id);
      void queryClient.invalidateQueries({ queryKey: ['executives', 'threads'] });
    },
    onError: () => setNotice('A conversa não pôde ser criada. Nenhum conteúdo local foi apresentado como persistido.'),
  });
  const sendMessage = useMutation({
    mutationFn: (body: string) => klmApi.sendExecutiveMessage(selectedThread!.id, { body, requestedExecutives: selectedThread?.selectedExecutives, preventAdditionalInvites: false, context: [] }),
    onSuccess: () => { setDraft(''); void queryClient.invalidateQueries({ queryKey: ['executives', 'messages', selectedThread?.id] }); void queryClient.invalidateQueries({ queryKey: ['executives', 'threads'] }); },
    onError: () => setNotice('O conselho não respondeu. Nenhuma resposta simulada foi inserida.'),
  });
  const archiveThread = useMutation({
    mutationFn: klmApi.archiveExecutiveThread,
    onSuccess: () => { setSelectedThreadId(null); void queryClient.invalidateQueries({ queryKey: ['executives', 'threads'] }); },
  });
  const shareMutation = useMutation({
    mutationFn: async ({ userId, shared }: { userId: string; shared: boolean }) => {
      if (!selectedThread) throw new Error('Conversa indisponível');
      if (shared) await klmApi.revokeExecutiveThreadShare(selectedThread.id, userId);
      else await klmApi.shareExecutiveThread(selectedThread.id, userId);
    },
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['executives', 'shares', selectedThread?.id] }); void queryClient.invalidateQueries({ queryKey: ['executives', 'threads'] }); },
    onError: () => setNotice('O compartilhamento não pôde ser alterado. O acesso anterior foi preservado quando possível.'),
  });
  const mentionFragment = /@([\p{L}]*)$/u.exec(draft)?.[1]?.toLowerCase() ?? null;
  const mentionMatches = mentionFragment === null ? [] : profiles.filter((profile) => profile.mention.toLowerCase().includes(mentionFragment) || profile.key.includes(mentionFragment)).slice(0, 8);

  if (!allowed) return <KlmPanel className="mx-auto max-w-xl p-8 text-center"><LockKeyhole className="mx-auto h-7 w-7 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">Conselho restrito</h2><p className="mt-2 text-xs leading-5 text-muted-foreground">Meus Executivos está disponível somente para o titular e administradores do workspace.</p></KlmPanel>;
  if (profilesQuery.isError || threadsQuery.isError) return <EmptyError onRetry={() => { void profilesQuery.refetch(); void threadsQuery.refetch(); }} />;

  return (
    <div className="mx-auto max-w-[1540px] space-y-5">
      <header className="relative overflow-hidden rounded-2xl border bg-[#071426] px-5 py-6 text-white shadow-[0_18px_60px_-36px_rgba(2,23,52,.75)] sm:px-7 lg:px-8">
        <div className="pointer-events-none absolute -right-24 -top-28 h-80 w-80 rounded-full bg-[#1d6ff2]/20 blur-3xl" />
        <div className="pointer-events-none absolute bottom-0 left-1/3 h-32 w-72 bg-[#13b8a6]/10 blur-3xl" />
        <div className="relative flex flex-col gap-6 xl:flex-row xl:items-end xl:justify-between">
          <div className="max-w-3xl">
            <div className="flex flex-wrap items-center gap-2 text-[10px] font-semibold uppercase tracking-[.16em] text-blue-200"><span>Inteligência executiva</span><span className="h-1 w-1 rounded-full bg-blue-300" /><span>Conselho privado</span></div>
            <h2 className="mt-3 text-3xl font-semibold tracking-[-.035em] sm:text-4xl">Decida com o conselho inteiro na sala.</h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300">Convoque especialistas, confronte perspectivas e transforme dados do escritório em uma decisão clara — sempre com evidências e aprovação humana.</p>
            <div className="mt-5"><CouncilPresence profiles={profiles} /></div>
          </div>
          <div className="flex shrink-0 flex-col items-start gap-3 xl:items-end">
            <div className="flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-[10px] text-slate-300"><ShieldCheck className="h-3.5 w-3.5 text-emerald-300" />Consultivo · privado · sem ações automáticas</div>
            <Button className="h-11 bg-white px-5 text-[#071426] hover:bg-slate-100" onClick={() => setNewThreadOpen(true)} disabled={profilesQuery.isPending}><MessageSquarePlus className="mr-2 h-4 w-4" />Convocar o conselho</Button>
          </div>
        </div>
      </header>
      {notice && <div role="status" className="flex items-center justify-between rounded-lg border border-[#a53e3c]/20 bg-[#a53e3c]/5 px-4 py-2 text-xs"><span>{notice}</span><Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => setNotice(null)}><X className="h-3.5 w-3.5" /></Button></div>}
      {!selectedThread && (
        <section aria-label="Perguntas sugeridas" className="grid gap-2 md:grid-cols-3">
          {starterQuestions.map(({ label, prompt, icon: Icon }) => (
            <button key={label} type="button" onClick={() => { setDraft(prompt); setNewThreadOpen(true); }} className="group flex items-center gap-3 rounded-xl border bg-card p-3.5 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-[#0057d8]/40 hover:shadow-md">
              <span className="grid h-9 w-9 place-items-center rounded-lg bg-[#0057d8]/8 text-[#0057d8]"><Icon className="h-4 w-4" /></span>
              <span className="min-w-0 flex-1"><span className="block text-xs font-semibold">{label}</span><span className="mt-0.5 block truncate text-[10px] text-muted-foreground">Levar ao conselho</span></span>
              <ArrowUpRight className="h-3.5 w-3.5 text-muted-foreground transition group-hover:text-[#0057d8]" />
            </button>
          ))}
        </section>
      )}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <TabsList className="h-10 rounded-xl bg-secondary/70 p-1"><TabsTrigger className="rounded-lg px-4" value="council"><UsersRound className="mr-2 h-3.5 w-3.5" />Sala do conselho</TabsTrigger><TabsTrigger className="rounded-lg px-4" value="reports"><FileText className="mr-2 h-3.5 w-3.5" />Reports executivos</TabsTrigger></TabsList>
          <p className="text-[10px] text-muted-foreground"><LockKeyhole className="mr-1 inline h-3 w-3" />Visível apenas para owner e administradores autorizados</p>
        </div>
        <TabsContent value="council" className="mt-4">
          <KlmPanel className="min-h-[720px] overflow-hidden rounded-2xl border-border/80 shadow-[0_16px_50px_-38px_rgba(15,23,42,.5)]">
            <div className={cn('grid min-h-[720px]', sourcesOpen ? 'lg:grid-cols-[286px_minmax(0,1fr)_330px]' : 'lg:grid-cols-[286px_minmax(0,1fr)]')}>
              <aside className="border-r bg-[#f7f9fc] dark:bg-secondary/20">
                <div className="border-b p-4"><div className="flex items-center justify-between"><div><p className="text-xs font-semibold">Conversas</p><p className="mt-0.5 text-[10px] text-muted-foreground">{filteredThreads.length} ativas</p></div><Button size="icon" className="h-8 w-8 rounded-lg" aria-label="Nova conversa" onClick={() => setNewThreadOpen(true)}><MessageSquarePlus className="h-3.5 w-3.5" /></Button></div><div className="relative mt-3"><Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-muted-foreground" /><Input value={threadSearch} onChange={(event) => setThreadSearch(event.target.value)} className="h-9 rounded-xl border-border/70 bg-card pl-9 text-xs shadow-none" placeholder="Buscar uma decisão..." /></div></div>
                <div className="max-h-[590px] overflow-y-auto">
                  {threadsQuery.isPending ? <p className="p-4 text-xs text-muted-foreground"><LoaderCircle className="mr-2 inline h-3.5 w-3.5 animate-spin" />Carregando...</p> : filteredThreads.length === 0 ? <div className="p-5"><div className="rounded-xl border border-dashed bg-card p-4"><p className="text-xs font-semibold">Nenhuma decisão em pauta</p><p className="mt-1 text-[10px] leading-4 text-muted-foreground">Convoque um executivo ou reúna uma mesa redonda.</p></div></div> : filteredThreads.map((thread) => <button key={thread.id} type="button" onClick={() => setSelectedThreadId(thread.id)} className={cn('relative w-full border-b px-4 py-3.5 text-left transition hover:bg-white dark:hover:bg-muted/60', thread.id === selectedThread?.id && 'bg-white shadow-[inset_3px_0_0_#0b66e4] dark:bg-[#0057d8]/5')}><div className="flex items-center justify-between gap-2"><p className="truncate text-xs font-semibold">{thread.title}</p>{thread.visibility === 'private' ? <LockKeyhole className="h-3 w-3 text-muted-foreground" /> : <Share2 className="h-3 w-3 text-[#0057d8]" />}</div><p className="mt-1 text-[10px] text-muted-foreground">{thread.mode === 'roundtable' ? 'Mesa redonda' : 'Conversa direta'} · {formatDate(thread.lastMessageAt)}</p><div className="mt-2.5 flex -space-x-1">{thread.selectedExecutives.slice(0, 5).map((key) => <ExecutiveAvatar key={key} executiveKey={key} className="h-6 w-6 rounded-full border-2 border-card text-[8px]" />)}</div></button>)}
                </div>
              </aside>
              <section className="flex min-w-0 flex-col" aria-label="Conversa do conselho">
                {selectedThread ? <><div className="flex items-center justify-between gap-3 border-b px-4 py-3"><div className="min-w-0"><h2 className="truncate text-sm font-semibold">{selectedThread.title || 'Conversa sem título'}</h2><p className="mt-0.5 text-2xs text-muted-foreground"><Crown className="mr-1 inline h-3 w-3 text-[#b7791f]" />@CEO coordena · {selectedThread.mode === 'roundtable' ? 'Mesa redonda' : 'Conversa direta'} · {selectedThread.visibility === 'private' ? 'Privada' : 'Compartilhada'}</p></div><div className="flex items-center"><Button size="icon" variant="ghost" aria-label="Compartilhar conversa" onClick={() => setShareOpen(true)}><Share2 className="h-4 w-4" /></Button><Button size="icon" variant="ghost" aria-label="Arquivar conversa" onClick={() => archiveThread.mutate(selectedThread.id)}><Archive className="h-4 w-4" /></Button><Button size="icon" variant="ghost" aria-label="Mostrar fontes" onClick={() => setSourcesOpen((value) => !value)}><PanelRight className="h-4 w-4" /></Button></div></div><div className="min-h-0 flex-1 overflow-y-auto divide-y">{messagesQuery.isPending ? <div className="grid min-h-[420px] place-items-center text-xs text-muted-foreground"><span><LoaderCircle className="mr-2 inline h-4 w-4 animate-spin" />Carregando conversa...</span></div> : messagesQuery.isError ? <div className="grid min-h-[420px] place-items-center px-8 text-center"><div><AlertTriangle className="mx-auto h-7 w-7 text-[#a53e3c]" /><h3 className="mt-3 text-sm font-semibold">Não foi possível carregar o histórico</h3><p className="mt-1 text-xs text-muted-foreground">A conversa foi preservada. Tente consultar as mensagens novamente.</p><Button className="mt-4" variant="outline" onClick={() => void messagesQuery.refetch()}>Tentar novamente</Button></div></div> : (messagesQuery.data ?? []).length === 0 ? <div className="grid min-h-[420px] place-items-center px-8 text-center"><div><Sparkles className="mx-auto h-8 w-8 text-[#0057d8]" /><h3 className="mt-3 text-base font-semibold">Leve uma decisão ao conselho</h3><p className="mx-auto mt-2 max-w-md text-xs leading-5 text-muted-foreground">A pergunta será persistida com segurança. Enquanto o motor executivo não estiver homologado, o sistema registrará a indisponibilidade sem fabricar respostas.</p></div></div> : (messagesQuery.data ?? []).map((message) => <MessageCard key={message.id} message={message} profile={message.executiveKey ? profilesByKey[message.executiveKey] : undefined} onSources={() => { const receipt = (sourcesQuery.data ?? []).find((item) => item.roundId === message.roundId && (!message.executiveKey || item.executiveKey === message.executiveKey)); setSelectedSources(receipt?.selectedSources ?? []); setSourcesOpen(true); }} />)}</div><div className="relative border-t bg-card p-3">{mentionMatches.length > 0 && <div className="absolute bottom-full left-3 z-20 mb-1 w-64 overflow-hidden rounded-lg border bg-popover shadow-lg">{mentionMatches.map((profile) => <button key={profile.key} type="button" className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted" onClick={() => setDraft((value) => value.replace(/@[\p{L}]*$/u, `${profile.mention} `))}><ExecutiveAvatar executiveKey={profile.key} className="h-7 w-7" /><span><span className="block text-xs font-semibold">{profile.mention}</span><span className="block text-2xs text-muted-foreground">{profile.role}</span></span></button>)}</div>}<div className="rounded-xl border bg-background p-2 focus-within:ring-2 focus-within:ring-ring"><Textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (draft.trim() && !sendMessage.isPending) sendMessage.mutate(draft.trim()); } }} placeholder="Registre a pergunta para o conselho. Use @CFO, @CRO, @todos..." className="min-h-[74px] resize-none border-0 p-2 shadow-none focus-visible:ring-0" /><div className="flex items-center justify-between gap-2 px-1 pb-1"><span className="text-[10px] text-muted-foreground"><Database className="mr-1 inline h-3 w-3" />Contexto executivo: indisponível até homologação do motor e RAG</span><Button size="sm" disabled={!draft.trim() || sendMessage.isPending} onClick={() => sendMessage.mutate(draft.trim())}>{sendMessage.isPending ? <LoaderCircle className="mr-2 h-3.5 w-3.5 animate-spin" /> : <Send className="mr-2 h-3.5 w-3.5" />}Registrar pergunta</Button></div></div></div></> : <div className="grid flex-1 place-items-center px-8 text-center"><div><Crown className="mx-auto h-9 w-9 text-[#b7791f]" /><h2 className="mt-3 text-base font-semibold">Seu conselho executivo</h2><p className="mt-2 max-w-sm text-xs leading-5 text-muted-foreground">Crie uma conversa privada com um C-level ou reúna diferentes especialistas em uma mesa redonda coordenada pelo CEO.</p><Button className="mt-5" onClick={() => setNewThreadOpen(true)}>Começar conversa</Button></div></div>}
              </section>
              {sourcesOpen && <SourcePanel sources={selectedSources} onClose={() => setSourcesOpen(false)} />}
            </div>
          </KlmPanel>
        </TabsContent>
        <TabsContent value="reports" className="mt-4"><ReportsView reports={reportsQuery.data ?? []} pending={reportsQuery.isPending} error={reportsQuery.isError} onRetry={() => void reportsQuery.refetch()} /></TabsContent>
      </Tabs>
      <NewThreadDialog open={newThreadOpen} profiles={profiles} onOpenChange={setNewThreadOpen} onCreate={(payload) => createThread.mutate(payload)} pending={createThread.isPending} />
      <ShareThreadDialog open={shareOpen} thread={selectedThread} users={workspaceUsersQuery.data ?? []} sharedUserIds={new Set((sharesQuery.data ?? []).map((share) => share.userId))} pending={shareMutation.isPending || workspaceUsersQuery.isPending || sharesQuery.isPending} onOpenChange={setShareOpen} onToggle={(userId, shared) => shareMutation.mutate({ userId, shared })} />
    </div>
  );
}
