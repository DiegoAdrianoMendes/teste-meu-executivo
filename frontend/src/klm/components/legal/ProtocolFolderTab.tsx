import { useMemo, useState } from 'react';
import { CheckCircle2, FileArchive, Info, Loader2, ShieldAlert, XCircle } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import {
  downloadProtocolFolderZip,
  useBuildProtocolFolder,
  useDemandLegalArtifacts,
  useRegisterProtocolReceipt,
} from '@/klm/api/useProtocolFolder';
import { isLegalDemandAccessDeniedError } from '@/klm/api/useLegalDemands';
import type { KlmLegalArtifactVersion, KlmProtocolFolder, KlmProtocolFolderState } from '@/klm/api';
import { ApiError } from '@/services/http';
import { useAppStore } from '@/store/app-context';

/**
 * Aba Protocolo do detalhe de uma demanda (Sprint A UI, Onda 7). Consome
 * `app/api/v1/endpoints/protocol_folder.py` via `klmApi`/hooks
 * (`@/klm/api/useProtocolFolder`) — monta/revalida a pasta numerada a
 * partir da versão aprovada do artefato, mostra as 8 subpastas + o
 * manifesto de conformidade, baixa o `.zip` de entrega, e registra o
 * recibo de um protocolo que já aconteceu fora da plataforma.
 *
 * `PROTOCOLO É EXCLUSIVAMENTE HUMANO`: esta tela nunca oferece uma ação
 * de "protocolar" — a montagem só PREPARA e VALIDA; o advogado protocola
 * no tribunal e cola o comprovante aqui depois (`registrar recibo`).
 * Estado bloqueado/pendente mostra a lista de pendências explícita,
 * nunca um "pronto" falso.
 */

const ARTIFACT_KIND_LABELS: Record<KlmLegalArtifactVersion['kind'], string> = {
  petition: 'Petição',
  contract: 'Contrato',
  power_of_attorney: 'Procuração',
  proposal: 'Proposta',
  client_update: 'Atualização ao cliente',
};

const ARTIFACT_STATUS_LABELS: Record<KlmLegalArtifactVersion['status'], string> = {
  draft: 'Rascunho',
  review_pending: 'Em revisão',
  approved: 'Aprovado',
  rejected: 'Rejeitado',
  changes_requested: 'Alterações solicitadas',
  archived: 'Arquivado',
};

const FOLDER_STATE_LABELS: Record<KlmProtocolFolderState, string> = {
  bloqueado: 'Bloqueado',
  pendente: 'Pendente',
  protocolavel: 'Protocolável',
};

const FOLDER_STATE_TONE: Record<KlmProtocolFolderState, 'red' | 'gold' | 'green'> = {
  bloqueado: 'red',
  pendente: 'gold',
  protocolavel: 'green',
};

const SUBFOLDERS: Array<{ id: string; label: string }> = [
  { id: '00_MANIFESTO', label: 'Manifesto' },
  { id: '01_PETICAO', label: 'Peça' },
  { id: '02_PROCURACAO', label: 'Procuração' },
  { id: '03_DOCS_PESSOAIS', label: 'Docs. pessoais' },
  { id: '04_PROVAS', label: 'Provas' },
  { id: '05_DOCS_PROCESSUAIS', label: 'Docs. processuais' },
  { id: '06_CUSTAS', label: 'Custas' },
  { id: '99_ORIGINAIS', label: 'Originais' },
];

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { message?: string } | undefined;
    return detail?.message ?? error.message;
  }
  return error instanceof Error ? error.message : 'Tente novamente.';
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  return new Date(value).toLocaleString('pt-BR');
}

function pickDefaultArtifact(artifacts: KlmLegalArtifactVersion[]): string | undefined {
  const approved = artifacts.filter((item) => item.status === 'approved');
  const pool = approved.length > 0 ? approved : artifacts;
  const sorted = [...pool].sort((a, b) => b.version - a.version);
  return sorted[0]?.id;
}

function ManifestFactRow({ label, ok, detail }: { label: string; ok: boolean | null; detail?: string }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5 text-2xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="flex items-center gap-1.5 text-right font-medium">
        {detail && <span className="text-muted-foreground">{detail}</span>}
        {ok === true && <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />}
        {ok === false && <XCircle className="h-3.5 w-3.5 shrink-0 text-red-600 dark:text-red-400" />}
      </span>
    </div>
  );
}

export function LegalProtocolTab({ demandId }: { demandId: string }) {
  const { toast } = useToast();
  const { can } = useAppStore();
  const canManage = can('legal', 'manage');

  const artifactsQuery = useDemandLegalArtifacts(demandId);
  const buildFolder = useBuildProtocolFolder(demandId);

  const [selectedArtifactId, setSelectedArtifactId] = useState<string>('');
  const [tribunal, setTribunal] = useState('');
  const [signatureConfirmed, setSignatureConfirmed] = useState(false);
  const [folder, setFolder] = useState<KlmProtocolFolder | null>(null);
  const [isDownloading, setIsDownloading] = useState(false);

  const [receiptReference, setReceiptReference] = useState('');
  const [receiptNote, setReceiptNote] = useState('');
  const registerReceipt = useRegisterProtocolReceipt(demandId, folder?.id);

  const artifacts = useMemo(() => artifactsQuery.data ?? [], [artifactsQuery.data]);
  const effectiveArtifactId = selectedArtifactId || pickDefaultArtifact(artifacts) || '';

  // Todos os hooks (inclusive este) ficam ANTES do retorno antecipado de
  // acesso negado abaixo — nunca depois — para nunca violar as Rules of
  // Hooks pulando um `useMemo`/`useState` num dos caminhos de render.
  const filesByFolder = useMemo(() => {
    const map = new Map<string, KlmProtocolFolder['files']>();
    if (!folder) return map;
    for (const file of folder.files) {
      const list = map.get(file.folder) ?? [];
      list.push(file);
      map.set(file.folder, list);
    }
    return map;
  }, [folder]);

  if (isLegalDemandAccessDeniedError(artifactsQuery.error)) {
    return (
      <KlmPanel>
        <div className="flex items-start gap-3 p-4">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary">
            <ShieldAlert className="h-4 w-4 text-muted-foreground" />
          </span>
          <div>
            <p className="text-sm font-semibold">Acesso ao módulo jurídico restrito</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              Seu papel atual não tem a permissão jurídica (módulo <code>legal</code>) necessária para ver a pasta de protocolo.
            </p>
          </div>
        </div>
      </KlmPanel>
    );
  }

  const handleBuild = () => {
    if (!effectiveArtifactId) return;
    buildFolder.mutate(
      {
        artifactId: effectiveArtifactId,
        tribunal: tribunal.trim() || null,
        signatureConfirmed,
      },
      {
        onSuccess: (result) => {
          setFolder(result);
          toast({
            title: `Pasta ${FOLDER_STATE_LABELS[result.state].toLowerCase()}`,
            description:
              result.state === 'protocolavel'
                ? 'Todas as validações passaram — a pasta está pronta para download.'
                : `${result.manifest.pendencias.length} pendência(s) — veja a lista abaixo antes de protocolar.`,
          });
        },
        onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível montar a pasta', description: errorMessage(error) }),
      },
    );
  };

  const handleDownload = async () => {
    if (!folder) return;
    setIsDownloading(true);
    try {
      await downloadProtocolFolderZip(demandId, folder.id, `pasta-protocolo-${folder.id}.zip`);
    } catch (error) {
      toast({ variant: 'destructive', title: 'Não foi possível baixar o .zip', description: errorMessage(error) });
    } finally {
      setIsDownloading(false);
    }
  };

  const handleRegisterReceipt = (event: React.FormEvent) => {
    event.preventDefault();
    if (!folder || !receiptReference.trim()) return;
    registerReceipt.mutate(
      { reference: receiptReference.trim(), note: receiptNote.trim() || null },
      {
        onSuccess: (result) => {
          setFolder(result);
          setReceiptReference('');
          setReceiptNote('');
          toast({ title: 'Recibo registrado', description: 'O protocolo real (feito no tribunal) ficou registrado nesta demanda.' });
        },
        onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível registrar o recibo', description: errorMessage(error) }),
      },
    );
  };

  return (
    <div className="space-y-4">
      <KlmPanel className="p-4">
        <KlmSectionTitle
          title="Pasta de protocolo"
          description="Monta e valida a pasta numerada (8 subpastas + manifesto) a partir da versão aprovada da peça — o protocolo em si é sempre feito por um humano."
        />

        <Alert className="mt-3">
          <Info className="h-4 w-4" />
          <AlertTitle className="text-xs">A plataforma não protocola</AlertTitle>
          <AlertDescription className="text-2xs leading-5">
            Esta tela prepara e valida a pasta (estrutura, manifesto, hash de cada arquivo) e gera o <code>.zip</code> de entrega.
            O protocolo nos autos é feito pelo advogado, diretamente no tribunal — depois, cole o comprovante em
            &quot;Registrar recibo&quot; abaixo.
          </AlertDescription>
        </Alert>

        {artifactsQuery.isPending && <Skeleton className="mt-3 h-24 rounded-lg" />}

        {artifactsQuery.isSuccess && artifacts.length === 0 && (
          <p className="mt-3 text-xs text-muted-foreground">
            Nenhuma peça (versão jurídica) encontrada nesta demanda ainda — gere uma na aba Peças antes de montar a pasta de protocolo.
          </p>
        )}

        {artifactsQuery.isSuccess && artifacts.length > 0 && (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label className="text-2xs text-muted-foreground">Versão jurídica (peça)</Label>
              <Select value={effectiveArtifactId} onValueChange={setSelectedArtifactId}>
                <SelectTrigger className="h-9 text-xs">
                  <SelectValue placeholder="Selecione a versão" />
                </SelectTrigger>
                <SelectContent>
                  {artifacts.map((artifact) => (
                    <SelectItem key={artifact.id} value={artifact.id} className="text-xs">
                      v{artifact.version} · {ARTIFACT_KIND_LABELS[artifact.kind]} · {ARTIFACT_STATUS_LABELS[artifact.status]}
                      {artifact.title ? ` — ${artifact.title}` : ''}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-2xs text-muted-foreground">Tribunal (opcional — usa o do processo se vazio)</Label>
              <Input
                value={tribunal}
                onChange={(event) => setTribunal(event.target.value)}
                placeholder="Ex.: TJDFT — 1ª Vara Cível"
                className="h-9 text-xs"
              />
            </div>
            <div className="flex items-center gap-2 sm:col-span-2">
              <Checkbox
                id="protocol-signature-confirmed"
                checked={signatureConfirmed}
                onCheckedChange={(checked) => setSignatureConfirmed(checked === true)}
              />
              <Label htmlFor="protocol-signature-confirmed" className="text-2xs font-normal text-muted-foreground">
                Confirmo, como humano responsável, que a peça está assinada (a plataforma não verifica assinatura digital).
              </Label>
            </div>
            <div className="sm:col-span-2">
              <Button
                size="sm"
                disabled={!canManage || !effectiveArtifactId || buildFolder.isPending}
                title={!canManage ? 'Permissão legal.manage necessária' : undefined}
                onClick={handleBuild}
              >
                {buildFolder.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                {folder ? 'Revalidar pasta' : 'Montar pasta de protocolo'}
              </Button>
            </div>
          </div>
        )}
      </KlmPanel>

      {folder && (
        <KlmPanel className="p-4">
          <KlmSectionTitle
            title="Manifesto"
            description={`Gerado em ${formatDateTime(folder.manifest.geradoEm)}`}
            action={<KlmPill tone={FOLDER_STATE_TONE[folder.state]}>{FOLDER_STATE_LABELS[folder.state]}</KlmPill>}
          />

          {folder.manifest.pendencias.length > 0 && (
            <Alert variant={folder.state === 'bloqueado' ? 'destructive' : 'default'} className="mt-3">
              <ShieldAlert className="h-4 w-4" />
              <AlertTitle className="text-xs">
                {folder.manifest.pendencias.length} pendência(s) — pasta ainda não está pronta
              </AlertTitle>
              <AlertDescription>
                <ul className="mt-1 list-disc space-y-1 pl-4 text-2xs leading-5">
                  {folder.manifest.pendencias.map((pending, index) => (
                    <li key={`${pending.item}-${index}`}>
                      <span className="font-medium">{pending.item}:</span> {pending.detail}
                    </li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}

          <div className="mt-4 grid gap-x-6 gap-y-0 sm:grid-cols-2">
            <ManifestFactRow label="Versão liberada" ok={folder.manifest.versaoLiberada.status === 'approved'} detail={`v${folder.manifest.versaoLiberada.version} · ${folder.manifest.versaoLiberada.status}`} />
            <ManifestFactRow label="Assinatura confirmada" ok={folder.manifest.assinaturaConfirmada} />
            <ManifestFactRow
              label="Procuração"
              ok={folder.manifest.procuracao.dispensada || folder.manifest.procuracao.presente}
              detail={folder.manifest.procuracao.dispensada ? 'dispensada' : undefined}
            />
            <ManifestFactRow
              label="Custas"
              ok={!folder.manifest.custas.exigida || folder.manifest.custas.pagaNoFinanceiro === true}
              detail={folder.manifest.custas.exigida ? undefined : 'não exigida'}
            />
            <ManifestFactRow
              label="Citações verificadas"
              ok={folder.manifest.citacoes.naoVerificadas.length === 0}
              detail={`${folder.manifest.citacoes.total - folder.manifest.citacoes.naoVerificadas.length}/${folder.manifest.citacoes.total}`}
            />
            <ManifestFactRow label="Achados críticos abertos" ok={folder.manifest.achadosCriticosAbertos === 0} detail={String(folder.manifest.achadosCriticosAbertos)} />
            <ManifestFactRow label="Placeholder pendente" ok={!folder.manifest.placeholders.presente} />
            <ManifestFactRow label="Responsável vinculado" ok={folder.manifest.responsavel !== null} detail={folder.manifest.responsavel?.nome} />
            <ManifestFactRow label="Tribunal" ok={Boolean(folder.manifest.tribunal)} detail={folder.manifest.tribunal || 'não informado'} />
            <ManifestFactRow label="Tamanho total" ok={null} detail={formatBytes(folder.manifest.tamanhoTotalBytes)} />
          </div>

          <div className="mt-4">
            <Label className="text-2xs text-muted-foreground">Estrutura da pasta (8 subpastas)</Label>
            <div className="mt-1.5 grid gap-1.5 sm:grid-cols-2">
              {SUBFOLDERS.map((subfolder) => {
                const files = subfolder.id === '00_MANIFESTO' ? undefined : filesByFolder.get(subfolder.id);
                const count = subfolder.id === '00_MANIFESTO' ? 1 : (files?.length ?? 0);
                return (
                  <div key={subfolder.id} className="rounded-lg border border-border bg-secondary/40 px-3 py-2 text-2xs">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-muted-foreground">{subfolder.id}</span>
                      <KlmPill tone={count > 0 ? 'blue' : 'muted'}>{count} arquivo{count === 1 ? '' : 's'}</KlmPill>
                    </div>
                    <p className="mt-0.5 font-medium">{subfolder.label}</p>
                    {files && files.length > 0 && (
                      <ul className="mt-1 space-y-0.5 text-muted-foreground">
                        {files.map((file) => (
                          <li key={`${file.sourceType}-${file.sourceId}`} className="truncate">
                            {String(file.order).padStart(2, '0')}_{file.filename} · {formatBytes(file.sizeBytes)}
                          </li>
                        ))}
                      </ul>
                    )}
                    {subfolder.id === '00_MANIFESTO' && (
                      <p className="mt-1 text-muted-foreground">manifesto.json (gerado automaticamente no .zip)</p>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="mt-4">
            <Button size="sm" variant="outline" disabled={isDownloading} onClick={handleDownload}>
              {isDownloading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <FileArchive className="mr-1.5 h-3.5 w-3.5" />}
              Baixar .zip (export.zip)
            </Button>
          </div>
        </KlmPanel>
      )}

      {folder && (
        <KlmPanel className="p-4">
          <KlmSectionTitle
            title="Recibo do protocolo"
            description="Registro POSTERIOR e humano — cole aqui o número/comprovante depois de protocolar no tribunal. Nunca é uma ação automática desta plataforma."
          />

          {folder.receiptRegisteredAt ? (
            <div className="mt-3 space-y-1 rounded-lg border border-emerald-600/20 bg-emerald-500/[0.08] p-3 text-2xs">
              <p className="flex items-center gap-1.5 font-medium text-[#0b7435] dark:text-emerald-400">
                <CheckCircle2 className="h-3.5 w-3.5" />Protocolado
              </p>
              <p><span className="text-muted-foreground">Referência:</span> {folder.receiptReference}</p>
              <p><span className="text-muted-foreground">Data do registro:</span> {formatDateTime(folder.receiptRegisteredAt)}</p>
              {folder.receiptNote && <p><span className="text-muted-foreground">Nota:</span> {folder.receiptNote}</p>}
            </div>
          ) : (
            <form className="mt-3 grid gap-3 sm:grid-cols-2" onSubmit={handleRegisterReceipt}>
              <div className="space-y-1.5">
                <Label className="text-2xs text-muted-foreground">Número/protocolo do comprovante *</Label>
                <Input
                  value={receiptReference}
                  onChange={(event) => setReceiptReference(event.target.value)}
                  placeholder="Ex.: 2026.0001234-5"
                  className="h-9 text-xs"
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-2xs text-muted-foreground">Observação (opcional)</Label>
                <Textarea
                  value={receiptNote}
                  onChange={(event) => setReceiptNote(event.target.value)}
                  className="min-h-9 text-xs"
                  rows={1}
                />
              </div>
              <div className="sm:col-span-2">
                <Button
                  type="submit"
                  size="sm"
                  disabled={!canManage || !receiptReference.trim() || registerReceipt.isPending}
                  title={!canManage ? 'Permissão legal.manage necessária' : undefined}
                >
                  {registerReceipt.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                  Registrar recibo
                </Button>
              </div>
            </form>
          )}
        </KlmPanel>
      )}
    </div>
  );
}
