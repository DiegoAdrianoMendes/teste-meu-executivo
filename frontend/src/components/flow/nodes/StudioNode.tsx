import { Handle, Position, type NodeProps } from '@xyflow/react';
import {
  BadgeDollarSign,
  BellRing,
  BookOpenCheck,
  Bot,
  Braces,
  Cable,
  Calculator,
  CircleDotDashed,
  ClipboardList,
  Combine,
  FileClock,
  FileSignature,
  FileText,
  GitFork,
  Globe2,
  Link2,
  ListRestart,
  LogOut,
  Merge,
  MessageSquareReply,
  PenLine,
  RefreshCcw,
  Route,
  ShieldAlert,
  Sparkles,
  Tags,
  UserRoundCheck,
} from 'lucide-react';

const presentation: Record<string, { eyebrow: string; color: string; icon: typeof Route }> = {
  router: { eyebrow: 'Roteador', color: 'indigo', icon: Route },
  action: { eyebrow: 'Ação', color: 'slate', icon: CircleDotDashed },
  end: { eyebrow: 'Fim do fluxo', color: 'slate', icon: LogOut },
  form: { eyebrow: 'Formulário', color: 'cyan', icon: FileText },
  llm: { eyebrow: 'Modelo de IA', color: 'violet', icon: Sparkles },
  human_transfer: { eyebrow: 'Transferência humana', color: 'amber', icon: UserRoundCheck },
  landing_page: { eyebrow: 'Página de destino', color: 'emerald', icon: Globe2 },
  merge: { eyebrow: 'Unir rotas', color: 'indigo', icon: Merge },
  loop: { eyebrow: 'Iterador', color: 'indigo', icon: RefreshCcw },
  error_handler: { eyebrow: 'Recuperação', color: 'red', icon: ShieldAlert },
  set_variable: { eyebrow: 'Variável', color: 'cyan', icon: Braces },
  transform: { eyebrow: 'Mapear dados', color: 'cyan', icon: GitFork },
  aggregate: { eyebrow: 'Agregação', color: 'cyan', icon: Calculator },
  link_action: { eyebrow: 'Link / CTA', color: 'emerald', icon: Link2 },
  create_task: { eyebrow: 'Tarefa interna', color: 'emerald', icon: ClipboardList },
  update_card: { eyebrow: 'Caso 360', color: 'emerald', icon: Tags },
  internal_note: { eyebrow: 'Nota interna', color: 'emerald', icon: PenLine },
  audit_event: { eyebrow: 'Auditoria', color: 'slate', icon: BookOpenCheck },
  payment_request: { eyebrow: 'Pagamento', color: 'amber', icon: BadgeDollarSign },
  social_action: { eyebrow: 'Ação social', color: 'pink', icon: MessageSquareReply },
  connector_action: { eyebrow: 'Conector', color: 'violet', icon: Cable },
  scheduled_message: { eyebrow: 'Mensagem agendada', color: 'cyan', icon: FileClock },
  agent: { eyebrow: 'Agente', color: 'violet', icon: Bot },
  zapsign_request: { eyebrow: 'Assinatura', color: 'emerald', icon: FileSignature },
};

const colorClasses: Record<string, { border: string; icon: string; handle: string }> = {
  indigo: { border: 'border-indigo-400', icon: 'bg-indigo-400/15 text-indigo-600', handle: '!border-indigo-600 !bg-indigo-400' },
  red: { border: 'border-red-400', icon: 'bg-red-400/15 text-red-600', handle: '!border-red-600 !bg-red-400' },
  cyan: { border: 'border-cyan-400', icon: 'bg-cyan-400/15 text-cyan-600', handle: '!border-cyan-600 !bg-cyan-400' },
  emerald: { border: 'border-emerald-400', icon: 'bg-emerald-400/15 text-emerald-600', handle: '!border-emerald-600 !bg-emerald-400' },
  slate: { border: 'border-slate-400', icon: 'bg-slate-400/15 text-slate-600', handle: '!border-slate-600 !bg-slate-400' },
  amber: { border: 'border-amber-400', icon: 'bg-amber-400/15 text-amber-600', handle: '!border-amber-600 !bg-amber-400' },
  pink: { border: 'border-pink-400', icon: 'bg-pink-400/15 text-pink-600', handle: '!border-pink-600 !bg-pink-400' },
  violet: { border: 'border-violet-400', icon: 'bg-violet-400/15 text-violet-600', handle: '!border-violet-600 !bg-violet-400' },
};

function routesFromData(data: Record<string, unknown>, type: string) {
  if (type === 'loop') return [{ key: 'loop', label: 'Iterar' }, { key: 'done', label: 'Concluído' }];
  if (type !== 'router' || !Array.isArray(data.routes)) return [];
  return data.routes
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    .map((item) => ({ key: String(item.key ?? ''), label: String(item.label ?? item.key ?? '') }))
    .filter((item) => item.key);
}

export function StudioNode({ data, selected, type }: NodeProps) {
  const nodeData = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
  const nodeType = String(type || 'operation');
  const info = presentation[nodeType] ?? { eyebrow: 'Operação', color: 'slate', icon: CircleDotDashed };
  const colors = colorClasses[info.color] ?? colorClasses.slate;
  const Icon = info.icon;
  const routes = routesFromData(nodeData, nodeType);
  const label = String(nodeData.label || info.eyebrow);
  const detail = nodeType === 'connector_action'
    ? `${String(nodeData.connector || 'conector')} · ${String(nodeData.operation || 'ação')}`
    : nodeType === 'payment_request'
      ? `${String(nodeData.payment_method || 'pagamento')} · ${String(nodeData.payment_provider || 'provedor')}`
      : nodeType === 'social_action'
        ? String(nodeData.social_action || 'ação social').replaceAll('_', ' ')
        : nodeType === 'link_action'
          ? String(nodeData.link_kind || 'link').replaceAll('_', ' ')
          : null;

  return (
    <div className={`relative min-w-[190px] max-w-[240px] rounded-lg border-2 bg-card px-4 py-3 shadow-sm ${selected ? 'border-primary' : colors.border}`}>
      <Handle type="target" position={Position.Top} className={colors.handle} />
      <div className="flex items-center gap-2">
        <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${colors.icon}`}><Icon className="h-4 w-4" /></div>
        <div className="min-w-0"><p className="truncate text-2xs uppercase tracking-wider text-muted-foreground">{info.eyebrow}</p><p className="truncate text-xs font-semibold text-foreground" title={label}>{label}</p></div>
      </div>
      {detail ? <p className="mt-2 truncate pl-9 text-2xs text-muted-foreground">{detail}</p> : null}
      {routes.length ? (
        <div className="mt-3 flex min-h-5 items-center justify-around gap-1 border-t pt-2">
          {routes.map((route, index) => (
            <div key={route.key} className="relative min-w-0 flex-1 text-center">
              <span className="block truncate text-2xs font-semibold text-muted-foreground" title={route.label}>{route.label}</span>
              <Handle type="source" id={route.key} position={Position.Bottom} className={colors.handle} style={{ left: `${((index + 1) / (routes.length + 1)) * 100}%` }} />
            </div>
          ))}
        </div>
      ) : <Handle type="source" position={Position.Bottom} className={colors.handle} />}
    </div>
  );
}
