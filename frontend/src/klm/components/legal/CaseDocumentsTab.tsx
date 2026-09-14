import { useRef, useState } from 'react';
import {
  Clock,
  Download,
  FileText,
  Link2,
  Loader2,
  Radar,
  ShieldAlert,
  ShieldOff,
  Upload,
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
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { useToast } from '@/hooks/use-toast';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import {
  useCaseQuarantinedLegalDocuments,
  useCreateLegalDocumentShareLink,
  useLegalDocumentShareDownloads,
  useLegalDocuments,
  useRevokeLegalDocumentShareLink,
  useUploadLegalDocument,
} from '@/klm/api/useLegalDocuments';
import { isLegalDemandAccessDeniedError, useLegalDemand } from '@/klm/api/useLegalDemands';
import type {
  KlmLegalDocumentAsset,
  KlmLegalDocumentAvStatus,
  KlmLegalDocumentClassificationMethod,
  KlmLegalDocumentOcrStatus,
  KlmLegalDocumentShareLinkIssued,
  KlmLegalDocumentType,
} from '@/klm/api';
import { ApiError } from '@/services/http';

/**
 * Aba Documentos do detalhe de uma demanda (Sprint A UI, Onda 7). Consome o
 * DMS jurídico (`app/api/v1/endpoints/legal_documents.py`) via
 * `klmApi`/hooks (`@/klm/api/useLegalDocuments`): upload múltiplo/arrastar,
 * listagem com tipo/OCR/quarentena honestos, compartilhamento com link de
 * expiração obrigatória + trilha de download, e a fila de quarentena deste
 * caso isolada.
 *
 * Escopo: o backend guarda o documento por `matter_id` (o `Matter`/Kanban
 * vinculado à demanda), não por `legal_demand_id`. Uma demanda sem
 * `matterId` ainda não tem DMS — a aba mostra esse estado honestamente em
 * vez de listar documentos de outro caso ou fingir upload disponível.
 */

const DOCUMENT_TYPE_LABELS: Record<KlmLegalDocumentType, string> = {
  peticao_inicial: 'Petição inicial',
  procuracao: 'Procuração',
  contrato: 'Contrato',
  comprovante: 'Comprovante',
  sentenca: 'Sentença',
  recurso: 'Recurso',
  correspondencia: 'Correspondência',
  outro: 'Outro',
  nao_classificado: 'Não classificado',
};

const OCR_STATUS_LABELS: Record<KlmLegalDocumentOcrStatus, string> = {
  nao_aplicavel: 'OCR não aplicável',
  ocr_pendente: 'OCR pendente',
  concluido: 'OCR concluído',
  indisponivel: 'OCR indisponível',
};

const CLASSIFICATION_METHOD_LABELS: Record<KlmLegalDocumentClassificationMethod, string> = {
  ia: 'Classificado por IA',
  manual: 'Classificado manualmente',
  indisponivel: 'Sem classificador disponível',
};

const AV_STATUS_LABELS: Record<KlmLegalDocumentAvStatus, string> = {
  pending: 'Antivírus pendente',
  simulated_unverified: 'Antivírus simulado (não verificado)',
  simulated_blocked: 'Antivírus simulado (bloqueado)',
  clean: 'Limpo',
  infected: 'Infectado',
  error: 'Erro no antivírus',
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { message?: string } | undefined;
    return detail?.message ?? error.message;
  }
  return error instanceof Error ? error.message : 'Tente novamente.';
}

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  return new Date(value).toLocaleString('pt-BR');
}

/** Padrão exigido por `<input type="datetime-local">`, no fuso local. */
function minDateTimeLocal(): string {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16);
}

function storageBadge(asset: KlmLegalDocumentAsset): { label: string; tone: 'green' | 'gold' | 'red' | 'muted' } {
  if (!asset.storage) return { label: 'Sem versão física', tone: 'muted' };
  if (asset.storage.state === 'quarantined') return { label: 'Em quarentena', tone: 'red' };
  if (asset.storage.state === 'pending') return { label: 'Upload pendente', tone: 'gold' };
  return { label: 'Disponível', tone: 'green' };
}

export function LegalDocumentsTab({ demandId }: { demandId: string }) {
  const { toast } = useToast();
  const demandQuery = useLegalDemand(demandId);

  if (demandQuery.isPending) {
    return <div className="grid gap-3">{Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-16 rounded-lg" />)}</div>;
  }
  if (isLegalDemandAccessDeniedError(demandQuery.error)) return null; // já mostrado no cabeçalho da página
  if (demandQuery.isError || !demandQuery.data) {
    return (
      <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
        Não foi possível carregar a demanda para montar os documentos agora. Tente novamente em instantes.
      </p>
    );
  }

  const matterId = demandQuery.data.matterId;
  const clientId = demandQuery.data.clientId;

  if (!matterId) {
    return (
      <KlmPanel>
        <div className="flex items-start gap-3 p-4">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary">
            <FileText className="h-4 w-4 text-muted-foreground" />
          </span>
          <div>
            <p className="text-sm font-semibold">Documentos ainda não disponíveis para esta demanda</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              O DMS jurídico organiza documentos pelo caso operacional (Kanban) vinculado à demanda, e esta
              demanda ainda não tem um caso vinculado (<code>matterId</code> vazio). Vincule a demanda a um
              card do funil para habilitar o upload e a listagem de documentos aqui — nunca mostramos
              documentos de outro caso nesta aba.
            </p>
          </div>
        </div>
      </KlmPanel>
    );
  }

  return <CaseDocumentsContent matterId={matterId} clientId={clientId} toast={toast} />;
}

function CaseDocumentsContent({
  matterId,
  clientId,
  toast,
}: {
  matterId: string;
  clientId: string | null;
  toast: ReturnType<typeof useToast>['toast'];
}) {
  const documentsQuery = useLegalDocuments(matterId);
  const quarantineQuery = useCaseQuarantinedLegalDocuments(matterId);
  const uploadMutation = useUploadLegalDocument(matterId, clientId);
  const [dragOver, setDragOver] = useState(false);
  const [shareTarget, setShareTarget] = useState<KlmLegalDocumentAsset | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const uploadFiles = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;
    let ok = 0;
    let deduplicated = 0;
    let failed = 0;
    for (const file of list) {
      try {
        // eslint-disable-next-line no-await-in-loop -- upload sequencial: cada arquivo depende do dedup por hash já persistido
        const result = await uploadMutation.mutateAsync(file);
        if (result.deduplicated) deduplicated += 1;
        else ok += 1;
      } catch {
        failed += 1;
      }
    }
    if (ok > 0 || deduplicated > 0) {
      toast({
        title: `${ok + deduplicated} documento(s) processado(s)`,
        description: deduplicated > 0 ? `${deduplicated} já existia(m) por conteúdo idêntico — sem bytes duplicados.` : undefined,
      });
    }
    if (failed > 0) {
      toast({ variant: 'destructive', title: `${failed} arquivo(s) não puderam ser enviados`, description: 'Tente novamente em instantes.' });
    }
  };

  const documents = documentsQuery.data ?? [];
  const quarantined = quarantineQuery.data ?? [];

  return (
    <div className="space-y-4">
      <KlmPanel className="p-4">
        <KlmSectionTitle
          title="Documentos do caso"
          description="Upload deduplicado por hash de conteúdo — o mesmo arquivo enviado duas vezes vira uma nova referência, nunca bytes duplicados."
          action={
            <Button size="sm" disabled={uploadMutation.isPending} onClick={() => fileInputRef.current?.click()}>
              {uploadMutation.isPending ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Upload className="mr-1.5 h-4 w-4" />}
              Enviar documentos
            </Button>
          }
        />

        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => {
            const { files } = event.target;
            event.target.value = '';
            if (files && files.length > 0) void uploadFiles(files);
          }}
        />

        <div
          className={`mt-3 flex flex-col items-center justify-center gap-1.5 rounded-lg border-2 border-dashed p-6 text-center text-xs transition-colors ${dragOver ? 'border-primary bg-primary/5' : 'border-border bg-secondary/30'}`}
          onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragOver(false);
            if (event.dataTransfer.files.length > 0) void uploadFiles(event.dataTransfer.files);
          }}
        >
          <Upload className="h-5 w-5 text-muted-foreground" />
          <p className="text-muted-foreground">Arraste arquivos aqui ou use "Enviar documentos" — múltiplos arquivos de uma vez.</p>
        </div>

        {documentsQuery.isPending && <div className="mt-3 grid gap-2">{Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-14 rounded-lg" />)}</div>}

        {documentsQuery.isError && (
          <p className="mt-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
            Não foi possível carregar os documentos agora. Tente novamente em instantes.
          </p>
        )}

        {documentsQuery.isSuccess && documents.length === 0 && (
          <p className="mt-3 border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
            Nenhum documento enviado para este caso ainda.
          </p>
        )}

        {documentsQuery.isSuccess && documents.length > 0 && (
          <div className="mt-3 divide-y divide-border rounded-lg border border-border">
            {documents.map((asset) => {
              const badge = storageBadge(asset);
              const isQuarantined = asset.storage?.state === 'quarantined';
              return (
                <div key={asset.id} className="flex flex-wrap items-center justify-between gap-3 p-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{asset.originalFilename}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground">
                      <KlmPill tone="muted">{DOCUMENT_TYPE_LABELS[asset.documentType]}</KlmPill>
                      <KlmPill tone={asset.ocrStatus === 'concluido' ? 'green' : 'muted'}>{OCR_STATUS_LABELS[asset.ocrStatus]}</KlmPill>
                      <span>{CLASSIFICATION_METHOD_LABELS[asset.classificationMethod]}</span>
                      <span>· {formatBytes(asset.storage?.byteSize)}</span>
                      <span>· {formatDateTime(asset.createdAt)}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <KlmPill tone={badge.tone}>{badge.label}</KlmPill>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-2xs"
                      disabled={isQuarantined}
                      title={isQuarantined ? 'Documento em quarentena não pode ser compartilhado' : undefined}
                      onClick={() => setShareTarget(asset)}
                    >
                      <Link2 className="mr-1.5 h-3 w-3" />Compartilhar
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </KlmPanel>

      <KlmPanel className={`p-4 ${quarantined.length > 0 ? 'border-destructive/30 bg-destructive/5' : ''}`}>
        <KlmSectionTitle
          title="Quarentena deste caso"
          description="Arquivo que o antivírus isolou nunca é classificado ou tem OCR tentado até revisão humana — nunca aparece junto dos documentos disponíveis."
        />
        {quarantineQuery.isPending && <Skeleton className="mt-3 h-12 rounded-lg" />}
        {quarantineQuery.isSuccess && quarantined.length === 0 && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground"><ShieldOff className="h-3.5 w-3.5" />Nenhum documento em quarentena neste caso.</p>
        )}
        {quarantined.length > 0 && (
          <div className="mt-3 space-y-2">
            {quarantined.map((asset) => (
              <div key={asset.id} className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-card px-3 py-2 text-xs">
                <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
                <div className="min-w-0">
                  <p className="truncate font-medium">{asset.originalFilename}</p>
                  <p className="mt-0.5 text-muted-foreground">
                    {asset.storage ? AV_STATUS_LABELS[asset.storage.avStatus] : 'Antivírus pendente'}
                    {asset.storage?.quarantineReason ? ` · ${asset.storage.quarantineReason}` : ''}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </KlmPanel>

      <ShareDocumentDialog document={shareTarget} onClose={() => setShareTarget(null)} toast={toast} />
    </div>
  );
}

function ShareDocumentDialog({
  document: targetDocument,
  onClose,
  toast,
}: {
  document: KlmLegalDocumentAsset | null;
  onClose: () => void;
  toast: ReturnType<typeof useToast>['toast'];
}) {
  const createShare = useCreateLegalDocumentShareLink();
  const revokeShare = useRevokeLegalDocumentShareLink();
  const [expiresAt, setExpiresAt] = useState('');
  const [watermarkEnabled, setWatermarkEnabled] = useState(true);
  const [maxDownloads, setMaxDownloads] = useState('');
  const [issued, setIssued] = useState<KlmLegalDocumentShareLinkIssued | null>(null);
  const downloadsQuery = useLegalDocumentShareDownloads(issued?.id ?? null);

  const reset = () => {
    setExpiresAt('');
    setWatermarkEnabled(true);
    setMaxDownloads('');
    setIssued(null);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!targetDocument || !expiresAt) return;
    createShare.mutate(
      {
        assetId: targetDocument.id,
        payload: {
          expiresAt: new Date(expiresAt).toISOString(),
          watermarkEnabled,
          maxDownloads: maxDownloads.trim() ? Number(maxDownloads) : null,
        },
      },
      {
        onSuccess: (result) => {
          setIssued(result);
          toast({ title: 'Link de compartilhamento criado', description: `Expira em ${formatDateTime(result.expiresAt)}.` });
        },
        onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível criar o link', description: errorMessage(error) }),
      },
    );
  };

  const handleRevoke = () => {
    if (!issued) return;
    revokeShare.mutate(issued.id, {
      onSuccess: () => { toast({ title: 'Link revogado' }); handleClose(); },
      onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível revogar', description: errorMessage(error) }),
    });
  };

  return (
    <Dialog open={Boolean(targetDocument)} onOpenChange={(open) => { if (!open) handleClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Compartilhar documento</DialogTitle>
          <DialogDescription>{targetDocument?.originalFilename}</DialogDescription>
        </DialogHeader>

        {!issued ? (
          <form className="space-y-3" onSubmit={handleSubmit}>
            <div className="space-y-1">
              <Label htmlFor="share-expira">Expira em</Label>
              <Input
                id="share-expira"
                type="datetime-local"
                required
                min={minDateTimeLocal()}
                value={expiresAt}
                onChange={(event) => setExpiresAt(event.target.value)}
              />
            </div>
            <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <Label htmlFor="share-marca-dagua" className="text-xs font-normal">Marca d'água na redenção</Label>
              <Switch id="share-marca-dagua" checked={watermarkEnabled} onCheckedChange={setWatermarkEnabled} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="share-max-downloads">Limite de downloads (opcional)</Label>
              <Input
                id="share-max-downloads"
                type="number"
                min={1}
                placeholder="Sem limite"
                value={maxDownloads}
                onChange={(event) => setMaxDownloads(event.target.value)}
              />
            </div>
            <p className="flex items-start gap-1.5 rounded-lg border border-border bg-secondary/40 px-3 py-2 text-2xs text-muted-foreground">
              <Radar className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              O resgate deste link registra data, usuário e IP na trilha de download — não existe hoje um
              link público anônimo; quem resgatar precisa de acesso jurídico autenticado neste workspace.
            </p>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>Cancelar</Button>
              <Button type="submit" disabled={createShare.isPending || !expiresAt}>
                {createShare.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Gerar link
              </Button>
            </DialogFooter>
          </form>
        ) : (
          <div className="space-y-3">
            <div className="space-y-1 rounded-lg border border-border bg-secondary/40 p-3">
              <p className="text-2xs font-semibold uppercase text-muted-foreground">Token (mostrado uma única vez)</p>
              <p className="break-all font-mono text-xs">{issued.token}</p>
              <p className="mt-1 text-2xs text-muted-foreground">Expira em {formatDateTime(issued.expiresAt)} · {issued.watermarkEnabled ? 'com' : 'sem'} marca d'água{issued.maxDownloads ? ` · limite ${issued.maxDownloads} download(s)` : ''}</p>
            </div>

            <div>
              <p className="mb-1.5 flex items-center gap-1.5 text-2xs font-semibold uppercase text-muted-foreground"><Clock className="h-3 w-3" />Trilha de download</p>
              {downloadsQuery.isPending && <Skeleton className="h-8 rounded-lg" />}
              {downloadsQuery.isSuccess && downloadsQuery.data.length === 0 && (
                <p className="text-2xs text-muted-foreground">Ainda não resgatado.</p>
              )}
              {downloadsQuery.isSuccess && downloadsQuery.data.length > 0 && (
                <div className="space-y-1.5">
                  {downloadsQuery.data.map((entry) => (
                    <div key={entry.id} className="flex items-center gap-1.5 rounded-lg border border-border px-2 py-1.5 text-2xs">
                      <Download className="h-3 w-3 text-muted-foreground" />
                      {formatDateTime(entry.downloadedAt)}{entry.downloaderIp ? ` · ${entry.downloaderIp}` : ''}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}><X className="mr-1.5 h-3.5 w-3.5" />Fechar</Button>
              <Button type="button" variant="destructive" disabled={revokeShare.isPending} onClick={handleRevoke}>
                {revokeShare.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Revogar link
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
