import { useMutation, useQuery } from '@tanstack/react-query';
import {
  CheckCircle2,
  Clock3,
  Download,
  FileCheck2,
  FileWarning,
  Fingerprint,
  History,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { KlmPill } from '@/klm/components/Primitives';
import { klmApi, type KlmMessageEvidenceEvent } from '@/klm/api';
import { cn } from '@/lib/utils';
import { downloadLocalStorageObject, saveBlob } from '@/services/local-storage';

type ConversationEvidenceSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  threadId: string | null;
  messageId: string | null;
};

const eventLabels: Record<KlmMessageEvidenceEvent['eventType'], string> = {
  captured: 'Original capturado',
  edited: 'Mensagem editada',
  deleted: 'Exclusão sinalizada',
  provider_receipt: 'Recibo do provedor',
  media_captured: 'Mídia preservada',
  media_unavailable: 'Mídia indisponível',
  provider_unsupported: 'Alteração não suportada',
};

const lifecycleLabels = {
  original: 'Original',
  edited: 'Editada',
  deleted: 'Apagada no canal',
  provider_unsupported: 'Sinalização do provedor',
} as const;

function formatDate(value: string | null) {
  if (!value) return 'Horário não informado pelo provedor';
  return new Date(value).toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function shortHash(value: string | null) {
  if (!value) return 'não disponível';
  return `${value.slice(0, 12)}…${value.slice(-12)}`;
}

function EvidenceEvent({ event }: { event: KlmMessageEvidenceEvent }) {
  const hasMedia = Boolean(event.media.sha256 || event.media.providerMediaId);
  const isAlert = ['deleted', 'media_unavailable', 'provider_unsupported'].includes(event.eventType);
  const mediaDownload = useMutation({
    mutationFn: () => downloadLocalStorageObject(
      event.media.objectId!,
      event.media.storageObjectVersion ?? undefined,
    ),
    onSuccess: (blob) => saveBlob(
      blob,
      event.media.filename || `arquivo-preservado-${event.revision}`,
    ),
  });
  return (
    <li className="relative pl-8" data-testid={`evidence-event-${event.eventType}`}>
      <span
        className={cn(
          'absolute left-0 top-0.5 flex h-6 w-6 items-center justify-center rounded-full border bg-card',
          isAlert ? 'border-amber-300 text-amber-700 dark:border-amber-700 dark:text-amber-300' : 'border-emerald-300 text-emerald-700 dark:border-emerald-800 dark:text-emerald-300',
        )}
      >
        {isAlert ? <FileWarning className="h-3.5 w-3.5" /> : <FileCheck2 className="h-3.5 w-3.5" />}
      </span>
      <div className="rounded-lg border border-border bg-card p-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-2xs font-semibold">{eventLabels[event.eventType]}</p>
            <p className="mt-0.5 text-2xs text-muted-foreground">
              Evento {event.revision}{event.contentRevision ? ` · versão de conteúdo ${event.contentRevision}` : ''}
            </p>
          </div>
          <KlmPill tone={event.observationState === 'unavailable' ? 'gold' : 'muted'}>
            {event.observationState === 'provider_signal' ? 'sinal do provedor' : event.observationState === 'inferred' ? 'inferido' : event.observationState === 'unavailable' ? 'indisponível' : 'observado'}
          </KlmPill>
        </div>

        {event.bodySnapshot ? (
          <blockquote className="mt-3 whitespace-pre-wrap rounded-md border-l-2 border-primary bg-secondary px-3 py-2 text-2xs leading-4">
            {event.bodySnapshot}
          </blockquote>
        ) : null}

        {hasMedia ? (
          <dl className="mt-3 grid grid-cols-[110px_minmax(0,1fr)] gap-x-2 gap-y-1.5 border-t border-border pt-3 text-2xs">
            <dt className="text-muted-foreground">Arquivo</dt><dd className="min-w-0 truncate" title={event.media.filename ?? undefined}>{event.media.filename ?? 'nome não informado'}</dd>
            <dt className="text-muted-foreground">Tipo e tamanho</dt><dd>{event.media.mimeType ?? 'tipo não informado'}{event.media.byteSize !== null ? ` · ${new Intl.NumberFormat('pt-BR').format(event.media.byteSize)} bytes` : ''}</dd>
            <dt className="text-muted-foreground">Versão imutável</dt><dd>{event.media.storageObjectVersion ?? 'não reconciliada'}</dd>
            <dt className="text-muted-foreground">SHA-256 da mídia</dt><dd className="break-all font-mono" title={event.media.sha256 ?? undefined}>{shortHash(event.media.sha256)}</dd>
          </dl>
        ) : null}

        {event.media.objectId && event.media.storageObjectVersion ? (
          <div className="mt-3 border-t border-border pt-3">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 w-full text-2xs"
              disabled={mediaDownload.isPending}
              onClick={() => mediaDownload.mutate()}
            >
              {mediaDownload.isPending ? <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Download className="mr-1.5 h-3.5 w-3.5" />}
              {mediaDownload.isPending ? 'Verificando e preparando' : 'Baixar esta versão preservada'}
            </Button>
            {mediaDownload.isError ? <p role="alert" className="mt-2 text-center text-2xs text-red-600">O arquivo não pôde ser baixado. A cadeia permanece íntegra.</p> : null}
          </div>
        ) : null}

        <dl className="mt-3 grid grid-cols-[110px_minmax(0,1fr)] gap-x-2 gap-y-1.5 border-t border-border pt-3 text-2xs">
          <dt className="text-muted-foreground">No provedor</dt><dd>{formatDate(event.providerOccurredAt)}</dd>
          <dt className="text-muted-foreground">Recebido na plataforma</dt><dd>{formatDate(event.receivedAt)}</dd>
          <dt className="text-muted-foreground">Hash do evento</dt><dd className="break-all font-mono" title={event.evidenceSha256}>{shortHash(event.evidenceSha256)}</dd>
          <dt className="text-muted-foreground">Envelope assinado</dt><dd className="break-all font-mono" title={event.sourceEnvelopeSha256 ?? undefined}>{shortHash(event.sourceEnvelopeSha256)}</dd>
        </dl>
      </div>
    </li>
  );
}

export function ConversationEvidenceSheet({
  open,
  onOpenChange,
  threadId,
  messageId,
}: ConversationEvidenceSheetProps) {
  const evidenceQuery = useQuery({
    queryKey: ['klm', 'message-evidence', threadId, messageId],
    queryFn: () => klmApi.messageEvidence(threadId!, messageId!),
    enabled: open && Boolean(threadId && messageId),
  });
  const exportMutation = useMutation({
    mutationFn: () => klmApi.exportMessageEvidence(threadId!, messageId!),
    onSuccess: (manifest) => {
      const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `klm-evidencia-${messageId}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    },
  });
  const chain = evidenceQuery.data;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-[96vw] flex-col overflow-hidden p-0 sm:max-w-[680px]">
        <SheetHeader className="border-b border-border px-5 py-5 pr-12 text-left">
          <div className="mb-2 flex h-9 w-9 items-center justify-center rounded-md bg-[#031121] text-primary dark:bg-white dark:text-[#0057d8]">
            <ShieldCheck className="h-4 w-4" />
          </div>
          <SheetTitle className="text-lg font-semibold">Histórico e integridade</SheetTitle>
          <SheetDescription className="text-2xs leading-4">
            Original, alterações e arquivos preservados em cadeia append-only, sem retirar você da conversa.
          </SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 scrollbar-thin">
          {evidenceQuery.isPending ? (
            <div className="flex min-h-48 items-center justify-center gap-2 text-2xs text-muted-foreground">
              <LoaderCircle className="h-4 w-4 animate-spin" />Verificando a cadeia
            </div>
          ) : null}

          {evidenceQuery.isError ? (
            <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-center text-red-900 dark:border-red-900/70 dark:bg-red-950/30 dark:text-red-100">
              <TriangleAlert className="mx-auto h-5 w-5" />
              <p className="mt-2 text-2xs font-semibold">Não foi possível verificar o histórico</p>
              <p className="mt-1 text-2xs opacity-75">A mensagem continua preservada. Tente consultar novamente.</p>
              <Button variant="outline" size="sm" className="mt-3 h-8 text-2xs" onClick={() => void evidenceQuery.refetch()}>
                <RefreshCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente
              </Button>
            </div>
          ) : null}

          {chain ? (
            <>
              <section className={cn('rounded-lg border p-4', chain.integrityStatus === 'verified' ? 'border-emerald-300 bg-emerald-50/70 dark:border-emerald-900 dark:bg-emerald-950/25' : chain.integrityStatus === 'unavailable' ? 'border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30' : 'border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/30')} aria-label="Resultado da verificação">
                <div className="flex items-start gap-3">
                  {chain.integrityStatus === 'verified' ? <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-700 dark:text-emerald-300" /> : <TriangleAlert className={cn('mt-0.5 h-5 w-5 shrink-0', chain.integrityStatus === 'unavailable' ? 'text-amber-700 dark:text-amber-300' : 'text-red-700 dark:text-red-300')} />}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-xs font-semibold">{chain.integrityStatus === 'verified' ? 'Cadeia íntegra nesta verificação' : chain.integrityStatus === 'unavailable' ? 'Linha de base ainda não registrada' : 'Divergência detectada na cadeia'}</p>
                      <KlmPill tone={chain.contentLifecycleState === 'original' ? 'green' : 'gold'}>{lifecycleLabels[chain.contentLifecycleState]}</KlmPill>
                    </div>
                    <p className="mt-1 text-2xs leading-4 text-muted-foreground">{chain.eventCount} eventos · {chain.contentRevisionCount} versões de conteúdo</p>
                    <div className="mt-3 flex items-center gap-2 rounded-md border border-current/10 bg-card/70 px-2.5 py-2 text-2xs">
                      <Fingerprint className="h-3.5 w-3.5 shrink-0" />
                      <span className="min-w-0 truncate font-mono" title={chain.chainHeadSha256 ?? undefined}>{shortHash(chain.chainHeadSha256)}</span>
                    </div>
                  </div>
                </div>
              </section>

              {chain.sensitiveDataHidden ? (
                <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-2xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100">O conteúdo está oculto pela sua permissão. Metadados e hashes permanecem verificáveis.</p>
              ) : null}

              <section className="mt-5" aria-labelledby="evidence-timeline-title">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <h3 id="evidence-timeline-title" className="flex items-center gap-2 text-2xs font-semibold uppercase text-muted-foreground"><History className="h-3.5 w-3.5" />Linha do tempo</h3>
                  <span className="flex items-center gap-1 text-2xs text-muted-foreground"><Clock3 className="h-3 w-3" />ordem de recebimento</span>
                </div>
                <ol className="space-y-3 border-l border-border pl-3">
                  {chain.events.map((event) => <EvidenceEvent key={event.id} event={event} />)}
                </ol>
              </section>

              <section className="mt-5 rounded-lg border border-border bg-secondary p-3">
                <p className="text-2xs font-semibold">Limites da verificação</p>
                <ul className="mt-2 space-y-1.5 text-2xs leading-4 text-muted-foreground">
                  {chain.providerNotes.map((note) => <li key={note}>• {note}</li>)}
                </ul>
              </section>
            </>
          ) : null}
        </div>

        <div className="border-t border-border bg-card px-5 py-4">
          <Button
            className="h-9 w-full bg-[#031121] text-2xs text-white hover:bg-[#031121]"
            disabled={!chain || exportMutation.isPending || chain.integrityStatus !== 'verified'}
            onClick={() => exportMutation.mutate()}
          >
            {exportMutation.isPending ? <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Download className="mr-1.5 h-3.5 w-3.5" />}
            {exportMutation.isPending ? 'Gerando manifesto' : 'Exportar manifesto verificável'}
          </Button>
          {exportMutation.isError ? <p role="alert" className="mt-2 text-center text-2xs text-red-600">A exportação falhou. O histórico não foi alterado.</p> : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
