import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Pause, Play, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { useToast } from '@/hooks/use-toast';
import { klmApi, type KlmFinanceRecurrenceStatus } from '@/klm/api';
import { formatBRL } from '@/lib/formatters';

const STATUS_LABEL: Record<KlmFinanceRecurrenceStatus, string> = {
  ativa: 'Ativa',
  pausada: 'Pausada',
  encerrada: 'Encerrada',
};

const STATUS_TONE: Record<KlmFinanceRecurrenceStatus, 'green' | 'gold' | 'muted'> = {
  ativa: 'green',
  pausada: 'gold',
  encerrada: 'muted',
};

const CYCLE_LABEL: Record<string, string> = {
  semanal: 'Semanal',
  quinzenal: 'Quinzenal',
  mensal: 'Mensal',
  bimestral: 'Bimestral',
  trimestral: 'Trimestral',
  semestral: 'Semestral',
  anual: 'Anual',
};

const KIND_LABEL: Record<string, string> = {
  assinatura_gateway: 'Assinatura no gateway',
  geracao_programada: 'Geração agendada local',
};

type StatusFilter = 'todas' | KlmFinanceRecurrenceStatus;

/**
 * Painel "Recorrências" (F3) — gestão direta das recorrências financeiras
 * já criadas (listar, ver, pausar, retomar, encerrar). Consome
 * app/api/v1/endpoints/finance_recurrences.py. Superfície única também para
 * mensalidades geradas a partir de honorários retainer (FeeArrangementsPanel
 * > "Gerar cobrança" > mensalidade): hoje aquele fluxo só mostra um toast na
 * criação — a recorrência criada cai na mesma tabela `finance_recurrences` e
 * passa a aparecer e ser gerenciável aqui.
 */
export function RecurrencesPanel({ canManage }: { canManage: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('todas');

  const funnelQuery = useQuery({ queryKey: ['klm', 'recurrences-funnel'], queryFn: klmApi.funnel });
  const clientNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const card of funnelQuery.data?.cards ?? []) {
      if (!map.has(card.clientId)) map.set(card.clientId, card.name);
    }
    return map;
  }, [funnelQuery.data]);

  const recurrencesQuery = useQuery({
    queryKey: ['klm', 'finance-recurrences', statusFilter],
    queryFn: () => klmApi.financeRecurrences(statusFilter === 'todas' ? {} : { status: statusFilter }),
  });
  const recurrences = recurrencesQuery.data ?? [];

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['klm', 'finance-recurrences'] });

  const pauseMutation = useMutation({
    mutationFn: (id: string) => klmApi.financeRecurrencePause(id),
    onSuccess: () => {
      invalidate();
      toast({ title: 'Recorrência pausada.', description: 'O tick agendado para de gerar novos lançamentos.' });
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível pausar', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  const resumeMutation = useMutation({
    mutationFn: (id: string) => klmApi.financeRecurrenceResume(id),
    onSuccess: () => {
      invalidate();
      toast({ title: 'Recorrência retomada.' });
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível retomar', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  const endMutation = useMutation({
    mutationFn: (id: string) => klmApi.financeRecurrenceEnd(id),
    onSuccess: () => {
      invalidate();
      toast({ title: 'Recorrência encerrada.', description: 'Esta ação é definitiva — a recorrência não volta a gerar lançamentos.' });
    },
    onError: (error) => {
      toast({ variant: 'destructive', title: 'Não foi possível encerrar', description: error instanceof Error ? error.message : 'Tente novamente.' });
    },
  });

  const anyActionPending = pauseMutation.isPending || resumeMutation.isPending || endMutation.isPending;

  return (
    <KlmPanel className="p-4">
      <KlmSectionTitle
        title="Recorrências"
        description="Assinaturas no gateway e gerações programadas locais — inclui mensalidades de honorários retainer. Pause, retome ou encerre sem sair daqui."
        action={
          <Select value={statusFilter} onValueChange={(value) => setStatusFilter(value as StatusFilter)}>
            <SelectTrigger className="h-8 w-40 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="todas">Todas</SelectItem>
              <SelectItem value="ativa">Ativas</SelectItem>
              <SelectItem value="pausada">Pausadas</SelectItem>
              <SelectItem value="encerrada">Encerradas</SelectItem>
            </SelectContent>
          </Select>
        }
      />

      <div className="mt-4 overflow-x-auto rounded-lg border border-border">
        {recurrencesQuery.isPending ? (
          <div className="flex min-h-32 items-center justify-center gap-2 p-6 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />Carregando recorrências...
          </div>
        ) : recurrencesQuery.isError ? (
          <div className="p-6 text-center text-xs text-muted-foreground">Não foi possível carregar as recorrências.</div>
        ) : recurrences.length === 0 ? (
          <div className="p-10 text-center text-xs text-muted-foreground">
            Nenhuma recorrência encontrada para este filtro.
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">Recorrência</TableHead>
                <TableHead className="text-xs">Ciclo</TableHead>
                <TableHead className="text-xs">Próxima geração</TableHead>
                <TableHead className="text-xs">Status</TableHead>
                <TableHead className="w-[230px] text-right text-xs">Ação</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recurrences.map((recurrence) => (
                <TableRow key={recurrence.id}>
                  <TableCell className="align-top">
                    <p className="text-sm font-medium">{recurrence.entryTitle}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {clientNameById.get(recurrence.clientId) ?? 'Cliente'} · <span className="tabular-nums">{formatBRL(recurrence.entryValue)}</span> · {KIND_LABEL[recurrence.kind] ?? recurrence.kind}
                    </p>
                    {recurrence.generatedCount > 0 && (
                      <p className="mt-1 text-2xs text-muted-foreground">
                        {recurrence.generatedCount} gerada(s){recurrence.maxCount ? ` de ${recurrence.maxCount}` : ''}
                      </p>
                    )}
                  </TableCell>
                  <TableCell className="align-top text-xs">{CYCLE_LABEL[recurrence.cycle] ?? recurrence.cycle}</TableCell>
                  <TableCell className="align-top text-xs tabular-nums">
                    {recurrence.status === 'encerrada' ? '—' : new Date(recurrence.nextRun).toLocaleDateString('pt-BR')}
                  </TableCell>
                  <TableCell className="align-top">
                    <KlmPill tone={STATUS_TONE[recurrence.status]}>{STATUS_LABEL[recurrence.status]}</KlmPill>
                  </TableCell>
                  <TableCell className="align-top text-right">
                    <div className="flex justify-end gap-2">
                      {recurrence.status === 'ativa' && (
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 text-2xs"
                          disabled={!canManage || anyActionPending}
                          title={!canManage ? 'Permissão finance.manage necessária' : undefined}
                          onClick={() => pauseMutation.mutate(recurrence.id)}
                        >
                          <Pause className="mr-1 h-3 w-3" />Pausar
                        </Button>
                      )}
                      {recurrence.status === 'pausada' && (
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 text-2xs"
                          disabled={!canManage || anyActionPending}
                          title={!canManage ? 'Permissão finance.manage necessária' : undefined}
                          onClick={() => resumeMutation.mutate(recurrence.id)}
                        >
                          <Play className="mr-1 h-3 w-3" />Retomar
                        </Button>
                      )}
                      {recurrence.status !== 'encerrada' && (
                        <Button
                          size="sm"
                          className="h-7 bg-[#031121] text-2xs text-white hover:bg-[#031121]"
                          disabled={!canManage || anyActionPending}
                          title={!canManage ? 'Permissão finance.manage necessária' : undefined}
                          onClick={() => endMutation.mutate(recurrence.id)}
                        >
                          <Square className="mr-1 h-3 w-3" />Encerrar
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </KlmPanel>
  );
}
