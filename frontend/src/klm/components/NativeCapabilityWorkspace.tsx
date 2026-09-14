import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import {
  Activity,
  AlertCircle,
  ArrowDownRight,
  ArrowLeft,
  ArrowUpRight,
  BarChart3,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleGauge,
  CloudOff,
  Database,
  ExternalLink,
  FileJson,
  History,
  Info,
  List,
  Loader2,
  LockKeyhole,
  Minus,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Table2,
  X,
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
import { Textarea } from '@/components/ui/textarea';
import { KlmPill } from '@/klm/components/Primitives';
import { nativeDisplayText } from '@/klm/native-display';
import type { KlmNativeDomain } from '@/klm/native-domains';
import { cn } from '@/lib/utils';
import {
  applyNativeOptimisticPatch,
  createNativeIdempotencyKey,
  humanizeNativeKey,
  nativeCapabilityTransport,
  normalizeNativeCapabilityDocument,
  NativeCapabilityError,
  type NativeActionField,
  type NativeActionMode,
  type NativeActionSchema,
  type NativeCapabilityActionResult,
  type NativeCapabilityColumn,
  type NativeCapabilityDocument,
  type NativeCapabilitySection,
  type NativeCapabilityTransport,
  type NativeChartSection,
  type NativeDetailsSection,
  type NativeListSection,
  type NativeNoticeSection,
  type NativeRecord,
  type NativeStructuredSection,
  type NativeTableSection,
  type NativeTimelineSection,
  type NativeValue,
  type NativeValueFormat,
} from '@/klm/native-capabilities';

type WorkspacePhase = 'loading' | 'ready' | 'refreshing' | 'error' | 'offline';
type FormValues = Record<string, NativeValue>;

type DetailSelection = {
  title: string;
  data: NativeRecord;
  actionIds: string[];
  fields?: NativeCapabilityColumn[];
} | null;

export interface NativeCapabilityWorkspaceProps {
  domain: KlmNativeDomain;
  capability: string;
  transport?: NativeCapabilityTransport;
  initialDocument?: NativeCapabilityDocument | unknown;
  online?: boolean;
  onBack?: () => void;
  onOpenInventory?: () => void;
  className?: string;
}

const dateTimeFormatter = new Intl.DateTimeFormat('pt-BR', {
  dateStyle: 'short',
  timeStyle: 'short',
});
const dateFormatter = new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short' });
const numberFormatter = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 });
const integerFormatter = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });
const currencyFormatter = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
const DISPLAY_VALUE_LABELS: Record<string, string> = {
  active: 'Ativo',
  admin: 'Administrador',
  blocked: 'Bloqueado',
  calendar: 'Calendário',
  'channel::api': 'Canal de API',
  closed: 'Fechado',
  computed: 'Calculado localmente',
  configuration_only: 'Somente configuração',
  disabled: 'Desativado',
  email: 'E-mail',
  enabled: 'Ativo',
  error: 'Erro',
  'external gate': 'Controle de execução externa',
  external_gate: 'Controle de execução externa',
  failed: 'Falhou',
  healthy: 'Saudável',
  notifications: 'Notificações',
  offline: 'Offline',
  online: 'Online',
  open: 'Aberto',
  owner: 'Proprietário',
  payments: 'Pagamentos',
  pending: 'Pendente',
  ready: 'Pronto',
  resolved: 'Resolvido',
  routingcontext: 'Contexto de roteamento',
  state: 'Estado local',
  success: 'Concluído',
  synced: 'Sincronizado',
  user: 'Usuário',
};

function formatDate(value: NativeValue, includeTime: boolean): string {
  const parsed = new Date(String(value));
  if (Number.isNaN(parsed.getTime())) return String(value ?? '—');
  return includeTime ? dateTimeFormatter.format(parsed) : dateFormatter.format(parsed);
}

function formatValue(value: NativeValue | undefined, format?: NativeValueFormat): string {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) return value.map((item) => formatValue(item)).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  if (format === 'boolean' || typeof value === 'boolean') return value ? 'Sim' : 'Não';
  if (format === 'currency') return currencyFormatter.format(Number(value) || 0);
  if (format === 'percent') return `${numberFormatter.format(Number(value) || 0)}%`;
  if (format === 'integer') return integerFormatter.format(Number(value) || 0);
  if (format === 'number') return numberFormatter.format(Number(value) || 0);
  if (format === 'date') return formatDate(value, false);
  if (format === 'datetime') return formatDate(value, true);
  if (typeof value === 'string') {
    const translated = DISPLAY_VALUE_LABELS[value.trim().toLowerCase()];
    if (translated) return translated;
  }
  return String(value);
}

function formatTimestamp(value?: string | null): string {
  if (!value) return 'sem registro';
  return formatDate(value, true);
}

function healthTone(state: NativeCapabilityDocument['health']['state']): 'green' | 'gold' | 'red' | 'muted' {
  if (state === 'healthy') return 'green';
  if (state === 'degraded' || state === 'syncing' || state === 'gated') return 'gold';
  if (state === 'offline') return 'red';
  return 'muted';
}

function healthLabel(document: NativeCapabilityDocument): string {
  if (document.health.label) return document.health.label;
  return {
    healthy: 'Saudável',
    degraded: 'Degradada',
    syncing: 'Sincronizando',
    offline: 'Offline',
    gated: 'Sob gate',
    unknown: 'Não verificada',
  }[document.health.state];
}

function statusTone(value: NativeValue | undefined): 'green' | 'gold' | 'red' | 'blue' | 'muted' {
  const normalized = String(value ?? '').toLowerCase();
  if (['active', 'ready', 'healthy', 'synced', 'success', 'open', 'resolved', 'paid', 'approved', 'online'].some((item) => normalized.includes(item))) return 'green';
  if (['error', 'failed', 'offline', 'rejected', 'overdue', 'blocked'].some((item) => normalized.includes(item))) return 'red';
  if (['pending', 'warning', 'stale', 'waiting', 'draft', 'gated'].some((item) => normalized.includes(item))) return 'gold';
  if (['running', 'processing', 'progress'].some((item) => normalized.includes(item))) return 'blue';
  return 'muted';
}

function ValueCell({ value, format }: { value: NativeValue | undefined; format?: NativeValueFormat }) {
  if (format === 'status') return <KlmPill tone={statusTone(value)}>{formatValue(value)}</KlmPill>;
  if (format === 'link' && typeof value === 'string' && /^(https?:\/\/|\/)/.test(value)) {
    return (
      <a href={value} target="_blank" rel="noreferrer" className="inline-flex max-w-full items-center gap-1 truncate text-[#72500d] underline decoration-primary/50 underline-offset-2 dark:text-[#f1c46f]">
        <span className="truncate">{value}</span><ExternalLink className="h-3 w-3 shrink-0" />
      </a>
    );
  }
  if (typeof value === 'object' && value !== null) {
    const count = Array.isArray(value) ? value.length : Object.keys(value).length;
    return <span className="text-xs text-muted-foreground">{count} {count === 1 ? 'item' : 'itens'}</span>;
  }
  return <span>{formatValue(value, format)}</span>;
}

function StructuredValue({ value, depth = 0 }: { value: NativeValue; depth?: number }) {
  if (value === null || typeof value !== 'object') return <span>{formatValue(value)}</span>;
  if (depth >= 5) {
    return (
      <details className="min-w-0">
        <summary className="cursor-pointer text-xs font-medium text-[#72500d] dark:text-[#f1c46f]">Ver dados técnicos</summary>
        <pre className="mt-2 max-h-56 max-w-full overflow-auto whitespace-pre-wrap break-all border border-border bg-secondary/50 p-3 font-mono text-2xs leading-4 text-muted-foreground">{JSON.stringify(value, null, 2)}</pre>
      </details>
    );
  }
  if (Array.isArray(value)) {
    if (!value.length) return <span className="text-muted-foreground">Lista vazia</span>;
    return (
      <ol className="space-y-2" aria-label="Lista de valores estruturados">
        {value.map((item, index) => (
          <li key={index} className="border-l-2 border-primary/45 pl-3">
            <StructuredValue value={item} depth={depth + 1} />
          </li>
        ))}
      </ol>
    );
  }
  return (
    <dl className="grid gap-x-5 gap-y-3 sm:grid-cols-2">
      {Object.entries(value).map(([key, item]) => (
        <div key={key} className={cn('min-w-0', typeof item === 'object' && item !== null && 'sm:col-span-2')}>
          <dt className="text-2xs font-semibold uppercase text-muted-foreground">{humanizeNativeKey(key)}</dt>
          <dd className="mt-1 break-words text-xs leading-5 text-foreground"><StructuredValue value={item} depth={depth + 1} /></dd>
        </div>
      ))}
    </dl>
  );
}

function SectionShell({
  section,
  actions,
  onAction,
  online,
  children,
}: {
  section: NativeCapabilitySection;
  actions: NativeActionSchema[];
  onAction: (action: NativeActionSchema) => void;
  online: boolean;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={`${section.id}-title`} className="border-b border-border py-5 first:pt-0 last:border-b-0">
      <div className="mb-4 flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 id={`${section.id}-title`} className="flex items-center gap-2 text-sm font-semibold text-foreground">
            {section.type === 'table' && <Table2 className="h-4 w-4 text-[#0057d8]" />}
            {section.type === 'list' && <List className="h-4 w-4 text-[#0057d8]" />}
            {section.type === 'chart' && <BarChart3 className="h-4 w-4 text-[#0057d8]" />}
            {section.type === 'timeline' && <History className="h-4 w-4 text-[#0057d8]" />}
            {section.type === 'structured' && <FileJson className="h-4 w-4 text-[#0057d8]" />}
            {section.type === 'details' && <Database className="h-4 w-4 text-[#0057d8]" />}
            {section.type === 'notice' && <Info className="h-4 w-4 text-[#0057d8]" />}
            {section.title}
          </h2>
          {section.description && <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">{section.description}</p>}
        </div>
        {actions.length > 0 && (
          <div className="flex shrink-0 flex-wrap gap-2">
            {actions.map((action) => <ActionButton key={action.id} action={action} online={online} onClick={() => onAction(action)} compact />)}
          </div>
        )}
      </div>
      {children}
    </section>
  );
}

function actionBlockedReason(action: NativeActionSchema, online: boolean): string | null {
  if (action.permission?.allowed === false) return action.permission.reason || 'Seu perfil não possui esta permissão.';
  if (!online) return 'Ação indisponível enquanto o backend local estiver offline.';
  if (action.gate?.state === 'offline') return action.gate.reason || 'O módulo necessário está offline.';
  if (action.execution === 'external' && action.gate?.state === 'gated' && !action.gate.allowDryRun && !action.gate.allowConfigure) {
    return action.gate.reason || 'A execução externa está bloqueada.';
  }
  return null;
}

function ActionButton({
  action,
  onClick,
  online = true,
  compact = false,
}: {
  action: NativeActionSchema;
  onClick: () => void;
  online?: boolean;
  compact?: boolean;
}) {
  const blockedReason = actionBlockedReason(action, online);
  const gated = action.execution === 'external' && action.gate?.state === 'gated';
  return (
    <Button
      type="button"
      size="sm"
      variant={action.intent === 'primary' ? 'default' : action.intent === 'danger' ? 'destructive' : 'outline'}
      className={cn('max-w-full', compact ? 'h-8 text-xs' : 'h-9 text-xs', action.intent === 'primary' && 'bg-[#031121] text-white hover:bg-[#202020]')}
      disabled={Boolean(blockedReason)}
      title={blockedReason ?? action.description}
      onClick={onClick}
    >
      {blockedReason ? <LockKeyhole className="h-3.5 w-3.5" /> : gated ? <ShieldAlert className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
      <span className="truncate">{gated ? `${action.label} (simulação)` : action.label}</span>
    </Button>
  );
}

function rowMatches(row: NativeRecord, query: string, keys?: string[]): boolean {
  const selected = keys?.length ? keys.map((key) => row[key]) : Object.values(row);
  return selected.some((value) => formatValue(value).toLocaleLowerCase('pt-BR').includes(query));
}

function TableSection({
  section,
  actions,
  onSelect,
}: {
  section: NativeTableSection;
  actions: NativeActionSchema[];
  onSelect: (selection: DetailSelection) => void;
}) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const normalizedQuery = query.trim().toLocaleLowerCase('pt-BR');
  const filteredRows = useMemo(
    () => normalizedQuery ? section.rows.filter((row) => rowMatches(row, normalizedQuery, section.searchKeys)) : section.rows,
    [normalizedQuery, section.rows, section.searchKeys],
  );
  const pageSize = Math.max(1, Math.min(section.pageSize ?? 12, 100));
  const pages = Math.max(1, Math.ceil(filteredRows.length / pageSize));
  const currentPage = Math.min(page, pages);
  const visibleRows = filteredRows.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const selectRow = (row: NativeRecord, index: number) => onSelect({
    title: formatValue(row[section.columns[0]?.key]) || `Registro ${(currentPage - 1) * pageSize + index + 1}`,
    data: row,
    actionIds: section.rowActionIds ?? [],
    fields: section.columns,
  });

  useEffect(() => setPage(1), [normalizedQuery]);

  return (
    <div className="min-w-0">
      {section.searchable !== false && (
        <label className="relative mb-3 block max-w-md">
          <span className="sr-only">Pesquisar em {section.title}</span>
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`Pesquisar em ${section.title.toLocaleLowerCase('pt-BR')}`}
            className="h-9 pl-9 text-xs"
          />
        </label>
      )}

      {!filteredRows.length ? (
        <div className="border border-dashed border-border px-4 py-8 text-center">
          <Search className="mx-auto h-5 w-5 text-muted-foreground" />
          <p className="mt-2 text-sm font-medium">Nenhum registro encontrado</p>
          <p className="mt-1 text-xs text-muted-foreground">Ajuste a pesquisa ou atualize a fonte de dados.</p>
        </div>
      ) : (
        <>
          <div data-testid={`desktop-table-${section.id}`} data-layout="desktop-table" className="hidden overflow-x-auto border border-border [contain:paint] sm:block">
            <table className="w-full min-w-[680px] border-collapse text-left text-xs">
              <thead className="bg-secondary/80 text-2xs uppercase text-muted-foreground">
                <tr>
                  {section.columns.map((column) => <th key={column.key} className="whitespace-nowrap border-b border-border px-3 py-2.5 font-semibold">{column.label}</th>)}
                  <th className="w-10 border-b border-border px-2 py-2.5"><span className="sr-only">Detalhes</span></th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row, index) => {
                  const rowKey = String(row[section.idField ?? 'id'] ?? `${currentPage}-${index}`);
                  return (
                    <tr key={rowKey} className="border-b border-border last:border-b-0 hover:bg-secondary/45">
                      {section.columns.map((column) => <td key={column.key} className="max-w-[320px] px-3 py-3"><ValueCell value={row[column.key]} format={column.format} /></td>)}
                      <td className="px-2 py-2 text-right">
                        <Button type="button" variant="ghost" size="icon" className="h-8 w-8" aria-label={`Ver detalhes de ${formatValue(row[section.columns[0]?.key])}`} onClick={() => selectRow(row, index)}><ChevronRight className="h-4 w-4" /></Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div data-testid={`mobile-table-${section.id}`} data-layout="mobile-stacked" className="divide-y divide-border border border-border sm:hidden">
            {visibleRows.map((row, index) => {
              const rowKey = String(row[section.idField ?? 'id'] ?? `${currentPage}-${index}`);
              return (
                <button key={rowKey} type="button" className="block w-full px-3 py-3 text-left hover:bg-secondary/45" onClick={() => selectRow(row, index)}>
                  <span className="flex items-start justify-between gap-3">
                    <strong className="min-w-0 break-words text-sm">{formatValue(row[section.columns[0]?.key])}</strong>
                    <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                  </span>
                  <span className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2">
                    {section.columns.slice(1, 5).filter((column) => !column.hiddenOnMobile).map((column) => (
                      <span key={column.key} className="min-w-0">
                        <span className="block text-2xs uppercase text-muted-foreground">{column.label}</span>
                        <span className="mt-0.5 block break-words text-xs"><ValueCell value={row[column.key]} format={column.format} /></span>
                      </span>
                    ))}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="mt-3 flex items-center justify-between gap-3 text-xs text-muted-foreground">
            <span>{filteredRows.length} registro(s)</span>
            {pages > 1 && (
              <div className="flex items-center gap-1">
                <Button type="button" size="icon" variant="outline" className="h-8 w-8" aria-label="Página anterior" disabled={currentPage === 1} onClick={() => setPage((value) => Math.max(1, value - 1))}><ChevronLeft className="h-4 w-4" /></Button>
                <span className="min-w-16 text-center">{currentPage} de {pages}</span>
                <Button type="button" size="icon" variant="outline" className="h-8 w-8" aria-label="Próxima página" disabled={currentPage === pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}><ChevronRight className="h-4 w-4" /></Button>
              </div>
            )}
          </div>
        </>
      )}
      {actions.length > 0 && <span className="sr-only">Esta tabela possui ações contextuais disponíveis nos detalhes do registro.</span>}
    </div>
  );
}

function DetailsSection({ section }: { section: NativeDetailsSection }) {
  const fields: NativeCapabilityColumn[] = section.fields?.length
    ? section.fields
    : Object.keys(section.data).map((key) => ({ key, label: humanizeNativeKey(key) }));
  return (
    <dl className="grid gap-px overflow-hidden border border-border bg-border sm:grid-cols-2 xl:grid-cols-3">
      {fields.map((field) => (
        <div key={field.key} className="min-w-0 bg-card px-4 py-3">
          <dt className="text-2xs font-semibold uppercase text-muted-foreground">{field.label}</dt>
          <dd className="mt-1 break-words text-sm"><ValueCell value={section.data[field.key]} format={field.format} /></dd>
        </div>
      ))}
    </dl>
  );
}

function ListSection({ section, onSelect }: { section: NativeListSection; onSelect: (selection: DetailSelection) => void }) {
  if (!section.items.length) return <SectionEmpty label={section.title} />;
  return (
    <div className="divide-y divide-border border-y border-border">
      {section.items.map((item, index) => {
        const title = formatValue(item[section.titleField ?? 'title']) || `Item ${index + 1}`;
        const description = item[section.descriptionField ?? 'description'];
        const status = item[section.statusField ?? 'status'];
        return (
          <button
            key={String(item[section.idField ?? 'id'] ?? index)}
            type="button"
            className="flex w-full min-w-0 items-start justify-between gap-3 px-1 py-3 text-left hover:bg-secondary/40 sm:px-3"
            onClick={() => onSelect({ title, data: item, actionIds: section.rowActionIds ?? [] })}
          >
            <span className="min-w-0">
              <strong className="block break-words text-sm font-semibold">{title}</strong>
              {description !== undefined && <span className="mt-1 block break-words text-xs leading-5 text-muted-foreground">{formatValue(description)}</span>}
            </span>
            <span className="flex shrink-0 items-center gap-2">
              {status !== undefined && <KlmPill tone={statusTone(status)}>{formatValue(status)}</KlmPill>}
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            </span>
          </button>
        );
      })}
    </div>
  );
}

function TimelineSection({ section }: { section: NativeTimelineSection }) {
  if (!section.items.length) return <SectionEmpty label={section.title} />;
  return (
    <ol className="relative ml-2 border-l border-border">
      {section.items.map((item) => (
        <li key={item.id} className="relative pb-5 pl-5 last:pb-0">
          <span className="absolute -left-[5px] top-1 h-2.5 w-2.5 rounded-full border-2 border-background bg-primary" />
          <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
            <strong className="text-sm font-semibold">{item.title}</strong>
            <time className="shrink-0 text-xs text-muted-foreground">{formatTimestamp(item.timestamp)}</time>
          </div>
          {item.description && <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">{item.description}</p>}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {item.actor && <KlmPill tone="muted">{item.actor}</KlmPill>}
            {item.status && <KlmPill tone={statusTone(item.status)}>{item.status}</KlmPill>}
          </div>
          {item.metadata && Object.keys(item.metadata).length > 0 && <div className="mt-3 border-l-2 border-border pl-3"><StructuredValue value={item.metadata} /></div>}
        </li>
      ))}
    </ol>
  );
}

function ChartSection({ section }: { section: NativeChartSection }) {
  if (!section.points.length) return <SectionEmpty label={section.title} />;
  const max = Math.max(...section.points.map((point) => Math.abs(point.value)), 1);
  return (
    <div className="space-y-3" role="img" aria-label={`Gráfico ${section.title}`}>
      {section.points.map((point) => (
        <div key={point.id} className="grid grid-cols-[minmax(76px,140px)_minmax(80px,1fr)_auto] items-center gap-3 text-xs">
          <span className="truncate font-medium" title={point.label}>{point.label}</span>
          <span className="h-2 overflow-hidden rounded-full bg-secondary">
            <span className="block h-full rounded-full bg-primary transition-[width]" style={{ width: `${Math.max(2, Math.abs(point.value) / max * 100)}%` }} />
          </span>
          <strong className="whitespace-nowrap text-right">{point.displayValue ?? formatValue(point.value, point.format)}</strong>
          {point.detail && <span className="col-span-3 text-xs text-muted-foreground">{point.detail}</span>}
        </div>
      ))}
    </div>
  );
}

function NoticeSection({ section }: { section: NativeNoticeSection }) {
  return (
    <div className={cn(
      'border-l-4 px-4 py-3 text-xs leading-5',
      section.tone === 'success' && 'border-emerald-600 bg-emerald-500/8 text-emerald-900 dark:text-emerald-200',
      section.tone === 'warning' && 'border-amber-500 bg-amber-500/10',
      section.tone === 'danger' && 'border-red-600 bg-red-500/10 text-red-800 dark:text-red-200',
      section.tone === 'info' && 'border-[#031121] bg-[#031121]/8',
      (!section.tone || section.tone === 'neutral') && 'border-border bg-secondary',
    )}>
      {section.body ?? section.description ?? section.title}
    </div>
  );
}

function StructuredSection({ section }: { section: NativeStructuredSection }) {
  return <div className="border border-border p-4 text-xs leading-5"><StructuredValue value={section.data} /></div>;
}

function SectionEmpty({ label }: { label: string }) {
  return (
    <div className="border border-dashed border-border px-4 py-7 text-center">
      <Database className="mx-auto h-5 w-5 text-muted-foreground" />
      <p className="mt-2 text-sm font-medium">Sem dados em {label.toLocaleLowerCase('pt-BR')}</p>
      <p className="mt-1 text-xs text-muted-foreground">A fonte respondeu sem registros para este contexto.</p>
    </div>
  );
}

function fieldVisible(field: NativeActionField, values: FormValues): boolean {
  if (!field.visibleWhen?.field) return true;
  const current = values[field.visibleWhen.field];
  if (field.visibleWhen.equals !== undefined && current !== field.visibleWhen.equals) return false;
  if (field.visibleWhen.notEquals !== undefined && current === field.visibleWhen.notEquals) return false;
  return true;
}

function initialFieldValue(field: NativeActionField, context?: NativeRecord): NativeValue {
  const sourced = field.sourceKey ? context?.[field.sourceKey] : undefined;
  const value = sourced !== undefined ? sourced : field.defaultValue;
  if (field.type === 'checkbox' || field.type === 'switch') return typeof value === 'boolean' ? value : false;
  if (field.type === 'multiselect') return Array.isArray(value) ? value : [];
  if (field.type === 'json' && typeof value === 'object' && value !== null) return JSON.stringify(value, null, 2);
  return value ?? '';
}

function validateActionValues(action: NativeActionSchema, values: FormValues): { values: NativeRecord; errors: Record<string, string> } {
  const normalized: NativeRecord = {};
  const errors: Record<string, string> = {};
  const oneOfGroups = new Map<string, NativeActionField[]>();
  action.fields.forEach((field) => {
    if (!fieldVisible(field, values)) return;
    if (field.oneOfGroup) {
      oneOfGroups.set(field.oneOfGroup, [...(oneOfGroups.get(field.oneOfGroup) ?? []), field]);
    }
    const value = values[field.name];
    const empty = value === '' || value === null || value === undefined || (Array.isArray(value) && value.length === 0);
    if (field.required && empty) {
      errors[field.name] = `${field.label} é obrigatório.`;
      return;
    }
    if (empty) {
      normalized[field.name] = value ?? null;
      return;
    }
    if (field.type === 'number' || field.type === 'integer') {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) errors[field.name] = `${field.label} deve ser numérico.`;
      else if (field.min !== undefined && parsed < field.min) errors[field.name] = `${field.label} deve ser no mínimo ${field.min}.`;
      else if (field.max !== undefined && parsed > field.max) errors[field.name] = `${field.label} deve ser no máximo ${field.max}.`;
      else normalized[field.name] = field.type === 'integer' ? Math.trunc(parsed) : parsed;
      return;
    }
    if (field.type === 'json') {
      try {
        normalized[field.name] = JSON.parse(String(value)) as NativeValue;
      } catch {
        errors[field.name] = `${field.label} deve conter JSON válido.`;
      }
      return;
    }
    if (typeof value === 'string') {
      if (field.minLength !== undefined && value.length < field.minLength) errors[field.name] = `${field.label} deve ter ao menos ${field.minLength} caracteres.`;
      if (field.maxLength !== undefined && value.length > field.maxLength) errors[field.name] = `${field.label} deve ter no máximo ${field.maxLength} caracteres.`;
      if (field.pattern) {
        try {
          if (!new RegExp(field.pattern).test(value)) errors[field.name] = `${field.label} possui formato inválido.`;
        } catch {
          errors[field.name] = `${field.label} possui validação inválida no schema.`;
        }
      }
    }
    normalized[field.name] = value;
  });
  oneOfGroups.forEach((fields) => {
    const hasValue = fields.some((field) => {
      const value = values[field.name];
      return value !== '' && value !== null && value !== undefined && (!Array.isArray(value) || value.length > 0);
    });
    if (!hasValue && fields[0]) {
      errors[fields[0].name] = `Preencha ${fields.map((field) => field.label).join(' ou ')}.`;
    }
  });
  return { values: normalized, errors };
}

function ActionField({
  field,
  value,
  error,
  onChange,
}: {
  field: NativeActionField;
  value: NativeValue;
  error?: string;
  onChange: (value: NativeValue) => void;
}) {
  const id = `native-action-field-${field.name}`;
  if (field.type === 'hidden') return null;
  const describedBy = [field.description ? `${id}-description` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ') || undefined;
  const common = { id, name: field.name, disabled: field.readOnly, required: field.required, 'aria-required': field.required || Boolean(field.oneOfGroup), 'aria-invalid': Boolean(error), 'aria-describedby': describedBy };
  let control: React.ReactNode;

  if (field.type === 'textarea' || field.type === 'json') {
    control = <Textarea {...common} rows={field.type === 'json' ? 7 : 4} value={String(value ?? '')} placeholder={field.placeholder} className={cn('text-sm', field.type === 'json' && 'font-mono text-xs')} onChange={(event) => onChange(event.target.value)} />;
  } else if (field.type === 'select') {
    control = (
      <select {...common} value={String(value ?? '')} onChange={(event) => onChange(event.target.value)} className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <option value="">Selecione</option>
        {(field.options ?? []).map((option) => <option key={String(option.value)} value={String(option.value)}>{option.label}</option>)}
      </select>
    );
  } else if (field.type === 'multiselect') {
    const selected = Array.isArray(value) ? value.map(String) : [];
    control = (
      <fieldset id={id} aria-describedby={describedBy} className="grid gap-2 sm:grid-cols-2">
        {(field.options ?? []).map((option) => (
          <label key={String(option.value)} className="flex min-h-10 items-start gap-2 rounded-md border border-border px-3 py-2 text-xs">
            <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[#031121]" checked={selected.includes(String(option.value))} disabled={field.readOnly} onChange={(event) => onChange(event.target.checked ? [...selected, String(option.value)] : selected.filter((item) => item !== String(option.value)))} />
            <span><strong className="block font-medium">{option.label}</strong>{option.description && <span className="mt-0.5 block text-muted-foreground">{option.description}</span>}</span>
          </label>
        ))}
      </fieldset>
    );
  } else if (field.type === 'checkbox' || field.type === 'switch') {
    control = (
      <button {...common} type="button" role="switch" aria-checked={Boolean(value)} className="flex min-h-10 w-full items-center justify-between rounded-md border border-border px-3 py-2 text-left text-sm" onClick={() => onChange(!value)}>
        <span>{field.placeholder || field.label}</span>
        <span className={cn('relative h-5 w-9 rounded-full transition-colors', value ? 'bg-[#031121] dark:bg-primary' : 'bg-muted-foreground/30')}><span className={cn('absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform', value ? 'translate-x-[18px]' : 'translate-x-0.5')} /></span>
      </button>
    );
  } else {
    const inputType = field.type === 'integer' || field.type === 'number' ? 'number' : field.type === 'datetime' ? 'datetime-local' : field.type;
    control = <Input {...common} type={inputType} min={field.min} max={field.max} minLength={field.minLength} maxLength={field.maxLength} value={String(value ?? '')} placeholder={field.placeholder} onChange={(event) => onChange(event.target.value)} />;
  }

  return (
    <div className="min-w-0">
      <label htmlFor={field.type === 'multiselect' ? undefined : id} className={cn('mb-1.5 block text-xs font-semibold', (field.required || field.oneOfGroup) && "after:ml-1 after:text-red-600 after:content-['*']")}>{field.label}</label>
      {control}
      {field.description && <p id={`${id}-description`} className="mt-1 text-xs leading-4 text-muted-foreground">{field.description}</p>}
      {error && <p id={`${id}-error`} role="alert" className="mt-1 text-xs text-red-700 dark:text-red-300">{error}</p>}
    </div>
  );
}

function allowedActionModes(action: NativeActionSchema): NativeActionMode[] {
  const gatedExternal = action.execution === 'external' && action.gate?.state === 'gated';
  const modes: NativeActionMode[] = [];
  if (!gatedExternal) modes.push('execute');
  if (action.supportsDryRun || action.gate?.allowDryRun) modes.push('dry-run');
  if (action.gate?.allowConfigure) modes.push('configure');
  return [...new Set(modes)];
}

function modeLabel(mode: NativeActionMode): string {
  if (mode === 'dry-run') return 'Simulação';
  if (mode === 'configure') return 'Configuração';
  return 'Execução';
}

function ErrorState({ error, onRetry }: { error: NativeCapabilityError | Error; onRetry: () => void }) {
  const forbidden = error instanceof NativeCapabilityError && error.status === 403;
  const offline = error instanceof NativeCapabilityError && (error.status === 0 || error.code === 'offline');
  const Icon = forbidden ? LockKeyhole : offline ? CloudOff : AlertCircle;
  return (
    <div className="mx-auto flex min-h-[360px] max-w-xl flex-col items-center justify-center px-5 py-12 text-center" role="alert">
      <span className="flex h-12 w-12 items-center justify-center rounded-full border border-border bg-secondary"><Icon className="h-5 w-5" /></span>
      <h1 className="mt-4 text-lg font-semibold">{forbidden ? 'Acesso não autorizado' : offline ? 'Backend local offline' : 'Capacidade indisponível'}</h1>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">{error.message}</p>
      {!forbidden && <Button type="button" variant="outline" className="mt-5" onClick={onRetry}><RefreshCw className="h-4 w-4" />Tentar novamente</Button>}
    </div>
  );
}

function LoadingState() {
  return (
    <div className="min-h-[420px] animate-pulse px-3 py-4 sm:px-6" aria-label="Carregando capacidade nativa" role="status">
      <div className="h-16 border-b border-border bg-secondary/70" />
      <div className="grid grid-cols-2 gap-px border-b border-border bg-border py-px lg:grid-cols-4">
        {[0, 1, 2, 3].map((item) => <div key={item} className="h-24 bg-card" />)}
      </div>
      <div className="mt-6 h-10 max-w-md bg-secondary" />
      <div className="mt-4 h-56 border border-border bg-secondary/50" />
      <span className="sr-only">Carregando dados, indicadores e ações.</span>
    </div>
  );
}

export function NativeCapabilityWorkspace({
  domain,
  capability,
  transport = nativeCapabilityTransport,
  initialDocument,
  online,
  onBack,
  onOpenInventory,
  className,
}: NativeCapabilityWorkspaceProps) {
  const normalizedInitial = useMemo(
    () => initialDocument === undefined ? null : normalizeNativeCapabilityDocument(initialDocument, domain, capability),
    [capability, domain, initialDocument],
  );
  const [document, setDocument] = useState<NativeCapabilityDocument | null>(normalizedInitial);
  const documentRef = useRef<NativeCapabilityDocument | null>(normalizedInitial);
  const routeKeyRef = useRef(`${domain}:${capability}`);
  const tabGroupId = useId();
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [phase, setPhase] = useState<WorkspacePhase>(normalizedInitial ? 'ready' : 'loading');
  const [loadError, setLoadError] = useState<NativeCapabilityError | Error | null>(null);
  const [browserOnline, setBrowserOnline] = useState(() => typeof navigator === 'undefined' ? true : navigator.onLine);
  const [activeTab, setActiveTab] = useState('');
  const [selection, setSelection] = useState<DetailSelection>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [activeAction, setActiveAction] = useState<NativeActionSchema | null>(null);
  const [actionContext, setActionContext] = useState<NativeRecord | undefined>();
  const [actionValues, setActionValues] = useState<FormValues>({});
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({});
  const [actionStep, setActionStep] = useState<'form' | 'confirm'>('form');
  const [actionMode, setActionMode] = useState<NativeActionMode>('execute');
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState('');
  const isOnline = online ?? browserOnline;

  useEffect(() => {
    documentRef.current = document;
  }, [document]);

  useEffect(() => {
    if (online !== undefined || typeof window === 'undefined') return;
    const markOnline = () => setBrowserOnline(true);
    const markOffline = () => setBrowserOnline(false);
    window.addEventListener('online', markOnline);
    window.addEventListener('offline', markOffline);
    return () => {
      window.removeEventListener('online', markOnline);
      window.removeEventListener('offline', markOffline);
    };
  }, [online]);

  const load = useCallback(async (signal?: AbortSignal, quiet = false) => {
    if (!isOnline) {
      if (!documentRef.current) setPhase('offline');
      return;
    }
    if (!quiet) setPhase(documentRef.current ? 'refreshing' : 'loading');
    setLoadError(null);
    try {
      const next = await transport.load(domain, capability, signal);
      if (signal?.aborted) return;
      setDocument(next);
      setPhase('ready');
      setActiveTab((current) => current || next.tabs[0]?.id || (next.audit.length ? '__audit' : ''));
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      const normalized = error instanceof Error ? error : new Error('Falha desconhecida ao carregar a capacidade.');
      setLoadError(normalized);
      if (!documentRef.current) setPhase(normalized instanceof NativeCapabilityError && normalized.status === 0 ? 'offline' : 'error');
      else {
        setPhase('ready');
        setNotice(normalized.message);
      }
    }
  }, [capability, domain, isOnline, transport]);

  useEffect(() => {
    const routeKey = `${domain}:${capability}`;
    const routeChanged = routeKeyRef.current !== routeKey;
    routeKeyRef.current = routeKey;
    if (routeChanged) {
      setSelection(null);
      setNotice(null);
      setActiveTab('');
    }
    if (normalizedInitial) {
      setDocument(normalizedInitial);
      documentRef.current = normalizedInitial;
      setPhase('ready');
      setActiveTab(normalizedInitial.tabs[0]?.id || (normalizedInitial.audit.length ? '__audit' : ''));
      return;
    }
    if (routeChanged) {
      setDocument(null);
      documentRef.current = null;
    }
    if (!isOnline) {
      setPhase(documentRef.current ? 'ready' : 'offline');
      return;
    }
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [capability, domain, isOnline, load, normalizedInitial]);

  const tabs = useMemo(() => {
    if (!document) return [];
    const hasAuditTab = document.tabs.some((tab) => tab.id === '__audit');
    return document.audit.length && !hasAuditTab
      ? [...document.tabs, { id: '__audit', label: 'Auditoria', count: document.audit.length, sections: [{ id: '__audit-trail', type: 'timeline' as const, title: 'Trilha de auditoria', description: 'Ações e mudanças registradas para esta capacidade.', items: document.audit }] }]
      : document.tabs;
  }, [document]);

  const tabDomIds = useMemo(() => tabs.map((_, index) => ({
    tab: `${tabGroupId}-tab-${index}`,
    panel: `${tabGroupId}-tabpanel-${index}`,
  })), [tabGroupId, tabs]);

  const activateTabAtIndex = useCallback((index: number, moveFocus = false) => {
    const tab = tabs[index];
    if (!tab) return;
    setActiveTab(tab.id);
    setSelection(null);
    if (moveFocus) tabRefs.current[index]?.focus();
  }, [tabs]);

  const handleTabKeyDown = useCallback((event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = tabs.length - 1;
    if (nextIndex === null || !tabs.length) return;
    event.preventDefault();
    activateTabAtIndex(nextIndex, true);
  }, [activateTabAtIndex, tabs.length]);

  useEffect(() => {
    if (!tabs.length) return;
    if (!tabs.some((tab) => tab.id === activeTab)) setActiveTab(tabs[0].id);
  }, [activeTab, tabs]);

  const actionsById = useMemo(() => new Map((document?.actions ?? []).map((action) => [action.id, action])), [document?.actions]);
  const referencedActions = useMemo(() => new Set((document?.tabs ?? []).flatMap((tab) => tab.sections.flatMap((section) => [
    ...(section.actionIds ?? []),
    ...('rowActionIds' in section ? section.rowActionIds ?? [] : []),
  ]))), [document?.tabs]);
  const globalActions = useMemo(() => (document?.actions ?? []).filter((action) => !referencedActions.has(action.id)), [document?.actions, referencedActions]);

  const openAction = useCallback((action: NativeActionSchema, context?: NativeRecord) => {
    if (actionBlockedReason(action, isOnline)) return;
    const modes = allowedActionModes(action);
    if (!modes.length) return;
    setActiveAction(action);
    setActionContext(context);
    setActionValues(Object.fromEntries(action.fields.map((field) => [field.name, initialFieldValue(field, context)])));
    setActionErrors({});
    setActionError(null);
    setActionStep('form');
    setActionMode(modes.includes('dry-run') && action.execution === 'external' && action.gate?.state === 'gated' ? 'dry-run' : modes[0]);
    setIdempotencyKey(createNativeIdempotencyKey(domain, capability, action.id));
  }, [capability, domain, isOnline]);

  const closeAction = useCallback(() => {
    if (actionPending) return;
    setActiveAction(null);
    setActionContext(undefined);
    setActionValues({});
    setActionError(null);
    setActionErrors({});
  }, [actionPending]);

  const advanceAction = useCallback(() => {
    if (!activeAction) return;
    const validation = validateActionValues(activeAction, actionValues);
    setActionErrors(validation.errors);
    if (Object.keys(validation.errors).length) return;
    setActionValues(validation.values);
    setActionStep('confirm');
  }, [activeAction, actionValues]);

  const executeAction = useCallback(async () => {
    if (!activeAction || !documentRef.current) return;
    const modes = allowedActionModes(activeAction);
    if (!modes.includes(actionMode)) {
      setActionError('Este modo de operação não está autorizado pelo gate atual.');
      return;
    }
    if (activeAction.execution === 'external' && activeAction.gate?.state === 'gated' && actionMode === 'execute') {
      setActionError('A execução externa permanece bloqueada. Use simulação ou configuração.');
      return;
    }
    const snapshot = documentRef.current;
    const transientFieldNames = new Set(
      activeAction.fields.filter((field) => field.type === 'password').map((field) => field.name),
    );
    const publicActionValues = Object.fromEntries(
      Object.entries(actionValues).filter(([name]) => !transientFieldNames.has(name)),
    ) as NativeRecord;
    const transientSecrets = Object.fromEntries(
      Object.entries(actionValues).filter(
        ([name, value]) => transientFieldNames.has(name) && typeof value === 'string' && value.length > 0,
      ),
    ) as Record<string, string>;
    const optimistic = actionMode === 'dry-run'
      ? snapshot
      : applyNativeOptimisticPatch(snapshot, activeAction.optimisticPatch, publicActionValues);
    if (optimistic !== snapshot) setDocument(optimistic);
    setActionPending(true);
    setActionError(null);
    try {
      const result: NativeCapabilityActionResult = await transport.execute(domain, capability, activeAction, {
        actionId: activeAction.id,
        values: publicActionValues,
        transientSecrets,
        mode: actionMode,
        version: snapshot.version,
        idempotencyKey,
      });
      const next = result.document ?? {
        ...optimistic,
        version: result.version ?? optimistic.version,
        audit: result.auditEntry ? [result.auditEntry, ...optimistic.audit] : optimistic.audit,
      };
      setDocument(next);
      documentRef.current = next;
      setPhase('ready');
      setNotice(actionMode === 'dry-run'
        ? 'Simulação concluída. Nenhum efeito externo foi executado.'
        : actionMode === 'configure'
          ? result.message || 'Configuração salva e registrada na auditoria.'
          : result.message || (result.idempotentReplay ? 'A operação já havia sido aplicada; nenhum efeito foi duplicado.' : 'Operação concluída e registrada na auditoria.'));
      setActiveAction(null);
      setActionValues({});
      setSelection((current) => current && actionContext ? { ...current, data: actionContext } : current);
    } catch (error) {
      setDocument(snapshot);
      documentRef.current = snapshot;
      const normalized = error instanceof Error ? error : new Error('Não foi possível concluir a operação.');
      setActionError(normalized instanceof NativeCapabilityError && normalized.status === 409
        ? 'Os dados mudaram desde a abertura desta tela. Atualize a capacidade antes de tentar novamente.'
        : normalized.message);
      setActionStep('confirm');
    } finally {
      setActionPending(false);
    }
  }, [actionContext, actionMode, actionValues, activeAction, capability, domain, idempotencyKey, transport]);

  if (phase === 'loading' && !document) return <LoadingState />;
  if ((phase === 'error' || phase === 'offline') && !document) {
    return <ErrorState error={loadError ?? new NativeCapabilityError(0, 'O backend local está offline ou inacessível.', 'offline')} onRetry={() => void load()} />;
  }
  if (!document) return <ErrorState error={new Error('A capacidade não retornou um documento utilizável.')} onRetry={() => void load()} />;
  if (document.access.state === 'forbidden') {
    return <ErrorState error={new NativeCapabilityError(403, document.access.reason || 'Seu perfil não possui acesso a esta capacidade.')} onRetry={() => void load()} />;
  }

  const active = tabs.find((tab) => tab.id === activeTab) ?? tabs[0];
  const hasContent = document.kpis.length > 0 || tabs.some((tab) => tab.sections.length > 0) || document.actions.length > 0;
  const detailActions = selection?.actionIds.map((id) => actionsById.get(id)).filter(Boolean) as NativeActionSchema[] | undefined;

  return (
    <section aria-label="Operação da capacidade" className={cn('min-w-0 bg-background text-foreground', className)} data-testid="native-capability-workspace">
      <header className="border-b border-border bg-card px-3 py-3 sm:px-6">
        <div className="flex min-w-0 flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            {onBack && <Button type="button" variant="ghost" size="icon" className="h-9 w-9 shrink-0" aria-label="Voltar" onClick={onBack}><ArrowLeft className="h-4 w-4" /></Button>}
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2 text-2xs font-semibold uppercase text-muted-foreground">
                <span>{document.domain.label}</span><span aria-hidden="true">/</span><span>{document.capability.category || 'Recurso nativo'}</span>
              </div>
              <h1 className="mt-1 break-words text-2xl font-semibold">{document.capability.label}</h1>
              {document.capability.description && <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">{nativeDisplayText(document.capability.description)}</p>}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 lg:justify-end">
            {onOpenInventory && (
              <Button type="button" variant="outline" size="sm" className="h-9" onClick={onOpenInventory}>
                <List className="h-4 w-4" />Funções cobertas
              </Button>
            )}
            <Button type="button" variant="outline" size="sm" className="h-9" disabled={phase === 'refreshing' || !isOnline} onClick={() => void load()}>
              {phase === 'refreshing' ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}Atualizar
            </Button>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border pt-3 text-xs text-muted-foreground">
          <span className="inline-flex min-w-0 items-center gap-1.5"><Database className="h-3.5 w-3.5 shrink-0" /><strong className="text-foreground">Fonte:</strong><span className="truncate">núcleo canônico</span></span>
          <KlmPill tone={healthTone(document.health.state)}>{healthLabel(document)}</KlmPill>
          <span>Último sync: {formatTimestamp(document.health.lastSyncAt)}</span>
          {document.health.latencyMs !== null && document.health.latencyMs !== undefined && <span>{document.health.latencyMs} ms</span>}
          <span>versão {document.version}</span>
        </div>
      </header>

      {(!isOnline || document.health.state === 'offline' || document.access.state === 'gated' || document.health.state === 'gated') && (
        <div className={cn('flex items-start gap-3 border-b px-3 py-3 text-xs leading-5 sm:px-6', !isOnline || document.health.state === 'offline' ? 'border-red-200 bg-red-50 text-red-900 dark:border-red-950 dark:bg-red-950/30 dark:text-red-100' : 'border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-950 dark:bg-amber-950/30 dark:text-amber-100')} role="status">
          {!isOnline || document.health.state === 'offline' ? <CloudOff className="mt-0.5 h-4 w-4 shrink-0" /> : <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />}
          <span>{!isOnline ? 'Modo offline: os dados já carregados permanecem disponíveis somente para consulta.' : document.access.reason || document.health.message || 'Esta capacidade está sob gate. Configuração e simulação continuam disponíveis quando autorizadas, sem produzir efeito externo.'}</span>
        </div>
      )}

      {notice && (
        <div className="flex items-start justify-between gap-3 border-b border-primary/30 bg-primary/10 px-3 py-3 text-xs leading-5 sm:px-6" aria-live="polite">
          <span className="flex items-start gap-2"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[#7a5309]" />{notice}</span>
          <button type="button" className="shrink-0 text-muted-foreground" aria-label="Fechar aviso" onClick={() => setNotice(null)}><X className="h-4 w-4" /></button>
        </div>
      )}

      {document.kpis.length > 0 && (
        <section aria-label="Indicadores da capacidade" className="grid grid-cols-2 divide-x divide-y divide-border border-b border-border bg-card sm:grid-cols-3 lg:grid-cols-6 lg:divide-y-0">
          {document.kpis.map((kpi) => {
            const TrendIcon = kpi.trend === 'up' ? ArrowUpRight : kpi.trend === 'down' ? ArrowDownRight : Minus;
            return (
              <div key={kpi.id} className="min-w-0 px-3 py-4 sm:px-5">
                <p className="truncate text-2xs font-semibold uppercase text-muted-foreground" title={kpi.label}>{kpi.label}</p>
                <strong className="mt-1 block break-words text-lg font-semibold leading-6">{kpi.displayValue ?? formatValue(kpi.value, kpi.format)}</strong>
                {kpi.delta !== undefined && kpi.delta !== null && <span className={cn('mt-1 inline-flex items-center gap-1 text-xs', kpi.trend === 'up' && 'text-emerald-700 dark:text-emerald-400', kpi.trend === 'down' && 'text-red-700 dark:text-red-400', (!kpi.trend || kpi.trend === 'neutral') && 'text-muted-foreground')}><TrendIcon className="h-3 w-3" />{kpi.delta}</span>}
              </div>
            );
          })}
        </section>
      )}

      {globalActions.length > 0 && (
        <section aria-label="Ações da capacidade" className="flex flex-wrap items-center gap-2 border-b border-border bg-secondary/45 px-3 py-3 sm:px-6">
          <span className="mr-1 text-2xs font-semibold uppercase text-muted-foreground">Ações</span>
          {globalActions.map((action) => <ActionButton key={action.id} action={action} online={isOnline && document.health.state !== 'offline'} onClick={() => openAction(action)} />)}
        </section>
      )}

      {!hasContent ? (
        <div className="mx-auto flex min-h-[320px] max-w-lg flex-col items-center justify-center px-5 py-12 text-center">
          <Database className="h-6 w-6 text-muted-foreground" />
          <h2 className="mt-3 text-base font-semibold">Nenhum dado disponível</h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">O schema desta capacidade não retornou indicadores, seções ou ações para o contexto atual.</p>
        </div>
      ) : (
        <>
          {tabs.length > 0 && (
            <nav className="scrollbar-thin overflow-x-auto border-b border-border bg-card px-3 sm:px-6">
              <div role="tablist" aria-label="Visões da capacidade" aria-orientation="horizontal" className="flex min-w-max gap-5">
                {tabs.map((tab, index) => (
                  <button
                    key={tab.id}
                    ref={(node) => { tabRefs.current[index] = node; }}
                    id={tabDomIds[index].tab}
                    type="button"
                    role="tab"
                    aria-controls={tabDomIds[index].panel}
                    aria-selected={active?.id === tab.id}
                    tabIndex={active?.id === tab.id ? 0 : -1}
                    className={cn('flex h-11 items-center gap-2 border-b-2 text-xs font-semibold', active?.id === tab.id ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground')}
                    onClick={() => activateTabAtIndex(index)}
                    onKeyDown={(event) => handleTabKeyDown(event, index)}
                  >
                    {tab.label}{tab.count !== undefined && <span className="rounded-full bg-secondary px-1.5 py-0.5 text-2xs">{tab.count}</span>}
                  </button>
                ))}
              </div>
            </nav>
          )}

          <div className="grid min-w-0 gap-0 xl:grid-cols-[minmax(0,1fr)_minmax(280px,340px)]">
            {tabs.length > 0 ? tabs.map((tab, index) => {
              const isActiveTab = active?.id === tab.id;
              return (
                <div
                  key={tab.id}
                  id={tabDomIds[index].panel}
                  className="min-w-0 px-3 py-5 sm:px-6"
                  role="tabpanel"
                  aria-labelledby={tabDomIds[index].tab}
                  tabIndex={isActiveTab ? 0 : -1}
                  hidden={!isActiveTab}
                >
                  {isActiveTab && (
                    <>
                      {tab.description && <p className="mb-5 max-w-3xl text-xs leading-5 text-muted-foreground">{tab.description}</p>}
                      {tab.sections.map((section) => {
                        const sectionActions = (section.actionIds ?? []).map((id) => actionsById.get(id)).filter(Boolean) as NativeActionSchema[];
                        return (
                          <SectionShell key={section.id} section={section} actions={sectionActions} online={isOnline && document.health.state !== 'offline'} onAction={(action) => openAction(action)}>
                            {section.type === 'table' && <TableSection section={section} actions={(section.rowActionIds ?? []).map((id) => actionsById.get(id)).filter(Boolean) as NativeActionSchema[]} onSelect={setSelection} />}
                            {section.type === 'details' && <DetailsSection section={section} />}
                            {section.type === 'list' && <ListSection section={section} onSelect={setSelection} />}
                            {section.type === 'timeline' && <TimelineSection section={section} />}
                            {section.type === 'chart' && <ChartSection section={section} />}
                            {section.type === 'notice' && <NoticeSection section={section} />}
                            {section.type === 'structured' && <StructuredSection section={section} />}
                          </SectionShell>
                        );
                      })}
                      {tab.sections.length === 0 && <SectionEmpty label={tab.label} />}
                    </>
                  )}
                </div>
              );
            }) : <div className="min-w-0 px-3 py-5 sm:px-6" />}

            <aside className={cn('min-w-0 border-t border-border bg-secondary/35 px-3 py-5 sm:px-6 xl:min-h-[520px] xl:border-l xl:border-t-0', !selection && 'hidden xl:block')} aria-label="Detalhes do registro" data-testid="native-detail-pane">
              {selection ? (
                <div className="xl:sticky xl:top-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0"><p className="text-2xs font-semibold uppercase text-[#0057d8]">Detalhes</p><h2 className="mt-1 break-words text-base font-semibold">{selection.title}</h2></div>
                    <Button type="button" variant="ghost" size="icon" className="h-8 w-8 shrink-0" aria-label="Fechar detalhes" onClick={() => setSelection(null)}><X className="h-4 w-4" /></Button>
                  </div>
                  <div className="mt-4 border-t border-border pt-4">
                    {selection.fields?.length ? (
                      <dl className="grid gap-x-5 gap-y-3 sm:grid-cols-2 xl:grid-cols-1">
                        {selection.fields.map((field) => (
                          <div key={field.key} className="min-w-0">
                            <dt className="text-2xs font-semibold uppercase text-muted-foreground">{field.label}</dt>
                            <dd className="mt-1 break-words text-xs leading-5">
                              {typeof selection.data[field.key] === 'object' && selection.data[field.key] !== null
                                ? <StructuredValue value={selection.data[field.key]} />
                                : <ValueCell value={selection.data[field.key]} format={field.format} />}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    ) : <StructuredValue value={selection.data} />}
                  </div>
                  {detailActions && detailActions.length > 0 && (
                    <div className="mt-5 flex flex-col gap-2 border-t border-border pt-4">
                      <p className="text-2xs font-semibold uppercase text-muted-foreground">Ações do registro</p>
                      {detailActions.map((action) => <ActionButton key={action.id} action={action} online={isOnline && document.health.state !== 'offline'} onClick={() => openAction(action, selection.data)} />)}
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex min-h-40 flex-col items-center justify-center text-center text-muted-foreground">
                  <CircleGauge className="h-5 w-5" />
                  <p className="mt-2 text-xs leading-5">Selecione um registro para inspecionar todos os campos e ações disponíveis.</p>
                </div>
              )}
            </aside>
          </div>
        </>
      )}

      <Dialog open={Boolean(activeAction)} onOpenChange={(open) => { if (!open) closeAction(); }}>
        <DialogContent className="max-h-[calc(100vh-1rem)] w-[calc(100%-1rem)] max-w-2xl overflow-y-auto p-4 sm:p-6">
          {activeAction && (
            <>
              <DialogHeader>
                <div className="flex flex-wrap items-center gap-2 pr-8">
                  <KlmPill tone={activeAction.execution === 'external' ? 'gold' : 'black'}>{activeAction.execution === 'external' ? 'efeito externo' : 'operação local'}</KlmPill>
                  <KlmPill tone="muted">versão {document.version}</KlmPill>
                </div>
                <DialogTitle className="pt-2 text-left">{activeAction.label}</DialogTitle>
                <DialogDescription className="text-left leading-5">{activeAction.description || 'Preencha os dados e revise antes de confirmar.'}</DialogDescription>
              </DialogHeader>

              {allowedActionModes(activeAction).length > 1 && actionStep === 'form' && (
                <fieldset>
                  <legend className="mb-2 text-xs font-semibold">Modo</legend>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                    {allowedActionModes(activeAction).map((mode) => (
                      <button key={mode} type="button" aria-pressed={actionMode === mode} className={cn('min-h-10 rounded-md border px-3 text-xs font-semibold', actionMode === mode ? 'border-[#031121] bg-[#031121] text-white dark:border-primary dark:bg-primary dark:text-black' : 'border-border bg-card')} onClick={() => setActionMode(mode)}>{modeLabel(mode)}</button>
                    ))}
                  </div>
                </fieldset>
              )}

              {activeAction.execution === 'external' && activeAction.gate?.state === 'gated' && (
                <div className="flex items-start gap-2 border-l-4 border-amber-500 bg-amber-500/10 px-3 py-2 text-xs leading-5">
                  <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{activeAction.gate.reason || 'A execução externa está bloqueada.'} {actionMode === 'dry-run' && 'A simulação não envia, publica, cobra nem altera o sistema externo.'}</span>
                </div>
              )}

              {actionStep === 'form' ? (
                <form className="grid gap-4" onSubmit={(event) => { event.preventDefault(); advanceAction(); }}>
                  {activeAction.fields.filter((field) => fieldVisible(field, actionValues)).map((field) => (
                    <ActionField key={field.name} field={field} value={actionValues[field.name]} error={actionErrors[field.name]} onChange={(value) => { setActionValues((current) => ({ ...current, [field.name]: value })); setActionErrors((current) => ({ ...current, [field.name]: '' })); }} />
                  ))}
                  {!activeAction.fields.some((field) => field.type !== 'hidden' && fieldVisible(field, actionValues)) && <p className="border border-dashed border-border p-3 text-xs text-muted-foreground">Esta ação não exige parâmetros. A confirmação ainda é obrigatória.</p>}
                  {actionError && <p role="alert" className="border border-red-300 bg-red-50 p-3 text-xs leading-5 text-red-800 dark:border-red-950 dark:bg-red-950/30 dark:text-red-200">{actionError}</p>}
                  <DialogFooter className="gap-2 sm:gap-0">
                    <Button type="button" variant="outline" onClick={closeAction}>Cancelar</Button>
                    <Button type="submit" className="bg-[#031121] text-white hover:bg-[#202020]">Revisar ação<ChevronRight className="h-4 w-4" /></Button>
                  </DialogFooter>
                </form>
              ) : (
                <div className="space-y-4">
                  <div className="border border-border bg-secondary/55 p-4">
                    <p className="text-xs font-semibold uppercase text-muted-foreground">Confirmação obrigatória</p>
                    <h3 className="mt-2 text-base font-semibold">{activeAction.confirmation?.title || `Confirmar ${activeAction.label.toLocaleLowerCase('pt-BR')}?`}</h3>
                    <p className="mt-2 text-xs leading-5 text-muted-foreground">{activeAction.confirmation?.body || 'A ação usará controle de versão e uma chave de idempotência exclusiva. Em caso de conflito, nenhuma alteração otimista será mantida.'}</p>
                    <dl className="mt-4 grid gap-2 border-t border-border pt-3 text-xs sm:grid-cols-2">
                      <div><dt className="text-muted-foreground">Modo</dt><dd className="mt-0.5 font-semibold">{modeLabel(actionMode)}</dd></div>
                      <div><dt className="text-muted-foreground">Versão esperada</dt><dd className="mt-0.5 font-mono font-semibold">{document.version}</dd></div>
                      <div className="sm:col-span-2"><dt className="text-muted-foreground">Idempotência</dt><dd className="mt-0.5 break-all font-mono text-2xs">{idempotencyKey}</dd></div>
                    </dl>
                  </div>
                  {actionMode === 'dry-run' && <p className="flex items-start gap-2 border-l-4 border-primary bg-primary/10 px-3 py-2 text-xs leading-5"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />Nenhum efeito externo será executado. O resultado será apenas uma simulação auditável.</p>}
                  {actionError && <p role="alert" className="border border-red-300 bg-red-50 p-3 text-xs leading-5 text-red-800 dark:border-red-950 dark:bg-red-950/30 dark:text-red-200">{actionError}</p>}
                  <DialogFooter className="gap-2 sm:gap-0">
                    <Button type="button" variant="outline" disabled={actionPending} onClick={() => setActionStep('form')}><ChevronLeft className="h-4 w-4" />Revisar dados</Button>
                    <Button type="button" variant={activeAction.intent === 'danger' ? 'destructive' : 'default'} className={cn(activeAction.intent !== 'danger' && 'bg-[#031121] text-white hover:bg-[#202020]')} disabled={actionPending} onClick={() => void executeAction()}>
                      {actionPending ? <Loader2 className="h-4 w-4 animate-spin" /> : actionMode === 'dry-run' ? <Activity className="h-4 w-4" /> : <Check className="h-4 w-4" />}
                      {activeAction.confirmation?.confirmLabel || (actionMode === 'dry-run' ? 'Executar simulação' : 'Confirmar operação')}
                    </Button>
                  </DialogFooter>
                </div>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}
