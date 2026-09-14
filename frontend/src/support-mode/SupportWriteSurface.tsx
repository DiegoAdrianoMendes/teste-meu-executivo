import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Loader2, ShieldCheck } from 'lucide-react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { ApiError, apiFetch } from '@/services/http';
import { SUPPORT_MODE_ROUTES, supportModeRoutePath } from '@/support-mode/routes';
import type { SupportAccessScope } from '@/support-mode/scopes';
import {
  captureSupportModeRequestIdentity,
  isSupportModeRequestIdentityCurrent,
  SupportModeRequestAbortedError,
  type SupportModeRequestIdentity,
  type SupportModeSession,
} from '@/support-mode/session';
import type { SupportWriteActionKind } from '@/support-mode/surfaces';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'A alteração não foi concluída.';
}

interface SupportSelectOption {
  id: string;
  label: string;
  detail?: string;
}

function SupportEntitySelect({
  id,
  label,
  placeholder,
  value,
  options,
  loading,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  placeholder: string;
  value: string;
  options: SupportSelectOption[];
  loading: boolean;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const [search, setSearch] = useState('');
  const normalizedSearch = search.trim().toLocaleLowerCase('pt-BR');
  const filtered = normalizedSearch
    ? options.filter((option) => `${option.label} ${option.detail ?? ''}`.toLocaleLowerCase('pt-BR').includes(normalizedSearch))
    : options;

  return (
    <div className="space-y-1.5">
      <Label htmlFor={`${id}-search`}>{label}</Label>
      <Input
        id={`${id}-search`}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder={loading ? 'Carregando opções autorizadas...' : placeholder}
        disabled={disabled || loading}
        aria-controls={`${id}-select`}
      />
      <Select value={value} onValueChange={onChange} disabled={disabled || loading || options.length === 0}>
        <SelectTrigger id={`${id}-select`} aria-label={label}>
          <SelectValue placeholder={loading ? 'Carregando...' : options.length ? 'Selecione uma opção' : 'Nenhuma opção disponível'} />
        </SelectTrigger>
        <SelectContent>
          {filtered.map((option) => (
            <SelectItem key={option.id} value={option.id}>
              {option.label}{option.detail ? ` · ${option.detail}` : ''}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {!loading && options.length > 0 && filtered.length === 0 ? (
        <p className="text-xs text-muted-foreground" role="status">Nenhum resultado para esta busca.</p>
      ) : null}
    </div>
  );
}

function BlockedWrite({ reason }: { reason: string }) {
  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100" role="status">
      <div className="flex gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <div>
          <p className="font-medium">Alteração bloqueada pelo contrato atual</p>
          <p className="mt-1 text-xs opacity-80">{reason}</p>
          <Button type="button" size="sm" className="mt-3" disabled>Executar alteração</Button>
        </div>
      </div>
    </div>
  );
}

function ConfirmWrite({
  disabled,
  pending,
  label,
  title,
  description,
  onConfirm,
}: {
  disabled: boolean;
  pending: boolean;
  label: string;
  title: string;
  description: string;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button type="button" disabled={disabled || pending}>
          {pending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-1.5 h-4 w-4" />}
          {label}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Voltar</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>Confirmar alteração</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function WriteFeedback({ message, error }: { message: string | null; error: unknown }) {
  if (!message && !error) return null;
  return (
    <div
      className={`flex items-start gap-2 rounded-md border p-3 text-sm ${error ? 'border-destructive/40 text-destructive' : 'border-emerald-300 text-emerald-800 dark:border-emerald-900 dark:text-emerald-200'}`}
      role={error ? 'alert' : 'status'}
      aria-live="polite"
    >
      {error ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> : <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />}
      <span>{error ? errorMessage(error) : message}</span>
    </div>
  );
}

function useSupportWriteMutation<TVariables>({
  session,
  scope,
  mutationFn,
  successMessage,
  onSuccess,
}: {
  session: SupportModeSession;
  scope: SupportAccessScope;
  mutationFn: (variables: TVariables) => Promise<unknown>;
  successMessage: string;
  onSuccess?: () => void;
}) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [feedback, setFeedback] = useState<string | null>(null);
  const operationIdentityRef = useRef<SupportModeRequestIdentity | null>(null);
  const mutation = useMutation({
    mutationKey: ['support-mode', session.workspaceId, session.grantId, 'write', scope],
    mutationFn: async (variables: TVariables) => {
      const identity = captureSupportModeRequestIdentity(session);
      if (!identity) throw new SupportModeRequestAbortedError();
      operationIdentityRef.current = identity;
      try {
        const result = await mutationFn(variables);
        if (!isSupportModeRequestIdentityCurrent(identity)) {
          throw new SupportModeRequestAbortedError();
        }
        return result;
      } catch (error) {
        if (!isSupportModeRequestIdentityCurrent(identity)) {
          throw new SupportModeRequestAbortedError();
        }
        throw error;
      }
    },
    gcTime: 0,
    onMutate: () => {
      operationIdentityRef.current = null;
      setFeedback(null);
    },
    onSuccess: async () => {
      const identity = operationIdentityRef.current;
      if (!identity || !isSupportModeRequestIdentityCurrent(identity)) return;
      onSuccess?.();
      setFeedback(successMessage);
      await queryClient.invalidateQueries({
        queryKey: ['support-mode', session.workspaceId, session.grantId],
      });
      if (!isSupportModeRequestIdentityCurrent(identity)) return;
      toast({ title: successMessage });
    },
    onError: (error) => {
      const identity = operationIdentityRef.current;
      if (
        error instanceof SupportModeRequestAbortedError
        || !identity
        || !isSupportModeRequestIdentityCurrent(identity)
      ) return;
      toast({
        variant: 'destructive',
        title: 'Alteração não concluída',
        description: errorMessage(error),
      });
    },
  });
  const visibleError = mutation.error instanceof SupportModeRequestAbortedError
    ? null
    : mutation.error;
  return { mutation, feedback, visibleError };
}

interface WorkspaceAreaActivation {
  key: string;
  label: string;
  enabled: boolean;
  selectedFlowTemplateId?: string | null;
  followUpProfile: 'gentle' | 'balanced' | 'assertive';
  humanInterventionMode: 'human_only' | 'ai_only' | 'human_in_the_loop' | 'ai_with_human_approval';
}

interface WorkspaceOperations {
  areaActivations: WorkspaceAreaActivation[];
}

function WorkspaceWrite({ session }: { session: SupportModeSession }) {
  const canRead = session.scopes.includes('workspace:read');
  const queryKey = ['support-mode', session.workspaceId, session.grantId, 'write-prerequisite', 'workspace-operations'] as const;
  const operationsQuery = useQuery({
    queryKey,
    queryFn: () => apiFetch<WorkspaceOperations>(supportModeRoutePath(SUPPORT_MODE_ROUTES.workspaceOperationsRead)),
    enabled: canRead,
    retry: false,
  });
  const [areaKey, setAreaKey] = useState('');
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    const area = operationsQuery.data?.areaActivations.find((candidate) => candidate.key === areaKey)
      ?? operationsQuery.data?.areaActivations[0];
    if (!area) return;
    setAreaKey(area.key);
    setEnabled(area.enabled);
  }, [areaKey, operationsQuery.data]);

  const { mutation, feedback, visibleError } = useSupportWriteMutation({
    session,
    scope: 'workspace:write',
    mutationFn: async ({ targetAreaKey, targetEnabled }: { targetAreaKey: string; targetEnabled: boolean }) => {
      const operations = operationsQuery.data;
      if (!operations?.areaActivations.some((area) => area.key === targetAreaKey)) {
        throw new Error('A área selecionada não está mais disponível. Atualize os dados antes de alterar.');
      }
      return apiFetch(supportModeRoutePath(SUPPORT_MODE_ROUTES.workspaceOperationsWrite), {
        method: SUPPORT_MODE_ROUTES.workspaceOperationsWrite.method,
        body: JSON.stringify({
          areaActivations: operations.areaActivations.map((area) => ({
            key: area.key,
            enabled: area.key === targetAreaKey ? targetEnabled : area.enabled,
            selectedFlowTemplateId: area.selectedFlowTemplateId ?? null,
            followUpProfile: area.followUpProfile,
            humanInterventionMode: area.humanInterventionMode,
          })),
        }),
      });
    },
    successMessage: 'Operação do workspace atualizada e enviada à trilha.',
  });

  if (!canRead) {
    return <BlockedWrite reason="workspace:read também é necessário para preservar todas as áreas antes do PATCH integral exigido pelo backend." />;
  }
  if (operationsQuery.isLoading) return <p className="text-sm text-muted-foreground" role="status">Carregando operação vigente...</p>;
  if (operationsQuery.isError) return <WriteFeedback message={null} error={operationsQuery.error} />;
  const selectedArea = operationsQuery.data?.areaActivations.find((area) => area.key === areaKey);
  if (!selectedArea) return <BlockedWrite reason="O workspace não possui áreas operacionais configuradas para alteração segura." />;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
        <div className="space-y-1.5">
          <Label htmlFor="support-workspace-area">Área operacional</Label>
          <Select value={areaKey} onValueChange={(value) => {
            setAreaKey(value);
            const next = operationsQuery.data?.areaActivations.find((area) => area.key === value);
            if (next) setEnabled(next.enabled);
          }}>
            <SelectTrigger id="support-workspace-area"><SelectValue /></SelectTrigger>
            <SelectContent>
              {operationsQuery.data?.areaActivations.map((area) => (
                <SelectItem key={area.key} value={area.key}>{area.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <label className="flex h-10 items-center gap-2 rounded-md border px-3 text-sm">
          <Checkbox checked={enabled} onCheckedChange={(checked) => setEnabled(checked === true)} />
          Área ativa
        </label>
      </div>
      <ConfirmWrite
        disabled={enabled === selectedArea.enabled}
        pending={mutation.isPending}
        label="Atualizar área"
        title="Alterar a operação desta área?"
        description={`${selectedArea.label} ficará ${enabled ? 'ativa' : 'inativa'}. As demais áreas serão preservadas.`}
        onConfirm={() => mutation.mutate({ targetAreaKey: areaKey, targetEnabled: enabled })}
      />
      <WriteFeedback message={feedback} error={visibleError} />
    </div>
  );
}

function ClientWrite({ session }: { session: SupportModeSession }) {
  const [name, setName] = useState('');
  const cleanName = name.trim();
  const { mutation, feedback, visibleError } = useSupportWriteMutation({
    session,
    scope: 'clients:write',
    mutationFn: (clientName: string) => apiFetch(supportModeRoutePath(SUPPORT_MODE_ROUTES.clientsCreate), {
      method: SUPPORT_MODE_ROUTES.clientsCreate.method,
      body: JSON.stringify({ name: clientName }),
    }),
    successMessage: 'Cliente criado e operação enviada à trilha.',
    onSuccess: () => setName(''),
  });
  return (
    <div className="space-y-4">
      <div className="max-w-xl space-y-1.5">
        <Label htmlFor="support-client-name">Nome do cliente</Label>
        <Input id="support-client-name" value={name} maxLength={160} onChange={(event) => setName(event.target.value)} />
      </div>
      <ConfirmWrite
        disabled={cleanName.length < 2}
        pending={mutation.isPending}
        label="Criar cliente"
        title="Criar este cliente no workspace?"
        description={`Será criado um cliente chamado “${cleanName || 'não informado'}”.`}
        onConfirm={() => mutation.mutate(cleanName)}
      />
      <WriteFeedback message={feedback} error={visibleError} />
    </div>
  );
}

interface SupportMatterItem {
  id: string;
  caseNumber?: string | null;
  demandType: string;
  client: { name: string };
}

interface SupportTaskItem {
  id: string;
  title: string;
  status: string;
}

function matterOptions(items: SupportMatterItem[] | undefined): SupportSelectOption[] {
  return (items ?? []).map((matter) => ({
    id: matter.id,
    label: matter.client.name,
    detail: matter.caseNumber || matter.demandType,
  }));
}

function MatterWrite({ session }: { session: SupportModeSession }) {
  const canRead = session.scopes.includes('matters:read');
  const [matterId, setMatterId] = useState('');
  const [nextAction, setNextAction] = useState('');
  const cleanAction = nextAction.trim();
  const mattersQuery = useQuery({
    queryKey: ['support-mode', session.workspaceId, session.grantId, 'write-prerequisite', 'matters'],
    queryFn: () => apiFetch<SupportMatterItem[]>(supportModeRoutePath(SUPPORT_MODE_ROUTES.mattersList)),
    enabled: canRead,
    retry: false,
  });
  const { mutation, feedback, visibleError } = useSupportWriteMutation({
    session,
    scope: 'matters:write',
    mutationFn: ({ id, value }: { id: string; value: string }) => apiFetch(supportModeRoutePath(
      SUPPORT_MODE_ROUTES.matterNextActionWrite,
      { matterId: id },
    ), {
      method: SUPPORT_MODE_ROUTES.matterNextActionWrite.method,
      body: JSON.stringify({ nextAction: value }),
    }),
    successMessage: 'Próxima ação do caso atualizada e enviada à trilha.',
    onSuccess: () => setNextAction(''),
  });
  if (!canRead) {
    return <BlockedWrite reason="matters:read também é necessário para selecionar um caso do workspace sem expor ou digitar identificadores técnicos." />;
  }
  if (mattersQuery.isError) return <WriteFeedback message={null} error={mattersQuery.error} />;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 lg:grid-cols-[minmax(240px,0.7fr)_1fr]">
        <SupportEntitySelect
          id="support-matter"
          label="Caso do workspace"
          placeholder="Buscar por cliente, processo ou demanda"
          value={matterId}
          options={matterOptions(mattersQuery.data)}
          loading={mattersQuery.isLoading}
          onChange={setMatterId}
        />
        <div className="space-y-1.5">
          <Label htmlFor="support-matter-next-action">Próxima ação</Label>
          <Input id="support-matter-next-action" value={nextAction} maxLength={240} onChange={(event) => setNextAction(event.target.value)} />
        </div>
      </div>
      <ConfirmWrite
        disabled={!matterId || cleanAction.length < 3}
        pending={mutation.isPending}
        label="Atualizar caso"
        title="Atualizar a próxima ação deste caso?"
        description="O valor atual será substituído pelo texto informado e a chamada ficará vinculada ao grant."
        onConfirm={() => mutation.mutate({ id: matterId, value: cleanAction })}
      />
      <WriteFeedback message={feedback} error={visibleError} />
    </div>
  );
}

function TaskWrite({ session }: { session: SupportModeSession }) {
  const canReadMatters = session.scopes.includes('matters:read');
  const canReadTasks = session.scopes.includes('tasks:read');
  const [matterId, setMatterId] = useState('');
  const [taskId, setTaskId] = useState('');
  const [title, setTitle] = useState('');
  const cleanTitle = title.trim();
  const mattersQuery = useQuery({
    queryKey: ['support-mode', session.workspaceId, session.grantId, 'write-prerequisite', 'task-matters'],
    queryFn: () => apiFetch<SupportMatterItem[]>(supportModeRoutePath(SUPPORT_MODE_ROUTES.mattersList)),
    enabled: canReadMatters && canReadTasks,
    retry: false,
  });
  const tasksQuery = useQuery({
    queryKey: ['support-mode', session.workspaceId, session.grantId, 'write-prerequisite', 'tasks', matterId],
    queryFn: () => apiFetch<SupportTaskItem[]>(supportModeRoutePath(SUPPORT_MODE_ROUTES.tasksForMatter, { matterId })),
    enabled: canReadMatters && canReadTasks && Boolean(matterId),
    retry: false,
  });
  const { mutation, feedback, visibleError } = useSupportWriteMutation({
    session,
    scope: 'tasks:write',
    mutationFn: ({ id, value }: { id: string; value: string }) => apiFetch(supportModeRoutePath(
      SUPPORT_MODE_ROUTES.taskTitleWrite,
      { taskId: id },
    ), {
      method: SUPPORT_MODE_ROUTES.taskTitleWrite.method,
      body: JSON.stringify({ title: value }),
    }),
    successMessage: 'Tarefa atualizada e operação enviada à trilha.',
    onSuccess: () => setTitle(''),
  });
  if (!canReadMatters || !canReadTasks) {
    return <BlockedWrite reason="matters:read e tasks:read também são necessários para selecionar a tarefa autorizada sem digitar identificadores técnicos." />;
  }
  if (mattersQuery.isError) return <WriteFeedback message={null} error={mattersQuery.error} />;
  if (tasksQuery.isError) return <WriteFeedback message={null} error={tasksQuery.error} />;
  const tasks = (tasksQuery.data ?? []).map((task) => ({ id: task.id, label: task.title, detail: task.status }));
  return (
    <div className="space-y-4">
      <div className="grid gap-3 lg:grid-cols-3">
        <SupportEntitySelect
          id="support-task-matter"
          label="Caso do workspace"
          placeholder="Buscar caso da tarefa"
          value={matterId}
          options={matterOptions(mattersQuery.data)}
          loading={mattersQuery.isLoading}
          onChange={(value) => { setMatterId(value); setTaskId(''); }}
        />
        <SupportEntitySelect
          id="support-task"
          label="Tarefa do caso"
          placeholder={matterId ? 'Buscar tarefa por título' : 'Selecione primeiro o caso'}
          value={taskId}
          options={tasks}
          loading={tasksQuery.isLoading}
          disabled={!matterId}
          onChange={setTaskId}
        />
        <div className="space-y-1.5">
          <Label htmlFor="support-task-title">Novo título</Label>
          <Input id="support-task-title" value={title} maxLength={240} onChange={(event) => setTitle(event.target.value)} />
        </div>
      </div>
      <ConfirmWrite
        disabled={!taskId || cleanTitle.length < 3}
        pending={mutation.isPending}
        label="Atualizar tarefa"
        title="Substituir o título desta tarefa?"
        description="A alteração será executada somente na tarefa informada e ficará vinculada ao grant."
        onConfirm={() => mutation.mutate({ id: taskId, value: cleanTitle })}
      />
      <WriteFeedback message={feedback} error={visibleError} />
    </div>
  );
}

interface ConfigurationDocument {
  etag: string;
  schemaVersion: string;
  payload: Record<string, unknown>;
}

function ConfigurationWrite({ session }: { session: SupportModeSession }) {
  const canRead = session.scopes.includes('configurations:read');
  const documentQuery = useQuery({
    queryKey: ['support-mode', session.workspaceId, session.grantId, 'write-prerequisite', 'brand-identity-document'],
    queryFn: async (): Promise<ConfigurationDocument | null> => {
      try {
        return await apiFetch<ConfigurationDocument>(supportModeRoutePath(
          SUPPORT_MODE_ROUTES.configurationWorkspaceDocumentRead,
        ));
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: canRead,
    retry: false,
  });
  const [tagline, setTagline] = useState('');
  const cleanTagline = tagline.trim();

  useEffect(() => {
    const current = documentQuery.data?.payload.tagline;
    if (typeof current === 'string') setTagline(current);
  }, [documentQuery.data]);

  const { mutation, feedback, visibleError } = useSupportWriteMutation({
    session,
    scope: 'configurations:write',
    mutationFn: (value: string) => {
      const document = documentQuery.data;
      return apiFetch(supportModeRoutePath(SUPPORT_MODE_ROUTES.configurationWorkspaceDocumentWrite), {
        method: SUPPORT_MODE_ROUTES.configurationWorkspaceDocumentWrite.method,
        headers: document?.etag ? { 'If-Match': `"${document.etag}"` } : undefined,
        body: JSON.stringify({
          payload: { ...(document?.payload ?? {}), tagline: value },
          schemaVersion: document?.schemaVersion ?? '1.0',
          reason: 'Alteração temporária executada pelo modo suporte',
        }),
      });
    },
    successMessage: 'Identidade da marca atualizada e enviada à trilha.',
  });

  if (!canRead) {
    return <BlockedWrite reason="configurations:read também é necessário para preservar a camada vigente e seu ETag antes do PUT versionado." />;
  }
  if (documentQuery.isLoading) return <p className="text-sm text-muted-foreground" role="status">Carregando camada vigente...</p>;
  if (documentQuery.isError) return <WriteFeedback message={null} error={documentQuery.error} />;

  return (
    <div className="space-y-4">
      <div className="max-w-xl space-y-1.5">
        <Label htmlFor="support-brand-tagline">Tagline da marca</Label>
        <Input id="support-brand-tagline" value={tagline} maxLength={180} onChange={(event) => setTagline(event.target.value)} />
      </div>
      <ConfirmWrite
        disabled={cleanTagline.length < 3 || cleanTagline === documentQuery.data?.payload.tagline}
        pending={mutation.isPending}
        label="Atualizar tagline"
        title="Atualizar a identidade da marca?"
        description="A camada atual será preservada, versionada por ETag e terá apenas a tagline substituída."
        onConfirm={() => mutation.mutate(cleanTagline)}
      />
      <WriteFeedback message={feedback} error={visibleError} />
    </div>
  );
}

interface SupportTeamMember {
  membershipId: string;
  name: string;
  email: string;
  workspaceRole: string;
  accessPackage?: string | null;
  status: 'invited' | 'active' | 'suspended' | 'removed';
}

function TeamMemberWrite({ session }: { session: SupportModeSession }) {
  const canRead = session.scopes.includes('members:read');
  const membersQuery = useQuery({
    queryKey: ['support-mode', session.workspaceId, session.grantId, 'team-members'],
    queryFn: () => apiFetch<SupportTeamMember[]>(
      supportModeRoutePath(SUPPORT_MODE_ROUTES.teamMembersList),
    ),
    enabled: canRead,
    retry: false,
  });
  const [membershipId, setMembershipId] = useState('');
  const [status, setStatus] = useState<SupportTeamMember['status']>('active');
  const selected = membersQuery.data?.find((member) => member.membershipId === membershipId) ?? null;

  useEffect(() => {
    const member = selected ?? membersQuery.data?.[0];
    if (!member) return;
    setMembershipId(member.membershipId);
    setStatus(member.status);
  }, [membersQuery.data, selected]);

  const { mutation, feedback, visibleError } = useSupportWriteMutation({
    session,
    scope: 'members:write',
    mutationFn: ({ targetMembershipId, targetStatus }: { targetMembershipId: string; targetStatus: SupportTeamMember['status'] }) =>
      apiFetch(
        supportModeRoutePath(SUPPORT_MODE_ROUTES.teamMemberWrite, { membershipId: targetMembershipId }),
        {
          method: SUPPORT_MODE_ROUTES.teamMemberWrite.method,
          body: JSON.stringify({ status: targetStatus }),
        },
      ),
    successMessage: 'Acesso do usuário atualizado dentro do escopo aprovado.',
    onSuccess: () => { void membersQuery.refetch(); },
  });

  if (!canRead) {
    return <BlockedWrite reason="Inclua também members:read para selecionar o vínculo sem informar identificadores manualmente." />;
  }
  if (membersQuery.isLoading) return <p className="text-sm text-muted-foreground" role="status">Carregando equipe autorizada...</p>;
  if (membersQuery.isError) return <WriteFeedback message={null} error={membersQuery.error} />;
  if (!membersQuery.data?.length) return <p className="text-sm text-muted-foreground">Nenhum membro disponível nesta conta.</p>;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <Label>Usuário</Label>
          <Select value={membershipId} onValueChange={setMembershipId}>
            <SelectTrigger aria-label="Usuário da conta"><SelectValue placeholder="Selecione o usuário" /></SelectTrigger>
            <SelectContent>
              {membersQuery.data.map((member) => (
                <SelectItem key={member.membershipId} value={member.membershipId}>
                  {member.name} · {member.email}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>Status do vínculo</Label>
          <Select value={status} onValueChange={(value) => setStatus(value as SupportTeamMember['status'])}>
            <SelectTrigger aria-label="Status do vínculo"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="active">Ativo</SelectItem>
              <SelectItem value="suspended">Suspenso</SelectItem>
              <SelectItem value="removed">Removido</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      <ConfirmWrite
        disabled={!selected || status === selected.status}
        pending={mutation.isPending}
        label="Atualizar acesso"
        title="Alterar o acesso deste usuário?"
        description="A alteração será atribuída ao funcionário real, limitada ao workspace e registrada no grant de suporte ativo."
        onConfirm={() => mutation.mutate({ targetMembershipId: membershipId, targetStatus: status })}
      />
      <WriteFeedback message={feedback} error={visibleError} />
    </div>
  );
}

export function SupportWriteSurface({
  action,
  session,
}: {
  action: SupportWriteActionKind;
  session: SupportModeSession;
}) {
  return (
    <section className="space-y-4" data-testid={`support-write-${action}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">alteração aprovada</Badge>
        <span className="text-xs text-muted-foreground">Grant {session.grantId.slice(0, 8)}</span>
      </div>
      {action === 'update-workspace-area' && <WorkspaceWrite session={session} />}
      {action === 'update-team-member' && <TeamMemberWrite session={session} />}
      {action === 'create-client' && <ClientWrite session={session} />}
      {action === 'update-matter-next-action' && <MatterWrite session={session} />}
      {action === 'update-task-title' && <TaskWrite session={session} />}
      {action === 'update-brand-tagline' && <ConfigurationWrite session={session} />}
    </section>
  );
}
