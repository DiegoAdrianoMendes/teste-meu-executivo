import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Landmark, Pause, Pencil, Play, Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/hooks/use-toast';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { AddMonitoredProcessDialog } from '@/klm/components/AddMonitoredProcessDialog';
import { EditMonitoredProcessDialog } from '@/klm/components/EditMonitoredProcessDialog';
import { estadoOab, rotuloTribunal } from '@/klm/lib/oab';
import {
  DeadlineMemoryDialog,
  DeadlinePublicationRow,
  ProcessMonitoringModuleGate,
  useDeadlineMemoryDialog,
} from '@/klm/components/ProcessMonitoringShared';
import {
  isModuleNotContractedError,
  useConfirmProcessDeadline,
  useDismissProcessDeadline,
  useMonitoredProcesses,
  usePauseMonitoredProcess,
  useProcessPublications,
  useResumeMonitoredProcess,
} from '@/klm/api/useProcessos';
import type { KlmMonitoredProcess } from '@/klm/api';
import { useAppStore } from '@/store/app-context';
import { fetchWorkspacePlanSummary } from '@/services/admin';
import { ApiError } from '@/services/http';
import { useSearchParams } from 'react-router-dom';

const STATUS_TONE: Record<KlmMonitoredProcess['status'], 'green' | 'muted' | 'red'> = {
  ativo: 'green',
  pausado: 'muted',
  erro: 'red',
};

const MODULE_GATE_DESCRIPTION =
  'O monitoramento de processos é um módulo adicional: intimações do DJEN lidas diariamente, ' +
  'movimentações via DataJud, prazos calculados com memória de cálculo auditável e aviso no ' +
  'WhatsApp do responsável. Fale com o suporte para ativar na sua conta.';

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { message?: string } | undefined;
    return detail?.message ?? error.message;
  }
  return error instanceof Error ? error.message : 'Tente novamente.';
}

export default function ProcessosPage() {
  const { toast } = useToast();
  const { can } = useAppStore();
  const [searchParams, setSearchParams] = useSearchParams();
  const canManage = can('processos', 'manage');
  const [addOpen, setAddOpen] = useState(false);
  const [editing, setEditing] = useState<KlmMonitoredProcess | null>(null);

  useEffect(() => {
    if (searchParams.get('action') !== 'new' || !canManage) return;
    setAddOpen(true);
    const next = new URLSearchParams(searchParams);
    next.delete('action');
    setSearchParams(next, { replace: true });
  }, [canManage, searchParams, setSearchParams]);

  const planSummaryQuery = useQuery({ queryKey: ['klm', 'workspace-plan-summary'], queryFn: fetchWorkspacePlanSummary });
  const processesQuery = useMonitoredProcesses();
  const publicationsQuery = useProcessPublications();

  const pauseMutation = usePauseMonitoredProcess();
  const resumeMutation = useResumeMonitoredProcess();
  const confirmMutation = useConfirmProcessDeadline();
  const dismissMutation = useDismissProcessDeadline();
  const memoryDialog = useDeadlineMemoryDialog();

  const moduleNotContracted = isModuleNotContractedError(processesQuery.error);
  const plan = planSummaryQuery.data;
  const quotaLabel = plan && plan.maxMonitoredProcesses != null
    ? `${plan.monitoredProcessesUsed} de ${plan.maxMonitoredProcesses} monitorados`
    : plan
      ? `${plan.monitoredProcessesUsed} monitorados`
      : null;
  const quotaAtLimit = plan?.monitoredProcessesRemaining === 0;

  const togglePause = (process: KlmMonitoredProcess) => {
    const mutation = process.status === 'pausado' ? resumeMutation : pauseMutation;
    mutation.mutate(process.id, {
      onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível atualizar o processo', description: errorMessage(error) }),
    });
  };

  return (
    <div className="space-y-4 p-4">
      <KlmSectionTitle
        title="Processos"
        description="Monitoramento de intimações (DJEN) e movimentações (DataJud), com prazos calculados e confirmação humana."
        action={
          <div className="flex items-center gap-2">
            {quotaLabel && !moduleNotContracted && (
              <KlmPill tone={quotaAtLimit ? 'red' : 'blue'} data-testid="processos-quota-pill">{quotaLabel}</KlmPill>
            )}
            <Button
              size="sm"
              disabled={!canManage}
              onClick={() => setAddOpen(true)}
              title={!canManage ? 'Permissão processos.manage necessária' : undefined}
            >
              <Plus className="mr-1.5 h-4 w-4" />Adicionar processo
            </Button>
          </div>
        }
      />

      {processesQuery.isPending && (
        <div className="grid gap-3">{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-14 rounded-lg" />)}</div>
      )}

      {!processesQuery.isPending && moduleNotContracted && (
        <ProcessMonitoringModuleGate description={MODULE_GATE_DESCRIPTION} />
      )}

      {!processesQuery.isPending && !moduleNotContracted && processesQuery.isError && (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          Não foi possível consultar os processos agora. Tente novamente em instantes.
        </p>
      )}

      {!processesQuery.isPending && !moduleNotContracted && processesQuery.isSuccess && (
        <Tabs defaultValue="processos" className="space-y-4">
          <TabsList className="h-9 bg-secondary p-1">
            <TabsTrigger value="processos" className="text-xs">Processos</TabsTrigger>
            <TabsTrigger value="publicacoes" className="text-xs">Publicações e intimações</TabsTrigger>
          </TabsList>

          <TabsContent value="processos">
            <KlmPanel>
              {processesQuery.data.length === 0 ? (
                <p className="p-6 text-center text-xs text-muted-foreground">
                  Nenhum processo monitorado ainda. Cadastre o primeiro pelo número CNJ.
                </p>
              ) : (
                <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>CNJ</TableHead>
                      <TableHead>Tribunal</TableHead>
                      <TableHead>OAB</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Última movimentação</TableHead>
                      <TableHead>Erro</TableHead>
                      <TableHead className="text-right">Ação</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {processesQuery.data.map((process) => {
                      const oab = estadoOab(process.oabVinculada);
                      return (
                      <TableRow key={process.id}>
                        <TableCell className="font-medium tabular-nums">
                          <span className="flex items-center gap-2">
                            <Landmark className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                            {process.numeroCnj}
                          </span>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">{rotuloTribunal(process.tribunal)}</TableCell>
                        <TableCell className="text-xs tabular-nums">
                          {oab === 'canonica' && process.oabVinculada}
                          {oab === 'ausente' && (
                            <KlmPill tone="muted" title="Sem OAB o processo é acompanhado só pelo DataJud; as intimações do DJEN não chegam.">
                              sem OAB
                            </KlmPill>
                          )}
                          {oab === 'invalida' && (
                            <KlmPill tone="red" title={`"${process.oabVinculada}" não está no formato 123456/UF; o DJEN não é consultado até corrigir.`}>
                              OAB inválida, edite
                            </KlmPill>
                          )}
                        </TableCell>
                        <TableCell><KlmPill tone={STATUS_TONE[process.status]}>{process.status}</KlmPill></TableCell>
                        <TableCell className="text-xs tabular-nums text-muted-foreground">
                          {process.lastMovementAt ? new Date(process.lastMovementAt).toLocaleDateString('pt-BR') : '—'}
                        </TableCell>
                        <TableCell className="text-xs">
                          {process.lastError
                            ? <KlmPill tone="red" title={process.lastError} data-testid="processo-last-error">erro na consulta</KlmPill>
                            : <span className="text-muted-foreground">—</span>}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={!canManage}
                              onClick={() => setEditing(process)}
                              title={!canManage ? 'Permissão processos.manage necessária' : undefined}
                            >
                              <Pencil className="mr-1 h-3.5 w-3.5" />Editar
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={!canManage || pauseMutation.isPending || resumeMutation.isPending}
                              onClick={() => togglePause(process)}
                              title={!canManage ? 'Permissão processos.manage necessária' : undefined}
                            >
                              {process.status === 'pausado'
                                ? <><Play className="mr-1 h-3.5 w-3.5" />Retomar</>
                                : <><Pause className="mr-1 h-3.5 w-3.5" />Pausar</>}
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
                </div>
              )}
            </KlmPanel>
          </TabsContent>

          <TabsContent value="publicacoes">
            <KlmPanel>
              {publicationsQuery.isPending && (
                <div className="grid gap-3 p-4">{Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-16 rounded-lg" />)}</div>
              )}
              {publicationsQuery.isSuccess && publicationsQuery.data.length === 0 && (
                <p className="p-6 text-center text-xs text-muted-foreground">Nenhuma publicação ingerida ainda.</p>
              )}
              {publicationsQuery.isSuccess && publicationsQuery.data.length > 0 && (
                <div>
                  {publicationsQuery.data.map((publication) => (
                    <DeadlinePublicationRow
                      key={publication.id}
                      publication={publication}
                      canManage={canManage}
                      onViewMemory={() => memoryDialog.show(publication)}
                      onConfirm={() => confirmMutation.mutate(publication.id, {
                        onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível confirmar o prazo', description: errorMessage(error) }),
                      })}
                      onDismiss={() => dismissMutation.mutate(publication.id, {
                        onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível dispensar o prazo', description: errorMessage(error) }),
                      })}
                      confirmPending={confirmMutation.isPending}
                      dismissPending={dismissMutation.isPending}
                    />
                  ))}
                </div>
              )}
            </KlmPanel>
          </TabsContent>
        </Tabs>
      )}

      <AddMonitoredProcessDialog open={addOpen} onOpenChange={setAddOpen} canManage={canManage} />
      <EditMonitoredProcessDialog
        process={editing}
        open={editing !== null}
        onOpenChange={(next) => { if (!next) setEditing(null); }}
        canManage={canManage}
      />
      <DeadlineMemoryDialog publication={memoryDialog.publication} open={memoryDialog.open} onOpenChange={memoryDialog.onOpenChange} />
    </div>
  );
}
