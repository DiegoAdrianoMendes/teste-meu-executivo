import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ChevronRight, Plus, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { NewLegalDemandDialog } from '@/klm/components/NewLegalDemandDialog';
import { isLegalDemandAccessDeniedError, useLegalDemands } from '@/klm/api/useLegalDemands';
import { klmApi, type KlmLegalDemandNature, type KlmLegalDemandPhase } from '@/klm/api';
import {
  LEGAL_DEMAND_NATURE_LABELS,
  LEGAL_DEMAND_NATURES,
  LEGAL_DEMAND_PHASES,
  LEGAL_DEMAND_PHASE_LABELS,
  LEGAL_DEMAND_PHASE_TONE,
  LEGAL_DEMAND_RISK_LABELS,
  LEGAL_DEMAND_RISK_TONE,
} from '@/klm/lib/legal-demand-labels';
import { useAppStore } from '@/store/app-context';

const NATURE_FILTER_ALL = 'todas';
const PHASE_FILTER_ALL = 'todas';

/**
 * Tela-mãe da Cadeia Jurídica — lista as demandas canônicas (Onda 2,
 * `GET /legal/demands`) com filtro por natureza/fase e abre o detalhe em
 * abas (`DemandaDetailPage`). Módulo de permissão real: `legal`
 * (`require_permission("legal", ...)` no backend); a rota usa o gate
 * genérico `lista` (mesmo padrão de `ProcessosPage`) e esta página mostra o
 * estado honesto quando a chamada real devolve 403 — nunca lista vazia
 * silenciosa.
 */
export default function DemandasPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { can } = useAppStore();
  const canManage = can('legal', 'manage');
  const [natureFilter, setNatureFilter] = useState<typeof NATURE_FILTER_ALL | KlmLegalDemandNature>(NATURE_FILTER_ALL);
  const [phaseFilter, setPhaseFilter] = useState<typeof PHASE_FILTER_ALL | KlmLegalDemandPhase>(PHASE_FILTER_ALL);
  const [createOpen, setCreateOpen] = useState(false);

  useEffect(() => {
    if (searchParams.get('action') !== 'new' || !canManage) return;
    setCreateOpen(true);
    const next = new URLSearchParams(searchParams);
    next.delete('action');
    setSearchParams(next, { replace: true });
  }, [canManage, searchParams, setSearchParams]);

  const demandsQuery = useLegalDemands({
    nature: natureFilter === NATURE_FILTER_ALL ? undefined : natureFilter,
    phase: phaseFilter === PHASE_FILTER_ALL ? undefined : phaseFilter,
  });

  // Resolução best-effort do nome do cliente/caso a partir dos mesmos cards
  // do funil comercial (não há endpoint dedicado de busca de cliente por id
  // — mesmo racional de `NewLegalDemandDialog`/`AddMonitoredProcessDialog`).
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel });
  const namesByClientOrMatter = useMemo(() => {
    const map = new Map<string, string>();
    for (const card of funnelQuery.data?.cards ?? []) {
      map.set(card.clientId, card.name);
      map.set(card.id, card.name);
    }
    return map;
  }, [funnelQuery.data]);

  const accessDenied = isLegalDemandAccessDeniedError(demandsQuery.error);
  const demands = demandsQuery.data?.items ?? [];

  return (
    <div className="space-y-4 p-4">
      <KlmSectionTitle
        title="Demandas"
        description="Cadeia jurídica canônica — judicial, administrativo, consultivo, contrato, acordo, serviço recorrente e massificado, unificados em um só lugar."
        action={
          <Button
            size="sm"
            disabled={!canManage}
            onClick={() => setCreateOpen(true)}
            title={!canManage ? 'Permissão legal.manage necessária' : undefined}
          >
            <Plus className="mr-1.5 h-4 w-4" />Nova demanda
          </Button>
        }
      />

      {!demandsQuery.isPending && !accessDenied && (
        <div className="flex flex-wrap items-center gap-2">
          <Select value={natureFilter} onValueChange={(value) => setNatureFilter(value as typeof natureFilter)}>
            <SelectTrigger className="h-8 w-[200px] text-xs"><SelectValue placeholder="Natureza" /></SelectTrigger>
            <SelectContent>
              <SelectItem value={NATURE_FILTER_ALL}>Todas as naturezas</SelectItem>
              {LEGAL_DEMAND_NATURES.map((value) => (
                <SelectItem key={value} value={value}>{LEGAL_DEMAND_NATURE_LABELS[value]}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={phaseFilter} onValueChange={(value) => setPhaseFilter(value as typeof phaseFilter)}>
            <SelectTrigger className="h-8 w-[180px] text-xs"><SelectValue placeholder="Fase" /></SelectTrigger>
            <SelectContent>
              <SelectItem value={PHASE_FILTER_ALL}>Todas as fases</SelectItem>
              {LEGAL_DEMAND_PHASES.map((value) => (
                <SelectItem key={value} value={value}>{LEGAL_DEMAND_PHASE_LABELS[value]}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {demandsQuery.isPending && (
        <div className="grid gap-3">{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-14 rounded-lg" />)}</div>
      )}

      {!demandsQuery.isPending && accessDenied && (
        <KlmPanel>
          <div className="flex items-start gap-3 p-4" data-testid="demandas-access-denied">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary">
              <ShieldAlert className="h-4 w-4 text-muted-foreground" />
            </span>
            <div>
              <p className="text-sm font-semibold">Acesso ao módulo jurídico restrito</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                Seu papel atual não tem a permissão jurídica (módulo <code>legal</code>) necessária para ver
                demandas. Peça ao administrador do workspace para conceder o acesso no seu perfil.
              </p>
            </div>
          </div>
        </KlmPanel>
      )}

      {!demandsQuery.isPending && !accessDenied && demandsQuery.isError && (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          Não foi possível consultar as demandas agora. Tente novamente em instantes.
        </p>
      )}

      {!demandsQuery.isPending && !accessDenied && demandsQuery.isSuccess && (
        <KlmPanel>
          {demands.length === 0 ? (
            <p className="p-6 text-center text-xs text-muted-foreground">
              Nenhuma demanda cadastrada ainda. Crie a primeira pelo botão "Nova demanda".
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Natureza</TableHead>
                  <TableHead>Área</TableHead>
                  <TableHead>Cliente / caso</TableHead>
                  <TableHead>Fase</TableHead>
                  <TableHead>Risco</TableHead>
                  <TableHead className="text-right">Atualizada em</TableHead>
                  <TableHead className="w-8" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {demands.map((demand) => {
                  const label = namesByClientOrMatter.get(demand.matterId ?? '') ?? namesByClientOrMatter.get(demand.clientId);
                  return (
                    <TableRow
                      key={demand.id}
                      className="cursor-pointer"
                      data-testid={`demanda-row-${demand.id}`}
                      onClick={() => navigate(`/app/demandas/${demand.id}`)}
                    >
                      <TableCell className="font-medium">{LEGAL_DEMAND_NATURE_LABELS[demand.nature]}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{demand.area ?? '—'}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {label ?? <span className="tabular-nums">{demand.clientId.slice(0, 8)}…</span>}
                      </TableCell>
                      <TableCell><KlmPill tone={LEGAL_DEMAND_PHASE_TONE[demand.phase]}>{LEGAL_DEMAND_PHASE_LABELS[demand.phase]}</KlmPill></TableCell>
                      <TableCell><KlmPill tone={LEGAL_DEMAND_RISK_TONE[demand.risk]}>{LEGAL_DEMAND_RISK_LABELS[demand.risk]}</KlmPill></TableCell>
                      <TableCell className="text-right text-xs tabular-nums text-muted-foreground">
                        {new Date(demand.updatedAt).toLocaleDateString('pt-BR')}
                      </TableCell>
                      <TableCell><ChevronRight className="h-3.5 w-3.5 text-muted-foreground" /></TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </KlmPanel>
      )}

      <NewLegalDemandDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        canManage={canManage}
        onCreated={(demandId) => navigate(`/app/demandas/${demandId}`)}
      />
    </div>
  );
}
