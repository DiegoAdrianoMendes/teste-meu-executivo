import { useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BarChart3, Download, Filter, RefreshCw, ShieldCheck, Trophy } from 'lucide-react';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { KlmMetricCard, KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import { NativeCRMOperationsPanel } from '@/klm/components/NativeCRMOperationsPanel';
import { NativeModuleBar } from '@/klm/components/NativeModuleBar';
import { AdvancedReportsPanel } from '@/klm/components/AdvancedReportsPanel';
import { BiHonestyBanner, BiMetricValue } from '@/klm/components/BiHonestyBanner';
import { klmApi } from '@/klm/api';
import { formatBRL, formatDecimal } from '@/lib/formatters';
import { CustomPeriodButton } from '@/components/common/CustomPeriodButton';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';
import {
  isLegalBiAccessDeniedError,
  useLegalBiIa,
  useLegalBiJuridico,
  useLegalBiOperacao,
  useLegalCapacity,
} from '@/klm/api/useLegalBi';

const KPI_FAMILY_CATALOG = [
  { key: 'acquisition', title: 'Aquisição', description: 'Spend, impressões, CTR, CPC, CPL, MQL, CAC lead, CAC cliente, ROAS, pixel/CAPI, UTM e duplicidade.' },
  { key: 'funnel', title: 'Funil', description: 'Conversão por etapa, aging, win rate, motivo de perda, follow-up, proposta aceita e no-show recuperado.' },
  { key: 'service', title: 'Atendimento', description: 'Primeira resposta, mediana, sem resposta, autoria, handoff, humano assumiu, Áudio IA, opt-out e bloqueio.' },
  { key: 'documents', title: 'Docs/RAG', description: 'Recebidos, faltantes, OCR, classificação, confiança, match, latência de indexação e suficiência.' },
  { key: 'meetings', title: 'Reuniões', description: 'Marcadas, confirmadas, realizadas, no-show, remarcadas, convertidas, lembrete e tempo até agenda.' },
  { key: 'fees', title: 'Honorários', description: 'Inicial, final, êxito, desconto, com/sem valor econômico, payback, receita esperada e margem.' },
  { key: 'finance', title: 'Financeiro', description: 'Caixa, contas a receber, vencido, inadimplência, cobrança, promessa, erro do provedor e LTV por canal.' },
  { key: 'quality', title: 'Agentes/QA', description: 'Score, revisão, correção, confiança, escalonamento, carga, eval, prompt e resposta ruim.' },
  { key: 'flows', title: 'Flows/Disparos', description: 'Starts, conclusões, drop-off, retry, opt-out, loop guard, receita atribuída, A/B e aprovação.' },
  { key: 'marketing', title: 'Marketing', description: 'Criativo vencedor, fadiga, gap de oferta, remarketing, audiência, lead ruim e CAC marginal.' },
  { key: 'reliability', title: 'TI/Confiabilidade', description: 'Bugs, MTTR, PR lead time, deploy falho, erro de API/webhook, rollback e acessos.' },
  { key: 'future_areas', title: 'Áreas futuras', description: 'Taxonomia, templates, docs esperados, agentes habilitados, KPI por área e ramp-up.' },
] as const;

type Metric = { key: string; label: string; value: number | null; unit: string };
type MetricRow = readonly [string, string, number | null, string];
type ExportFormat = 'csv' | 'json';
type KpiFamilyKey = (typeof KPI_FAMILY_CATALOG)[number]['key'];
type FamilyFilter = 'all' | KpiFamilyKey;
type ChartDatum = { name: string; value: number; unit: string };

const FAMILY_METRIC_TERMS: Record<KpiFamilyKey, string[]> = {
  acquisition: ['lead', 'cac', 'cpl', 'ctr', 'cpc', 'roas', 'spend', 'impress', 'channel', 'pixel', 'utm'],
  funnel: ['qualified', 'contract', 'conversion', 'pipeline', 'proposal', 'meeting', 'win', 'loss'],
  service: ['thread', 'response', 'follow', 'handoff', 'approval', 'attendance', 'service'],
  documents: ['doc', 'rag', 'ocr', 'index', 'sufficien'],
  meetings: ['meeting', 'reun', 'agenda', 'no_show', 'no-show'],
  fees: ['fee', 'honor', 'economic', 'margin', 'payback'],
  finance: ['paid', 'revenue', 'receita', 'cash', 'invoice', 'overdue', 'receivable', 'finance'],
  quality: ['quality', 'score', 'review', 'confidence', 'agent', 'eval'],
  flows: ['flow', 'broadcast', 'disparo', 'retry', 'opt_out', 'opt-out', 'automation'],
  marketing: ['marketing', 'creative', 'campaign', 'audience', 'remarketing'],
  reliability: ['api', 'webhook', 'bug', 'mttr', 'deploy', 'rollback', 'runtime'],
  future_areas: ['taxonomy', 'template', 'ramp', 'area'],
};

const CHART_COLORS = ['#0057d8', '#1a9948', '#d28b16', '#7c5ce7', '#b94a48', '#1698a5'];

function belongsToFamily(metric: Metric, family: KpiFamilyKey) {
  const searchable = normalize(`${metric.key} ${metric.label}`);
  return FAMILY_METRIC_TERMS[family].some((term) => searchable.includes(normalize(term)));
}

function MetricRows({ rows }: { rows: ReadonlyArray<MetricRow> }) {
  return (
    <div className="mt-4 space-y-3">
      {rows.map(([label, value, progress, meta]) => (
        <div key={label}>
          <div className="mb-1 flex items-center gap-2 text-2xs"><span className="min-w-[100px] font-semibold">{label}</span><span className="text-[#0057d8] dark:text-primary">{value}</span><span className="ml-auto truncate text-muted-foreground">{meta}</span></div>
          {progress == null
            ? <p className="border-t border-dashed border-border pt-1 text-2xs text-muted-foreground">Sem percentual ou denominador agregado pela API.</p>
            : <KlmProgress value={progress} tone={progress >= 80 ? 'green' : progress < 30 ? 'red' : 'gold'} />}
        </div>
      ))}
    </div>
  );
}

function QueryState({ pending, error, empty, label }: { pending: boolean; error: boolean; empty: boolean; label: string }) {
  if (pending) return <div className="mt-4 space-y-2 py-2"><Skeleton className="h-3 w-2/3" /><Skeleton className="h-3 w-1/2" /><Skeleton className="h-3 w-1/3" /></div>;
  if (error) return <p className="mt-4 py-5 text-center text-2xs text-[#a53e3c]">Não foi possível carregar {label}.</p>;
  if (empty) return <p className="mt-4 py-5 text-center text-2xs text-muted-foreground">Nenhum dado registrado para {label}.</p>;
  return null;
}

function normalize(value: string) {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR');
}

function formatValue(value: number | null, unit: string) {
  if (value == null || !Number.isFinite(value)) return 'Sem dado';
  if (unit === 'BRL') return formatBRL(value);
  if (unit === 'percent') return `${formatDecimal(value)}%`;
  if (unit === 'ratio') return `${formatDecimal(value)}x`;
  if (unit === 'seconds') {
    const min = Math.floor(value / 60);
    const sec = Math.round(value % 60);
    return sec > 0 ? `${min} min ${sec}s` : `${min} min`;
  }
  // Contagens (leads, contratos, reuniões) são inteiros para pessoas.
  if (Number.isInteger(value)) return String(value);
  return formatDecimal(value);
}

function csvCell(value: unknown) {
  const text = value == null ? '' : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function downloadBrowserFile(filename: string, content: string, mimeType: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mimeType }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** BI jurídico + capacidade — abas acrescentadas à página existente (Onda 8,
 * Sprint C UI, decisão do dono: não item de menu novo). Contrato real:
 * `GET /legal/bi/operacao|juridico|ia` e `GET /legal/capacity` (ver
 * `app/api/v1/endpoints/legal_bi.py`). Toda aba mostra `BiHonestyBanner`
 * (corte temporal, amostra, origem, campos ausentes — Plano Mestre §15) e
 * usa `BiMetricValue` para indicadores que o backend pode declarar `null`
 * (ex.: "tempo economizado" em IA): nunca um zero fabricado. */
function BiAccessDenied({ label }: { label: string }) {
  return <KlmPanel className="p-6 text-center text-2xs text-muted-foreground">Este workspace não tem acesso a {label}. Fale com quem administra permissões (`relatorios.view`).</KlmPanel>;
}

function BiQueryState({ pending, error, isDenied, label }: { pending: boolean; error: boolean; isDenied: boolean; label: string }) {
  if (pending) return <div className="space-y-2 py-4"><Skeleton className="h-3 w-2/3" /><Skeleton className="h-3 w-1/2" /><Skeleton className="h-3 w-1/3" /></div>;
  if (isDenied) return <BiAccessDenied label={label} />;
  if (error) return <KlmPanel className="border-[#b94a48]/30 p-6 text-center text-2xs text-[#a53e3c]">Não foi possível carregar {label}.</KlmPanel>;
  return null;
}

function OperationBiTab() {
  const query = useLegalBiOperacao();
  const data = query.data;
  const isDenied = isLegalBiAccessDeniedError(query.error);
  return (
    <div className="space-y-4" data-testid="bi-tab-operacao">
      {data && <BiHonestyBanner {...data.honesty} />}
      <BiQueryState pending={query.isPending} error={query.isError} isDenied={isDenied} label="os indicadores de operação" />
      {data && <>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <KlmMetricCard label="Tarefas D0" value={String(data.tarefasD0)} delta="vencem hoje" />
          <KlmMetricCard label="Tarefas D1" value={String(data.tarefasD1)} delta="vencem amanhã" />
          <KlmMetricCard label="Tarefas atrasadas" value={String(data.tarefasAtrasadas)} delta="fora do prazo" trend={data.tarefasAtrasadas > 0 ? 'down' : 'neutral'} />
          <KlmMetricCard label="Sem data classificável" value={String(data.tarefasSemDataClassificavel)} delta="due_date fora do formato" />
          <KlmMetricCard label="Sem responsável" value={String(data.tarefasSemResponsavel)} delta="tarefas" />
          <KlmMetricCard label="Retrabalho" value={String(data.retrabalhoCorrecoesPendentes)} delta="correções pendentes → minuta" />
          <KlmMetricCard label="Minutas com múltiplas versões" value={String(data.minutasComMultiplasVersoes)} delta="no período" />
          <KlmMetricCard label="Processos sem movimentação" value={String(data.processosSemMovimentacao)} delta="monitoramento parado" />
        </div>
        <KlmPanel className="p-4">
          <KlmSectionTitle title="Tempo até cada marco da jornada" description="Média e mediana em horas, entre a captura da publicação e o evento de avanço — só quando o backend tem amostra." />
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[420px] text-2xs">
              <thead className="text-left text-muted-foreground"><tr><th className="pb-1.5 pr-3 font-medium">Marco</th><th className="pb-1.5 pr-3 font-medium">Amostra</th><th className="pb-1.5 pr-3 font-medium">Média</th><th className="pb-1.5 font-medium">Mediana</th></tr></thead>
              <tbody>{data.tempoPorMarco.map((row) => (
                <tr key={row.toState} className="border-t border-border"><td className="py-1.5 pr-3">{row.toState.replaceAll('_', ' ')}</td><td className="py-1.5 pr-3 tabular-nums">{row.sampleSize}</td><td className="py-1.5 pr-3"><BiMetricValue value={row.avgHours} suffix="h" /></td><td className="py-1.5"><BiMetricValue value={row.medianHours} suffix="h" /></td></tr>
              ))}</tbody>
            </table>
          </div>
        </KlmPanel>
        <div className="grid gap-4 lg:grid-cols-2">
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Backlog por responsável" description="Demandas fora de fase terminal, agrupadas por responsável." />
            <ul className="mt-3 space-y-1.5 text-2xs">{data.backlogPorResponsavel.length ? data.backlogPorResponsavel.map((row) => <li key={row.key} className="flex items-center justify-between border-b border-dashed border-border pb-1.5"><span className="truncate">{row.key}</span><span className="tabular-nums">{row.count}</span></li>) : <li className="text-muted-foreground">Nenhum registro no período.</li>}</ul>
          </KlmPanel>
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Exceções por origem" description="Exceções abertas no período, agrupadas por origem." />
            <ul className="mt-3 space-y-1.5 text-2xs">{data.excecoesPorOrigem.length ? data.excecoesPorOrigem.map((row) => <li key={row.key} className="flex items-center justify-between border-b border-dashed border-border pb-1.5"><span className="truncate">{row.key}</span><span className="tabular-nums">{row.count}</span></li>) : <li className="text-muted-foreground">Nenhuma exceção no período.</li>}</ul>
          </KlmPanel>
        </div>
        <KlmPanel className="p-4">
          <KlmSectionTitle title="SLA por cliente" description="Demandas ativas do cliente, por situação de SLA." />
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[420px] text-2xs">
              <thead className="text-left text-muted-foreground"><tr><th className="pb-1.5 pr-3 font-medium">Cliente</th><th className="pb-1.5 pr-3 font-medium">No prazo</th><th className="pb-1.5 pr-3 font-medium">Atrasado</th><th className="pb-1.5 font-medium">Sem SLA definido</th></tr></thead>
              <tbody>{data.slaPorCliente.length ? data.slaPorCliente.map((row) => (
                <tr key={row.clientId} className="border-t border-border"><td className="py-1.5 pr-3 truncate">{row.clientName}</td><td className="py-1.5 pr-3 tabular-nums">{row.noPrazo}</td><td className="py-1.5 pr-3 tabular-nums">{row.atrasado}</td><td className="py-1.5 tabular-nums">{row.semSlaDefinido}</td></tr>
              )) : <tr><td colSpan={4} className="py-3 text-center text-muted-foreground">Nenhum cliente com demanda ativa.</td></tr>}</tbody>
            </table>
          </div>
        </KlmPanel>
      </>}
    </div>
  );
}

function LegalBiTab() {
  const query = useLegalBiJuridico();
  const data = query.data;
  const isDenied = isLegalBiAccessDeniedError(query.error);
  return (
    <div className="space-y-4" data-testid="bi-tab-juridico">
      {data && <BiHonestyBanner {...data.honesty} />}
      <BiQueryState pending={query.isPending} error={query.isError} isDenied={isDenied} label="os indicadores jurídicos" />
      {data && <>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <KlmMetricCard label="Entradas no período" value={String(data.entradasNoPeriodo)} delta="novas demandas" />
          <KlmMetricCard label="Saídas no período" value={String(data.saidasNoPeriodo)} delta="para fase terminal" />
          <KlmMetricCard label="Acordos no período" value={String(data.acordosNoPeriodo)} delta="natureza = acordo" />
          <KlmMetricCard label="Com estratégia registrada" value={String(data.demandasComEstrategiaRegistrada)} delta="no período" />
          <KlmMetricCard label="Sem matter vinculado" value={String(data.resultadoSemMatterVinculado)} delta="fase terminal" />
          <KlmMetricCard label="Motivo de encerramento registrado" value={String(data.motivoEncerramentoRegistrado)} delta="no período" />
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Estoque por fase" description="Demandas do workspace, contagem atual por fase." />
            <ul className="mt-3 space-y-1.5 text-2xs">{data.estoquePorFase.map((row) => <li key={row.key} className="flex items-center justify-between border-b border-dashed border-border pb-1.5"><span className="truncate">{row.key.replaceAll('_', ' ')}</span><span className="tabular-nums">{row.count}</span></li>)}</ul>
          </KlmPanel>
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Resultado por coluna do kanban" description="Só demandas com matter vinculado — o restante está em 'sem matter vinculado' acima." />
            <ul className="mt-3 space-y-1.5 text-2xs">{data.resultadoPorColunaKanban.length ? data.resultadoPorColunaKanban.map((row) => <li key={row.key} className="flex items-center justify-between border-b border-dashed border-border pb-1.5"><span className="truncate">{row.key.replaceAll('_', ' ')}</span><span className="tabular-nums">{row.count}</span></li>) : <li className="text-muted-foreground">Nenhuma demanda terminal com matter vinculado.</li>}</ul>
          </KlmPanel>
        </div>
        <KlmPanel className="p-4">
          <KlmSectionTitle title="Duração média por natureza" description="Aproximação via updated_at da demanda em fase terminal — ver metadado de honestidade acima." />
          <ul className="mt-3 space-y-1.5 text-2xs">{data.duracaoMediaDiasPorNatureza.length ? data.duracaoMediaDiasPorNatureza.map((row) => <li key={row.nature} className="flex items-center justify-between border-b border-dashed border-border pb-1.5"><span className="truncate">{row.nature.replaceAll('_', ' ')} · amostra {row.sampleSize}</span><span className="tabular-nums">{formatDecimal(row.avgDays, 1)} dias</span></li>) : <li className="text-muted-foreground">Nenhuma demanda terminal com datas válidas.</li>}</ul>
        </KlmPanel>
        <div className="grid gap-4 lg:grid-cols-2">
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Valores por fase" description="Soma de LegalDemand.value, só demandas com valor informado." />
            <ul className="mt-3 space-y-1.5 text-2xs">{data.valoresPorFase.length ? data.valoresPorFase.map((row) => <li key={row.key} className="flex items-center justify-between border-b border-dashed border-border pb-1.5"><span className="truncate">{row.key.replaceAll('_', ' ')} · {row.count} demanda(s)</span><span className="tabular-nums">{formatBRL(row.totalValue)}</span></li>) : <li className="text-muted-foreground">Nenhuma demanda com valor informado.</li>}</ul>
          </KlmPanel>
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Contingência por risco" description="Demandas ativas com valor informado, agrupadas por risco." />
            <ul className="mt-3 space-y-1.5 text-2xs">{data.contingenciaPorRisco.length ? data.contingenciaPorRisco.map((row) => <li key={row.key} className="flex items-center justify-between border-b border-dashed border-border pb-1.5"><span className="truncate">{row.key.replaceAll('_', ' ')} · {row.count} demanda(s)</span><span className="tabular-nums">{formatBRL(row.totalValue)}</span></li>) : <li className="text-muted-foreground">Nenhuma demanda ativa com valor informado.</li>}</ul>
          </KlmPanel>
        </div>
      </>}
    </div>
  );
}

function AiBiTab() {
  const query = useLegalBiIa();
  const data = query.data;
  const isDenied = isLegalBiAccessDeniedError(query.error);
  return (
    <div className="space-y-4" data-testid="bi-tab-ia">
      {data && <BiHonestyBanner {...data.honesty} />}
      <BiQueryState pending={query.isPending} error={query.isError} isDenied={isDenied} label="os indicadores de IA" />
      {data && <>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <KlmMetricCard label="Execuções totais" value={String(data.execucoesTotais)} delta="no período" />
          <KlmMetricCard label="Execuções com falha" value={String(data.execucoesFalhas)} delta="status = failed" trend={data.execucoesFalhas > 0 ? 'down' : 'neutral'} />
          <KlmMetricCard label="Aguardando humano" value={String(data.execucoesAguardandoHumano)} delta="proxy de abstention" />
          <KlmMetricCard label="Intervenções humanas" value={String(data.intervencoesHumanasTotal)} delta="human overrides" />
          <KlmMetricCard label="Decisões aceitas" value={String(data.decisoesAceitas)} delta="override = accepted" />
          <KlmMetricCard label="Decisões corrigidas" value={String(data.decisoesCorrigidas)} delta="override = corrected" />
          <KlmMetricCard label="Decisões rejeitadas" value={String(data.decisoesRejeitadas)} delta="override = rejected" />
          <KlmMetricCard label="Tokens (entrada/saída)" value={`${data.tokensInputTotais.toLocaleString('pt-BR')} / ${data.tokensOutputTotais.toLocaleString('pt-BR')}`} delta="somados no período" />
        </div>
        <KlmPanel className="p-4">
          <div className="flex items-center justify-between"><span className="text-xs font-semibold">Taxa de intervenção humana</span><BiMetricValue value={data.taxaIntervencaoHumana == null ? null : data.taxaIntervencaoHumana * 100} suffix="%" /></div>
          <p className="mt-1 text-2xs text-muted-foreground">Intervenções ÷ execuções totais no período. Indisponível quando não há execução no recorte — nunca calculado como 0%.</p>
        </KlmPanel>
        <div className="grid gap-4 lg:grid-cols-2">
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Passos automatizados por agente" description="Execuções concluídas no período, por agente." />
            <ul className="mt-3 space-y-1.5 text-2xs">{data.passosAutomatizadosPorAgente.length ? data.passosAutomatizadosPorAgente.map((row) => <li key={row.key} className="flex items-center justify-between border-b border-dashed border-border pb-1.5"><span className="truncate">{row.key}</span><span className="tabular-nums">{row.count}</span></li>) : <li className="text-muted-foreground">Nenhuma execução concluída no período.</li>}</ul>
          </KlmPanel>
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Custo (USD) por agente" description="Soma de AgentRun.cost_usd no período." />
            <ul className="mt-3 space-y-1.5 text-2xs">{data.custoUsdPorAgente.length ? data.custoUsdPorAgente.map((row) => <li key={row.key} className="flex items-center justify-between border-b border-dashed border-border pb-1.5"><span className="truncate">{row.key} · {row.count} execução(ões)</span><span className="tabular-nums">US$ {formatDecimal(row.totalValue, 2)}</span></li>) : <li className="text-muted-foreground">Nenhum custo registrado no período.</li>}</ul>
          </KlmPanel>
        </div>
      </>}
    </div>
  );
}

function CapacityTab() {
  const query = useLegalCapacity();
  const data = query.data;
  const isDenied = isLegalBiAccessDeniedError(query.error);
  return (
    <div className="space-y-4" data-testid="bi-tab-capacidade">
      {data && <BiHonestyBanner generatedAt={data.honesty.generatedAt} sampleSize={data.honesty.sampleSize} origin={data.honesty.origin} missingFields={data.honesty.missingFields} />}
      <BiQueryState pending={query.isPending} error={query.isError} isDenied={isDenied} label="a capacidade da equipe" />
      {data && <>
        <KlmPanel className="p-4">
          <KlmSectionTitle title="Capacidade por responsável" description="Carga ativa (demandas fora de fase terminal + tarefas abertas) vs. disponibilidade configurada — nunca cruzadas em % de utilização (unidades diferentes, ver metadado acima)." />
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[620px] text-2xs">
              <thead className="text-left text-muted-foreground"><tr><th className="pb-1.5 pr-3 font-medium">Responsável</th><th className="pb-1.5 pr-3 font-medium">Demandas ativas</th><th className="pb-1.5 pr-3 font-medium">Tarefas abertas</th><th className="pb-1.5 pr-3 font-medium">Horas/semana</th><th className="pb-1.5 pr-3 font-medium">Disponível agora</th><th className="pb-1.5 font-medium">Motivo</th></tr></thead>
              <tbody>{data.porResponsavel.length ? data.porResponsavel.map((row) => (
                <tr key={row.responsibleUserId} className="border-t border-border">
                  <td className="py-1.5 pr-3 truncate">{row.responsibleName}</td>
                  <td className="py-1.5 pr-3 tabular-nums">{row.cargaDemandasAtivas}</td>
                  <td className="py-1.5 pr-3 tabular-nums">{row.cargaTarefasAbertas}</td>
                  <td className="py-1.5 pr-3"><BiMetricValue value={row.horasSemanaConfiguradas} suffix="h" /></td>
                  <td className="py-1.5 pr-3"><KlmPill tone={row.disponivelAgora ? 'green' : 'red'}>{row.disponivelAgora ? 'Sim' : 'Não'}</KlmPill></td>
                  <td className="py-1.5 text-muted-foreground">{row.motivoDisponibilidade.replaceAll('_', ' ')}</td>
                </tr>
              )) : <tr><td colSpan={6} className="py-3 text-center text-muted-foreground">Nenhum membro ativo no workspace.</td></tr>}</tbody>
            </table>
          </div>
        </KlmPanel>
        <div className="grid gap-4 lg:grid-cols-2">
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Carga por unidade" description="Soma de demandas ativas, agrupadas por LegalDemand.unit." />
            <ul className="mt-3 space-y-1.5 text-2xs">{data.porUnidade.length ? data.porUnidade.map((row) => <li key={row.key} className="flex items-center justify-between border-b border-dashed border-border pb-1.5"><span className="truncate">{row.key.replaceAll('_', ' ')}</span><span className="tabular-nums">{row.cargaDemandasAtivas}</span></li>) : <li className="text-muted-foreground">Nenhuma demanda ativa com unidade informada.</li>}</ul>
          </KlmPanel>
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Carga por equipe" description="Soma de demandas ativas, agrupadas por LegalDemand.team." />
            <ul className="mt-3 space-y-1.5 text-2xs">{data.porEquipe.length ? data.porEquipe.map((row) => <li key={row.key} className="flex items-center justify-between border-b border-dashed border-border pb-1.5"><span className="truncate">{row.key.replaceAll('_', ' ')}</span><span className="tabular-nums">{row.cargaDemandasAtivas}</span></li>) : <li className="text-muted-foreground">Nenhuma demanda ativa com equipe informada.</li>}</ul>
          </KlmPanel>
        </div>
      </>}
    </div>
  );
}

export default function KpisPage() {
  const [period, setPeriod] = useState('30');
  const [subtheme, setSubtheme] = useState('todos');
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [notice, setNotice] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('visao-geral');
  const [selectedFamily, setSelectedFamily] = useState<(typeof KPI_FAMILY_CATALOG)[number] | null>(null);
  const [familyFilter, setFamilyFilter] = useState<FamilyFilter>('all');
  const [exportOpen, setExportOpen] = useState(false);
  const analysisRef = useRef<HTMLDivElement>(null);

  const apiSubtheme = subtheme === 'todos' ? null : subtheme;
  const subthemeLabels: Record<string, string> = {
    todos: 'todas as áreas',
    medicamento_alto_custo: 'medicamento de alto custo',
    home_care: 'home care',
    cirurgia: 'cirurgia',
    exame: 'exame',
  };
  const kpiQuery = useQuery({
    queryKey: ['klm', 'kpis', Number(period), apiSubtheme],
    queryFn: () => klmApi.kpis(Number(period), apiSubtheme),
  });
  const dashboardQuery = useQuery({ queryKey: ['klm', 'dashboard'], queryFn: klmApi.dashboard });
  const financeQuery = useQuery({ queryKey: ['klm', 'finance'], queryFn: klmApi.financeOverview });
  const qualityQuery = useQuery({ queryKey: ['klm', 'quality'], queryFn: klmApi.quality });
  const trackingQuery = useQuery({ queryKey: ['klm', 'tracking', Number(period)], queryFn: () => klmApi.tracking(Number(period)) });

  const filteredTitle = subthemeLabels[subtheme] ?? subtheme.replaceAll('_', ' ');
  const kpiByKey = useMemo(
    () => Object.fromEntries((kpiQuery.data?.topMetrics ?? []).map((item) => [item.key, item])),
    [kpiQuery.data?.topMetrics],
  );
  const familyByKey = useMemo(
    () => Object.fromEntries((kpiQuery.data?.families ?? []).map((item) => [item.key, item])),
    [kpiQuery.data?.families],
  );

  const personalizedFields = useMemo<PersonalizedFilterField<Metric>[]>(() => [
    { key: 'key', label: 'Chave do indicador', type: 'text' },
    { key: 'label', label: 'Indicador', type: 'text' },
    { key: 'value', label: 'Valor', type: 'number' },
    { key: 'unit', label: 'Unidade', type: 'select', options: [...new Set((kpiQuery.data?.topMetrics ?? []).map((metric) => metric.unit))].map((value) => ({ value, label: value })) },
  ], [kpiQuery.data?.topMetrics]);
  const scopedMetrics = useMemo<Metric[]>(
    () => (kpiQuery.data?.topMetrics ?? []).filter((metric) => (
      matchesPersonalizedFilter(metric, personalizedFilter, personalizedFields)
      && (familyFilter === 'all' || belongsToFamily(metric, familyFilter))
    )),
    [familyFilter, kpiQuery.data?.topMetrics, personalizedFields, personalizedFilter],
  );
  const metricQueryPending = kpiQuery.isPending;
  const metricQueryError = kpiQuery.isError;

  const acquisition = useMemo<MetricRow[]>(() => {
    const rows = trackingQuery.data?.byChannel ?? [];
    const maxEvents = Math.max(1, ...rows.map((channel) => channel.events));
    return rows.map((channel) => [
      channel.channel.replaceAll('_', ' '),
      formatBRL(channel.adSpend),
      Math.round(channel.events / maxEvents * 100),
      `${channel.leads} leads / ${channel.clients} clientes / ROAS ${channel.roas == null ? '—' : `${formatDecimal(channel.roas)}x`}`,
    ] as const);
  }, [trackingQuery.data?.byChannel]);

  const stageRows = useMemo<MetricRow[]>(() => {
    const leadCount = kpiByKey.new_leads?.value;
    return ['new_leads', 'qualified', 'lead_to_meeting', 'contracts', 'paid']
      .map((key) => kpiByKey[key])
      .filter((item): item is Metric => Boolean(item))
      .map((item) => {
        const progress = item.value == null
          ? null
          : item.unit === 'percent'
            ? item.value
            : item.key === 'new_leads'
              ? item.value > 0 ? 100 : 0
              : leadCount != null && leadCount > 0 ? item.value / leadCount * 100 : null;
        return [item.label, formatValue(item.value, item.unit), progress, filteredTitle] as const;
      });
  }, [filteredTitle, kpiByKey]);

  const dashboardMetric = (key: string) => {
    const value = dashboardQuery.data?.metrics[key];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  };
  const activeThreads = dashboardMetric('activeThreads');
  const dueFollowUps = dashboardMetric('dueFollowUps');
  const pendingApprovals = dashboardMetric('pendingApprovals');
  const externalActions = dashboardMetric('externalActions');
  const serviceRows: MetricRow[] = dashboardQuery.data ? [
    ['Conversas ativas', activeThreads == null ? '—' : String(activeThreads), null, activeThreads == null ? 'não agregado' : 'threads abertas'],
    ['Follow-ups vencidos', dueFollowUps == null ? '—' : String(dueFollowUps), null, dueFollowUps == null ? 'não agregado' : 'exigem ação'],
    ['Aprovações pendentes', pendingApprovals == null ? '—' : String(pendingApprovals), null, pendingApprovals == null ? 'não agregado' : 'revisão humana'],
    ['Ações externas', externalActions == null ? '—' : String(externalActions), null, externalActions == null ? 'não agregado' : 'registradas no runtime'],
  ] : [];

  const documentCoverage = familyByKey.documents?.coverage;
  const docsComplete = kpiByKey.docs_complete;
  const documentRows: MetricRow[] = kpiQuery.data ? [
    ['Casos com docs suficientes', documentCoverage == null ? '—' : String(documentCoverage), null, documentCoverage == null ? 'não agregado' : 'cobertura persistida'],
    ['Docs completos', docsComplete ? formatValue(docsComplete.value, docsComplete.unit) : '—', docsComplete?.unit === 'percent' && docsComplete.value != null ? docsComplete.value : null, docsComplete ? 'percentual dos casos' : 'não agregado'],
  ] : [];

  const maxFinanceAmount = Math.max(1, financeQuery.data?.paidAmount ?? 0, financeQuery.data?.totalOpenReceivables ?? 0, financeQuery.data?.overdueAmount ?? 0, financeQuery.data?.scheduledAmount ?? 0);
  const financeRows: MetricRow[] = financeQuery.data ? [
    ['Pipeline projetado', kpiByKey.pipeline ? formatValue(kpiByKey.pipeline.value, kpiByKey.pipeline.unit) : '—', null, kpiByKey.pipeline ? 'projeções locais' : 'não agregado'],
    ['Recebido', formatBRL(financeQuery.data.paidAmount), financeQuery.data.paidAmount / maxFinanceAmount * 100, 'caixa'],
    ['A receber', formatBRL(financeQuery.data.totalOpenReceivables), financeQuery.data.totalOpenReceivables / maxFinanceAmount * 100, 'em aberto'],
    ['Em atraso', formatBRL(financeQuery.data.overdueAmount), financeQuery.data.overdueAmount / maxFinanceAmount * 100, 'cobrança'],
    ['Agendado', formatBRL(financeQuery.data.scheduledAmount), financeQuery.data.scheduledAmount / maxFinanceAmount * 100, 'recebíveis'],
  ] : [];
  const qualityRows: MetricRow[] = (qualityQuery.data?.dimensions ?? []).map((item) => [item.label, `${formatDecimal(item.score)}/100`, item.score, item.key] as const);

  const familyChartData = useMemo<ChartDatum[]>(() => {
    if (familyFilter === 'all') return [];
    if (familyFilter === 'acquisition' || familyFilter === 'marketing') {
      return (trackingQuery.data?.byChannel ?? []).map((channel) => ({
        name: channel.channel.replaceAll('_', ' '),
        value: familyFilter === 'marketing' ? channel.attributableRevenue : channel.leads,
        unit: familyFilter === 'marketing' ? 'BRL' : 'count',
      })).filter((item) => Number.isFinite(item.value));
    }
    if (familyFilter === 'service') {
      return [
        { name: 'Conversas ativas', value: activeThreads, unit: 'count' },
        { name: 'Follow-ups vencidos', value: dueFollowUps, unit: 'count' },
        { name: 'Aprovações pendentes', value: pendingApprovals, unit: 'count' },
        { name: 'Ações externas', value: externalActions, unit: 'count' },
      ].filter((item): item is ChartDatum => item.value != null);
    }
    if (familyFilter === 'finance' || familyFilter === 'fees') {
      if (!financeQuery.data) return [];
      return [
        { name: 'Recebido', value: financeQuery.data.paidAmount, unit: 'BRL' },
        { name: 'A receber', value: financeQuery.data.totalOpenReceivables, unit: 'BRL' },
        { name: 'Em atraso', value: financeQuery.data.overdueAmount, unit: 'BRL' },
        { name: 'Agendado', value: financeQuery.data.scheduledAmount, unit: 'BRL' },
      ];
    }
    if (familyFilter === 'quality') {
      return (qualityQuery.data?.dimensions ?? []).map((item) => ({ name: item.label, value: item.score, unit: 'score' }));
    }
    return (kpiQuery.data?.topMetrics ?? [])
      .filter((metric) => belongsToFamily(metric, familyFilter) && metric.value != null && Number.isFinite(metric.value))
      .map((metric) => ({ name: metric.label, value: metric.value as number, unit: metric.unit }));
  }, [activeThreads, dueFollowUps, externalActions, familyFilter, financeQuery.data, kpiQuery.data?.topMetrics, pendingApprovals, qualityQuery.data?.dimensions, trackingQuery.data?.byChannel]);

  const activeFamily = familyFilter === 'all' ? null : KPI_FAMILY_CATALOG.find((family) => family.key === familyFilter) ?? null;
  const displayedMetrics: Metric[] = scopedMetrics.length > 0
    ? scopedMetrics
    : familyChartData.slice(0, 8).map((item, index) => ({ key: `${familyFilter}-${index}`, label: item.name, value: item.value, unit: item.unit }));
  const familyChartHasSingleUnit = new Set(familyChartData.map((item) => item.unit)).size <= 1;
  const selectFamily = (family: FamilyFilter) => {
    setFamilyFilter(family);
    window.requestAnimationFrame(() => analysisRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }));
  };

  const trendSeries = useMemo(() => {
    const leadsByDate = new Map((kpiQuery.data?.trend?.leads ?? []).map((point) => [point.date, point.value]));
    const revenueByDate = new Map((kpiQuery.data?.trend?.revenue ?? []).map((point) => [point.date, point.value]));
    const dates = [...new Set([...leadsByDate.keys(), ...revenueByDate.keys()])].sort();
    return dates.map((date) => ({
      date: new Date(date).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' }),
      leads: leadsByDate.get(date) ?? 0,
      receita: revenueByDate.get(date) ?? 0,
    }));
  }, [kpiQuery.data?.trend]);
  const teamPerformance = kpiQuery.data?.teamPerformance;
  const highestTeamScore = Math.max(
    1,
    ...(teamPerformance?.rows ?? []).map((row) => row.score),
  );

  const requiredQueries = [kpiQuery, dashboardQuery, financeQuery, qualityQuery, trackingQuery];
  const exportReady = requiredQueries.every((query) => query.isSuccess);
  const hasQueryError = requiredQueries.some((query) => query.isError);
  const generatedAt = kpiQuery.data?.generatedAt ? new Date(kpiQuery.data.generatedAt).toLocaleString('pt-BR') : null;
  const selectedCoverage = selectedFamily ? familyByKey[selectedFamily.key]?.coverage : undefined;

  const exportReport = (format: ExportFormat) => {
    if (!exportReady) {
      setNotice(hasQueryError ? 'A exportação foi bloqueada porque uma consulta local está indisponível.' : 'Aguarde todas as consultas locais terminarem antes de exportar.');
      return;
    }
    const snapshot = {
      generatedAt: new Date().toISOString(),
      source: 'local_api_snapshot',
      scope: {
        kpiPeriodDays: Number(period),
        kpiSubtheme: subtheme,
        kpiSubthemeLabel: filteredTitle,
        scopedSections: ['kpis', 'funnel', 'documents', 'trend'],
        globalCurrentSnapshots: ['tracking', 'service', 'finance', 'quality'],
      },
      kpis: { ...kpiQuery.data, displayedMetrics: scopedMetrics },
      tracking: trackingQuery.data,
      funnelIndicators: stageRows,
      dashboardCurrentSnapshot: dashboardQuery.data,
      financeCurrentSnapshot: financeQuery.data,
      qualityCurrentSnapshot: qualityQuery.data,
    };
    try {
      if (format === 'json') {
        downloadBrowserFile(`klm-kpis-${period}d-${subtheme.replaceAll(' ', '-')}.json`, JSON.stringify(snapshot, null, 2), 'application/json;charset=utf-8');
      } else {
        const rows: unknown[][] = [['seção', 'chave', 'rótulo', 'valor', 'unidade', 'recorte']];
        scopedMetrics.forEach((item) => rows.push(['kpi', item.key, item.label, item.value, item.unit, filteredTitle]));
        trackingQuery.data.byChannel.forEach((channel) => {
          Object.entries(channel).filter(([, value]) => typeof value === 'number').forEach(([key, value]) => rows.push(['aquisição', channel.channel, key, value, key === 'adSpend' || key === 'attributableRevenue' || key === 'cac' ? 'BRL' : 'count_or_ratio', `conta inteira · ${period} dias`]));
        });
        stageRows.forEach(([label, value, , scope]) => rows.push(['funil', normalize(label).replaceAll(' ', '_'), label, value, 'count', scope]));
        serviceRows.forEach(([label, value, , scope]) => rows.push(['atendimento', normalize(label).replaceAll(' ', '_'), label, value, 'count', `conta inteira · ${scope}`]));
        financeRows.forEach(([label, value, , scope]) => rows.push(['financeiro', normalize(label).replaceAll(' ', '_'), label, value, 'display_value', `conta inteira · ${scope}`]));
        qualityRows.forEach(([label, value, , key]) => rows.push(['qualidade', key, label, value, 'score', 'conta inteira · snapshot atual']));
        downloadBrowserFile(`klm-kpis-${period}d-${subtheme.replaceAll(' ', '-')}.csv`, `\uFEFF${rows.map((row) => row.map(csvCell).join(',')).join('\n')}`, 'text/csv;charset=utf-8');
      }
      setNotice(`Arquivo ${format.toUpperCase()} gerado com os dados atuais.`);
      setExportOpen(false);
    } catch {
      setNotice('O navegador não conseguiu iniciar o download local.');
    }
  };

  const refreshAll = async () => {
    setNotice(null);
    const results = await Promise.all(requiredQueries.map((query) => query.refetch()));
    setNotice(results.some((result) => result.isError) ? 'Alguns dados ainda estão indisponíveis.' : 'Indicadores atualizados.');
  };

  return (
    <div className="mx-auto max-w-[1220px] space-y-5">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Meu Executivo / Performance do escritório</p>
          <h2 className="mt-2 text-2xl font-semibold">KPIs completos</h2>
          <p className="mt-1 max-w-3xl text-xs text-muted-foreground">Da aquisição ao caixa recebido: canais, funil, atendimento, documentos, honorários, agentes, automações e confiabilidade.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Select value={period} onValueChange={setPeriod}><SelectTrigger aria-label="Período dos indicadores" className="h-9 w-36 text-2xs"><SelectValue /></SelectTrigger><SelectContent>{!['7', '30', '90'].includes(period) && <SelectItem value={period}>{period} dias</SelectItem>}<SelectItem value="7">Últimos 7 dias</SelectItem><SelectItem value="30">Últimos 30 dias</SelectItem><SelectItem value="90">Últimos 90 dias</SelectItem></SelectContent></Select>
          <CustomPeriodButton value={Number(period)} onChange={(days) => setPeriod(String(days))} presets={[7, 30, 90]} />
          <Select value={subtheme} onValueChange={setSubtheme}><SelectTrigger aria-label="Subtema dos indicadores" className="h-9 w-48 text-2xs"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="todos">Todas as áreas</SelectItem><SelectItem value="medicamento_alto_custo">Medicamento alto custo</SelectItem><SelectItem value="home_care">Home care</SelectItem><SelectItem value="cirurgia">Cirurgia</SelectItem><SelectItem value="exame">Exame</SelectItem></SelectContent></Select>
          <Select value={familyFilter} onValueChange={(value) => selectFamily(value as FamilyFilter)}><SelectTrigger aria-label="Grupo de KPIs" className="h-9 w-44 text-2xs"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Todos os grupos</SelectItem>{KPI_FAMILY_CATALOG.map((family) => <SelectItem key={family.key} value={family.key}>{family.title}</SelectItem>)}</SelectContent></Select>
          <PersonalizedFilterBuilder scope="kpis" fields={personalizedFields} value={personalizedFilter} onChange={setPersonalizedFilter} className="h-9" />
          <Button className="h-9 bg-[#031121] text-2xs text-white hover:bg-[#031121]" onClick={() => setExportOpen(true)}><Download className="mr-1.5 h-3.5 w-3.5" />Exportar local</Button>
        </div>
      </div>

      {notice && <div className="flex items-center justify-between rounded-lg border border-primary/35 bg-primary/10 px-3 py-2 text-2xs"><span>{notice}</span><Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setNotice(null)}>Fechar</Button></div>}

      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="flex-wrap">
          <TabsTrigger value="visao-geral">Visão geral</TabsTrigger>
          <TabsTrigger value="operacao">Operação jurídica</TabsTrigger>
          <TabsTrigger value="juridico">Jurídico</TabsTrigger>
          <TabsTrigger value="ia">IA</TabsTrigger>
          <TabsTrigger value="capacidade">Capacidade</TabsTrigger>
        </TabsList>

        <TabsContent value="operacao"><OperationBiTab /></TabsContent>
        <TabsContent value="juridico"><LegalBiTab /></TabsContent>
        <TabsContent value="ia"><AiBiTab /></TabsContent>
        <TabsContent value="capacidade"><CapacityTab /></TabsContent>

        <TabsContent value="visao-geral" className="space-y-5">

      <div ref={analysisRef} className="scroll-mt-20 space-y-5" data-testid="kpi-family-analysis">
        {activeFamily && <div className="flex items-center justify-between border-b border-border pb-2"><div><p className="text-xs font-semibold">{activeFamily.title}</p><p className="mt-0.5 text-2xs text-muted-foreground">{activeFamily.description}</p></div><Button variant="ghost" size="sm" className="h-8 text-2xs" onClick={() => setSelectedFamily(activeFamily)}>Ver definição</Button></div>}
        {metricQueryPending && <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{Array.from({ length: 4 }).map((_, index) => <KlmMetricCard key={index} label="" value="" delta="" loading />)}</div>}
        {metricQueryError && <KlmPanel className="border-[#b94a48]/30 p-6 text-center text-2xs text-[#a53e3c]">Os indicadores deste recorte estão indisponíveis. Nenhum valor substituto foi exibido.</KlmPanel>}
        {!metricQueryPending && !metricQueryError && displayedMetrics.length > 0 && <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{displayedMetrics.map((item) => <KlmMetricCard key={item.key} label={item.label} value={formatValue(item.value, item.unit)} delta={activeFamily?.title ?? (subtheme === 'todos' ? 'snapshot consolidado' : `recorte ${filteredTitle}`)} trend="neutral" />)}</div>}
        {!metricQueryPending && !metricQueryError && !displayedMetrics.length && <KlmPanel className="p-6 text-center text-2xs text-muted-foreground">A API ainda não agrega indicadores numéricos para {activeFamily?.title ?? 'este recorte'}.</KlmPanel>}
      </div>

      <KlmPanel className="p-4" data-testid="kpis-trend-chart">
        <KlmSectionTitle title={activeFamily ? `Gráfico de ${activeFamily.title}` : 'Leads e receita ao longo do tempo'} description={activeFamily ? 'Comparação calculada apenas com valores retornados pelas APIs do workspace.' : 'Série diária dos últimos 14 dias, calculada dos casos e eventos reais do escritório'} />
        {activeFamily && familyChartData.length > 0 && (
          <div className={`mt-4 grid gap-4 ${familyChartHasSingleUnit ? 'lg:grid-cols-[minmax(0,1.5fr)_minmax(260px,0.7fr)]' : ''}`}>
            <div className="h-64 min-w-0">
              <ResponsiveContainer width="100%" height="100%"><BarChart data={familyChartData} margin={{ top: 8, right: 8, bottom: 36, left: 0 }}><CartesianGrid strokeDasharray="3 3" vertical={false} className="stroke-border" /><XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-20} textAnchor="end" interval={0} height={58} /><YAxis tick={{ fontSize: 10 }} tickFormatter={(value) => familyChartData[0]?.unit === 'BRL' ? formatBRL(Number(value)) : String(value)} /><Tooltip formatter={(value: number) => formatValue(value, familyChartData[0]?.unit ?? 'count')} /><Bar dataKey="value" name="Valor" radius={[4, 4, 0, 0]}>{familyChartData.map((item, index) => <Cell key={`${item.name}-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />)}</Bar></BarChart></ResponsiveContainer>
            </div>
            {familyChartHasSingleUnit && <div className="h-64 min-w-0">
              <ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={familyChartData.filter((item) => item.value > 0)} dataKey="value" nameKey="name" innerRadius={48} outerRadius={82} paddingAngle={2}>{familyChartData.filter((item) => item.value > 0).map((item, index) => <Cell key={`${item.name}-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />)}</Pie><Tooltip formatter={(value: number) => formatValue(value, familyChartData[0]?.unit ?? 'count')} /><Legend wrapperStyle={{ fontSize: 10 }} /></PieChart></ResponsiveContainer>
            </div>}
          </div>
        )}
        {activeFamily && !familyChartData.length && <p className="mt-4 py-8 text-center text-2xs text-muted-foreground">Nenhuma série agregada está disponível para esta família no recorte atual.</p>}
        {!activeFamily && <>
        {kpiQuery.isPending && <div className="mt-4 h-56"><Skeleton className="h-full w-full" /></div>}
        {kpiQuery.isError && <p className="mt-4 py-8 text-center text-2xs text-[#a53e3c]">Não foi possível carregar a série temporal.</p>}
        {kpiQuery.isSuccess && trendSeries.length > 0 && (
          <div className="mt-4 h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trendSeries} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="kpi-trend-leads" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#0057d8" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#0057d8" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="kpi-trend-revenue" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#1a9948" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#1a9948" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} className="stroke-border" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                <YAxis yAxisId="leads" tick={{ fontSize: 10 }} width={32} />
                <YAxis yAxisId="revenue" orientation="right" tick={{ fontSize: 10 }} width={56} tickFormatter={(value) => formatBRL(Number(value))} />
                <Tooltip formatter={(value: number, name: string) => (name === 'receita' ? formatBRL(value) : value)} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Area yAxisId="leads" type="monotone" dataKey="leads" name="Leads" stroke="#0057d8" strokeWidth={1.5} fill="url(#kpi-trend-leads)" />
                <Area yAxisId="revenue" type="monotone" dataKey="receita" name="receita" stroke="#1a9948" strokeWidth={1.5} fill="url(#kpi-trend-revenue)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
        {kpiQuery.isSuccess && !trendSeries.length && <p className="mt-4 py-8 text-center text-2xs text-muted-foreground">Nenhuma série temporal disponível ainda.</p>}
        </>}
      </KlmPanel>

      <AdvancedReportsPanel periodDays={Number(period)} />

      <NativeModuleBar domain="crm" surface="KPIs e métricas" capabilityIds={['opportunity-aging-goals', 'case-analytics-bulk-import', 'invoice-reports', 'business-hours-sla-csat']} returnTo="/app/kpis" />
      <NativeCRMOperationsPanel view="funnel" />

      <div className="grid gap-4 xl:grid-cols-[1.15fr_0.8fr_0.75fr]">
        <KlmPanel className="p-4">
          <KlmSectionTitle title="Aquisição e tracking" description={`Visão global da conta · canal, gasto, qualidade e saúde dos eventos · ${period} dias`} action={trackingQuery.data ? <KlmPill tone="green">{trackingQuery.data.eventCoverage.filter((item) => item.covered).length}/{trackingQuery.data.eventCoverage.length} eventos</KlmPill> : undefined} />
          <MetricRows rows={acquisition} />
          <QueryState pending={trackingQuery.isPending} error={trackingQuery.isError} empty={trackingQuery.isSuccess && !acquisition.length} label="aquisição" />
          <p className="mt-4 text-2xs leading-3 text-muted-foreground">O período selecionado é aplicado pela API de tracking. Valores sem eventos permanecem vazios.</p>
        </KlmPanel>
        <KlmPanel className="p-4">
          <KlmSectionTitle title="Funil comercial" description={`Etapas e conversão · ${filteredTitle}`} />
          <MetricRows rows={stageRows} />
          <QueryState pending={kpiQuery.isPending} error={kpiQuery.isError} empty={kpiQuery.isSuccess && !stageRows.length} label="funil" />
        </KlmPanel>
        <KlmPanel className="p-4">
          <KlmSectionTitle title="Atendimento" description="Visão global da conta · filas e decisões humanas" />
          <MetricRows rows={serviceRows} />
          <QueryState pending={dashboardQuery.isPending} error={dashboardQuery.isError} empty={dashboardQuery.isSuccess && !serviceRows.length} label="atendimento" />
        </KlmPanel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <KlmPanel className="p-4"><KlmSectionTitle title="Documentos e RAG" description="Suficiência calculada sobre casos persistidos" /><MetricRows rows={documentRows} /><QueryState pending={kpiQuery.isPending} error={kpiQuery.isError} empty={kpiQuery.isSuccess && !documentRows.length} label="documentos" /></KlmPanel>
        <KlmPanel className="p-4"><KlmSectionTitle title="Honorários e economia" description="Visão global da conta · projeções e lançamentos financeiros locais" /><MetricRows rows={financeRows} /><QueryState pending={financeQuery.isPending || kpiQuery.isPending} error={financeQuery.isError || kpiQuery.isError} empty={financeQuery.isSuccess && kpiQuery.isSuccess && !financeRows.length} label="financeiro" /></KlmPanel>
        <KlmPanel tone="soft" className="p-4"><KlmSectionTitle title="Agentes e qualidade" description="Visão global da conta · dimensões calculadas pela API de qualidade" /><MetricRows rows={qualityRows} /><QueryState pending={qualityQuery.isPending} error={qualityQuery.isError} empty={qualityQuery.isSuccess && !qualityRows.length} label="qualidade" /></KlmPanel>
      </div>

      <KlmPanel className="p-4" data-testid="workspace-team-performance">
        <KlmSectionTitle
          title="Desempenho e gamificação da equipe"
          description={`${teamPerformance?.scoreFormula ?? 'Classificação calculada somente com dados da conta atual.'}${subtheme === 'todos' ? '' : ' · equipe da conta inteira'}`}
          action={<KlmPill tone="blue"><Trophy className="mr-1 h-3 w-3" />{period} dias</KlmPill>}
        />
        {kpiQuery.isPending && <div className="mt-4 grid gap-2 lg:grid-cols-2">{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-16 w-full" />)}</div>}
        {kpiQuery.isError && <p className="mt-4 py-8 text-center text-2xs text-[#a53e3c]">Não foi possível calcular a classificação da equipe.</p>}
        {kpiQuery.isSuccess && Boolean(teamPerformance?.rows.length) && (
          <div className="mt-4 grid gap-2 lg:grid-cols-2">
            {teamPerformance?.rows.map((row, index) => (
              <div key={row.id} className="grid min-w-0 grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-3 rounded-lg border bg-card px-3 py-2.5">
                <span className="grid h-7 w-7 place-items-center rounded-md bg-secondary text-2xs font-semibold">{index + 1}</span>
                <span className="min-w-0">
                  <strong className="block truncate text-xs">{row.name}</strong>
                  <small className="mt-0.5 block truncate text-2xs text-muted-foreground">
                    {row.completedCount} concluídos · {row.pendingCount} pendentes · {row.primaryCount} casos
                  </small>
                  <span className="mt-2 block"><KlmProgress value={row.score / highestTeamScore * 100} tone={index === 0 ? 'green' : 'gold'} /></span>
                </span>
                <span className="text-right">
                  <strong className="block text-xs">{formatDecimal(row.score)} pts</strong>
                  <small className="block text-2xs text-muted-foreground">{row.qualityValue == null ? 'sem nota' : `qualidade ${formatDecimal(row.qualityValue)}`}</small>
                </span>
              </div>
            ))}
          </div>
        )}
        {kpiQuery.isSuccess && !teamPerformance?.rows.length && <p className="mt-4 py-8 text-center text-2xs text-muted-foreground">A classificação aparecerá quando houver usuários ativos na equipe.</p>}
      </KlmPanel>

      <KlmPanel className="p-4">
        <KlmSectionTitle title="Matriz completa de KPIs" description="Selecione um grupo para levar seus indicadores e gráficos ao topo da página" action={<Button variant="outline" size="sm" className="h-8 text-2xs" onClick={() => setSelectedFamily(activeFamily ?? KPI_FAMILY_CATALOG[0])}><Filter className="mr-1.5 h-3.5 w-3.5" />Definições</Button>} />
        <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {KPI_FAMILY_CATALOG.map((family, index) => {
            const coverage = familyByKey[family.key]?.coverage;
            return <button key={family.key} type="button" onClick={() => selectFamily(family.key)} aria-pressed={familyFilter === family.key} className="min-h-[92px] rounded-lg border border-border p-3 text-left transition-colors hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary aria-pressed:border-primary aria-pressed:bg-primary/10"><div className="flex items-center gap-2"><span className={`h-2 w-2 rounded-full ${coverage == null ? 'bg-muted-foreground/40' : coverage > 0 ? 'bg-[#1a9948]' : index % 3 === 1 ? 'bg-primary' : 'bg-[#b94a48]'}`} /><span className="text-2xs font-semibold">{family.title}</span></div><p className="mt-2 text-2xs leading-3 text-muted-foreground">{family.description}</p><span className="mt-2 block text-2xs font-semibold text-[#0057d8]">{coverage == null ? 'Sem agregado neste endpoint' : `${coverage} registro${coverage === 1 ? '' : 's'} de cobertura`}</span></button>;
          })}
        </div>
      </KlmPanel>

      <div className="flex flex-col gap-2 border-t border-border pt-4 text-2xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between"><span>Fonte: {kpiQuery.isPending ? 'carregando' : kpiQuery.isError ? 'indisponível' : `${kpiQuery.data?.source ?? 'fonte não informada'} · ${kpiQuery.data?.periodDays == null ? 'período não informado' : `${kpiQuery.data.periodDays} dias`} · ${kpiQuery.data?.subtheme ? subthemeLabels[kpiQuery.data.subtheme] ?? kpiQuery.data.subtheme : 'todos os subtemas'}${generatedAt ? ` · gerado em ${generatedAt}` : ''}`}. Atendimento, qualidade, aquisição e caixa detalhado são snapshots globais da conta identificados.</span><Button variant="ghost" size="sm" className="h-8 text-2xs" disabled={requiredQueries.some((query) => query.isFetching)} onClick={() => void refreshAll()}><RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${requiredQueries.some((query) => query.isFetching) ? 'animate-spin' : ''}`} />Atualizar</Button></div>

        </TabsContent>
      </Tabs>

      <Dialog open={Boolean(selectedFamily)} onOpenChange={(open) => { if (!open) setSelectedFamily(null); }}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle>{selectedFamily?.title}</DialogTitle><DialogDescription>Definição operacional da família de indicadores no catálogo semântico local.</DialogDescription></DialogHeader>
          <p className="text-xs leading-5 text-muted-foreground">{selectedFamily?.description}</p>
          <div className="grid gap-2 border-l-2 border-primary bg-secondary p-3 text-2xs sm:grid-cols-3"><span><strong className="block">Fonte</strong>{selectedCoverage == null ? 'catálogo da interface' : 'sistema'}</span><span><strong className="block">Cobertura</strong>{selectedCoverage == null ? 'não agregada' : `${selectedCoverage} registros`}</span><span><strong className="block">Recorte</strong>{period} dias · {filteredTitle}</span></div>
        </DialogContent>
      </Dialog>

      <Dialog open={exportOpen} onOpenChange={setExportOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>Exportar indicadores locais</DialogTitle><DialogDescription>CSV para planilha ou JSON para preservar a estrutura completa das respostas locais.</DialogDescription></DialogHeader>
          <div className="rounded-lg border bg-secondary p-4 text-2xs"><div className="flex items-center justify-between"><span>Consultas</span><KlmPill tone={exportReady ? 'green' : hasQueryError ? 'red' : 'gold'}>{exportReady ? '5 fontes prontas' : hasQueryError ? 'Fonte indisponível' : 'Carregando'}</KlmPill></div><div className="mt-3 flex items-center gap-2 text-muted-foreground"><ShieldCheck className="h-4 w-4 text-[#0057d8]" />Nenhum envio externo é realizado.</div></div>
          {!exportReady && <p className="flex items-center gap-2 text-2xs text-muted-foreground"><BarChart3 className="h-4 w-4" />{hasQueryError ? 'Atualize os dados e corrija as consultas indisponíveis.' : 'Aguarde o carregamento para evitar um arquivo parcial.'}</p>}
          <DialogFooter><Button variant="outline" onClick={() => setExportOpen(false)}>Cancelar</Button><Button variant="outline" disabled={!exportReady} onClick={() => exportReport('json')}><Download className="mr-2 h-4 w-4" />Baixar JSON</Button><Button className="bg-[#031121] text-white hover:bg-[#031121]" disabled={!exportReady} onClick={() => exportReport('csv')}><Download className="mr-2 h-4 w-4" />Baixar CSV</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
