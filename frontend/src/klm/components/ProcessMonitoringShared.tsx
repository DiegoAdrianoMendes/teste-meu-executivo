import { useState } from 'react';
import { CalendarClock, Check, Loader2, ShieldAlert, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import type { KlmProcessPublication } from '@/klm/api';

/**
 * Peças compartilhadas entre `ProcessosPage` (aba "Publicações e
 * intimações") e `PrazosPage` (agenda de prazos) — módulo processual
 * contratável à parte. Mantém a mensagem de "módulo não contratado", o
 * cálculo do badge D-N e o modal de memória de cálculo consistentes nas
 * duas telas.
 */

export function ProcessMonitoringModuleGate({ description }: { description: string }) {
  return (
    <KlmPanel>
      <div className="flex items-start gap-3 p-4">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary">
          <ShieldAlert className="h-4 w-4 text-muted-foreground" />
        </span>
        <div>
          <p className="text-sm font-semibold">Módulo processual não contratado</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
        </div>
      </div>
    </KlmPanel>
  );
}

export type DeadlineBadge = { label: string; tone: 'red' | 'gold' | 'blue' | 'muted' };

/** Dias corridos entre hoje e `dataFatal` (YYYY-MM-DD), ignorando hora. */
export function daysUntilDeadline(dataFatal: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const [year, month, day] = dataFatal.split('-').map(Number);
  const target = new Date(year, (month ?? 1) - 1, day ?? 1);
  target.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / 86_400_000);
}

/** Badge D-7/D-3/D-1/HOJE/VENCIDO conforme a proximidade da data fatal. */
export function deadlineBadge(dataFatal: string): DeadlineBadge {
  const diff = daysUntilDeadline(dataFatal);
  if (diff < 0) return { label: 'VENCIDO', tone: 'red' };
  if (diff === 0) return { label: 'HOJE', tone: 'red' };
  if (diff === 1) return { label: 'D-1', tone: 'red' };
  if (diff <= 3) return { label: `D-${diff}`, tone: 'gold' };
  if (diff <= 7) return { label: `D-${diff}`, tone: 'blue' };
  return { label: `D-${diff}`, tone: 'muted' };
}

export function formatDateBR(value: string): string {
  const [year, month, day] = value.split('-').map(Number);
  return new Date(year, (month ?? 1) - 1, day ?? 1).toLocaleDateString('pt-BR');
}

const URGENCIA_TONE: Record<string, 'red' | 'gold' | 'blue' | 'muted'> = {
  critica: 'red',
  alta: 'gold',
  media: 'blue',
  baixa: 'muted',
};

export function UrgenciaPill({ urgencia }: { urgencia: string | null }) {
  if (!urgencia) return null;
  return <KlmPill tone={URGENCIA_TONE[urgencia] ?? 'muted'}>{urgencia}</KlmPill>;
}

export function FontePill({ fonte }: { fonte: string }) {
  return <KlmPill tone={fonte === 'djen' ? 'blue' : 'muted'}>{fonte.toUpperCase()}</KlmPill>;
}

/** Modal "Ver memória de cálculo" — mostra o passo a passo determinístico
 * de `app/services/prazo_calculator.py` exatamente como veio do backend
 * (nunca reformula os números, só melhora a leitura do JSON). */
export function DeadlineMemoryDialog({
  publication,
  open,
  onOpenChange,
}: {
  publication: KlmProcessPublication | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <CalendarClock className="h-4 w-4 text-primary" />Memória de cálculo do prazo
          </DialogTitle>
          <DialogDescription>
            {publication ? `Processo ${publication.numeroCnj} — passo a passo determinístico usado para chegar na data fatal.` : ''}
          </DialogDescription>
        </DialogHeader>
        {publication && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2 text-2xs">
              <div className="rounded-md border p-2">
                <span className="block text-muted-foreground">Dias</span>
                <span className="font-semibold tabular-nums">
                  {publication.dias} {publication.tipoContagem === 'dias_uteis' ? 'úteis' : 'corridos'}
                </span>
              </div>
              <div className="rounded-md border p-2">
                <span className="block text-muted-foreground">Data fatal</span>
                <span className="font-semibold tabular-nums">
                  {publication.dataFatal ? formatDateBR(publication.dataFatal) : '—'}
                </span>
              </div>
            </div>
            <ol className="space-y-2 rounded-md border p-3 text-2xs">
              {(publication.memoriaCalculo ?? []).length === 0 && (
                <li className="text-muted-foreground">Sem memória de cálculo registrada para esta publicação.</li>
              )}
              {(publication.memoriaCalculo ?? []).map((passo, index) => (
                <li key={index} className="flex items-start gap-2 border-b pb-2 last:border-0 last:pb-0">
                  <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-secondary text-[10px] font-semibold">
                    {index + 1}
                  </span>
                  <span className="min-w-0 break-words leading-4">
                    {Object.entries(passo).map(([key, value]) => `${key}: ${String(value)}`).join(' · ')}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Fechar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Linha de uma publicação com prazo — usada na aba "Publicações e
 * intimações" (ProcessosPage) e na agenda de prazos (PrazosPage). */
export function DeadlinePublicationRow({
  publication,
  canManage,
  onViewMemory,
  onConfirm,
  onDismiss,
  confirmPending,
  dismissPending,
  showBadge = true,
}: {
  publication: KlmProcessPublication;
  canManage: boolean;
  onViewMemory: () => void;
  onConfirm?: () => void;
  onDismiss?: () => void;
  confirmPending?: boolean;
  dismissPending?: boolean;
  showBadge?: boolean;
}) {
  const badge = showBadge && publication.dataFatal ? deadlineBadge(publication.dataFatal) : null;
  const isPending = publication.prazoStatus === 'prazo_sugerido';
  return (
    <div className="flex flex-col gap-2 border-b p-4 last:border-0 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <FontePill fonte={publication.fonte} />
          <UrgenciaPill urgencia={publication.urgencia} />
          {publication.tipo && <KlmPill tone="muted">{publication.tipo}</KlmPill>}
          <span className="text-2xs text-muted-foreground tabular-nums">{publication.numeroCnj}</span>
        </div>
        <p className="line-clamp-2 text-xs leading-5 text-foreground">{publication.conteudo}</p>
        {publication.dataFatal && (
          <button
            type="button"
            onClick={onViewMemory}
            className="inline-flex items-center gap-1 text-2xs font-medium text-primary hover:underline"
          >
            <CalendarClock className="h-3 w-3" />
            Prazo: {publication.dias} {publication.tipoContagem === 'dias_uteis' ? 'dias úteis' : 'dias corridos'} · fatal {formatDateBR(publication.dataFatal)} · Ver memória de cálculo
          </button>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {badge && <KlmPill tone={badge.tone}>{badge.label}</KlmPill>}
        {publication.prazoStatus && publication.prazoStatus !== 'prazo_sugerido' && (
          <KlmPill tone={publication.prazoStatus === 'confirmado' ? 'green' : 'muted'}>
            {publication.prazoStatus === 'confirmado' ? 'Confirmado' : 'Dispensado'}
          </KlmPill>
        )}
        {isPending && onConfirm && onDismiss && (
          <>
            <Button
              size="sm"
              variant="outline"
              disabled={!canManage || dismissPending}
              onClick={onDismiss}
              title={!canManage ? 'Permissão processos.manage necessária' : 'Dispensar prazo sugerido'}
            >
              {dismissPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <X className="h-3.5 w-3.5" />}
            </Button>
            <Button
              size="sm"
              disabled={!canManage || confirmPending}
              onClick={onConfirm}
              title={!canManage ? 'Permissão processos.manage necessária' : 'Confirmar prazo — vira compromisso oficial'}
            >
              {confirmPending ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Check className="mr-1 h-3.5 w-3.5" />}
              Confirmar
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

/** Hook local de UI para abrir o modal de memória de cálculo a partir de qualquer lista. */
export function useDeadlineMemoryDialog() {
  const [publication, setPublication] = useState<KlmProcessPublication | null>(null);
  return {
    publication,
    open: publication !== null,
    show: (item: KlmProcessPublication) => setPublication(item),
    onOpenChange: (next: boolean) => { if (!next) setPublication(null); },
  };
}
