import { useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  FileDown,
  FileUp,
  Loader2,
  Plus,
  RefreshCw,
  Send,
  ShieldAlert,
  ShieldCheck,
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/lib/utils';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import {
  isLegalDemandAccessDeniedError,
} from '@/klm/api/useLegalDemands';
import {
  useGenerateLegalArtifact,
  useGenerateLegalArtifactSection,
  useLegalArtifactChecklist,
  useLegalArtifactVersions,
  useReimportLegalArtifactDocx,
  useReviewLegalArtifact,
  useSubmitLegalArtifact,
  useVerifyLegalArtifactCitations,
} from '@/klm/api/useLegalArtifacts';
import {
  klmApi,
  LEGAL_ARTIFACT_GENERABLE_KINDS,
  type KlmLegalArtifactEditorSection,
  type KlmLegalArtifactEditorVersion,
  type KlmLegalArtifactKind,
} from '@/klm/api';
import {
  CITATION_STATUS_LABELS,
  CITATION_STATUS_TONE,
  LEGAL_ARTIFACT_KIND_LABELS,
  LEGAL_ARTIFACT_PROTOCOLABILITY_LABELS,
  LEGAL_ARTIFACT_PROTOCOLABILITY_TONE,
  LEGAL_ARTIFACT_STATUS_LABELS,
  LEGAL_REVIEW_SEVERITY_LABELS,
  LEGAL_REVIEW_SEVERITY_TONE,
} from '@/klm/lib/legal-artifact-labels';

/**
 * Aba "Peças" do detalhe de uma demanda (Sprint A UI) — editor jurídico já
 * existente no backend (Onda 6, `app/api/v1/endpoints/legal_editor.py` +
 * `legal_demands.py`): lista as versões (`LegalArtifactVersion`) da
 * demanda, gera artefato/seção a partir do MODELO TIMBRADO do escritório,
 * mostra o checklist de protocolabilidade, confere citações e roda a
 * revisão adversarial, exporta/reimporta `.docx`. Nunca finge um estado que
 * o backend não confirmou — sem modelo timbrado publicado, o erro do
 * backend aparece verbatim; sem citação verificada, o veredito nunca vira
 * "verificada" sozinho.
 */

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function sortedByRecency(versions: KlmLegalArtifactEditorVersion[]): KlmLegalArtifactEditorVersion[] {
  return [...versions].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

function artifactSections(artifact: KlmLegalArtifactEditorVersion): KlmLegalArtifactEditorSection[] {
  const raw = artifact.content?.sections;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (item): item is KlmLegalArtifactEditorSection =>
      Boolean(item) && typeof item === 'object' && typeof (item as { id?: unknown }).id === 'string',
  );
}

function GenerateArtifactDialog({
  demandId,
  open,
  onOpenChange,
  onGenerated,
}: {
  demandId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onGenerated: (artifact: KlmLegalArtifactEditorVersion) => void;
}) {
  const { toast } = useToast();
  const [kind, setKind] = useState<KlmLegalArtifactKind>('petition');
  const [title, setTitle] = useState('');
  const [officeTemplateKey, setOfficeTemplateKey] = useState('');
  const generateMutation = useGenerateLegalArtifact(demandId);

  const reset = () => {
    setKind('petition');
    setTitle('');
    setOfficeTemplateKey('');
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (generateMutation.isPending) return;
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Gerar peça a partir do modelo timbrado</DialogTitle>
          <DialogDescription>
            Preenche o modelo timbrado publicado no playbook do escritório com as variáveis do Caso 360 atual. Sem
            modelo publicado para o tipo, a geração para com o erro exato do backend — nunca cai num modelo genérico.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div>
            <Label className="mb-1.5 block text-xs font-medium text-foreground">Tipo de peça</Label>
            <Select value={kind} onValueChange={(value) => setKind(value as KlmLegalArtifactKind)}>
              <SelectTrigger className="h-9 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LEGAL_ARTIFACT_GENERABLE_KINDS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {LEGAL_ARTIFACT_KIND_LABELS[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="mb-1.5 block text-xs font-medium text-foreground">Título</Label>
            <Input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Ex.: Petição inicial — Fulano x Beltrano"
              className="h-9 text-sm"
            />
          </div>
          <div>
            <Label className="mb-1.5 block text-xs font-medium text-foreground">
              Chave do modelo timbrado (opcional)
            </Label>
            <Input
              value={officeTemplateKey}
              onChange={(event) => setOfficeTemplateKey(event.target.value)}
              placeholder="Deixe em branco para usar o modelo padrão do tipo"
              className="h-9 text-sm"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={generateMutation.isPending} onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            disabled={title.trim().length < 3 || generateMutation.isPending}
            onClick={() => {
              generateMutation.mutate(
                {
                  legalDemandId: demandId,
                  kind,
                  title: title.trim(),
                  officeTemplateKey: officeTemplateKey.trim() || undefined,
                },
                {
                  onSuccess: (artifact) => {
                    toast({ title: 'Peça gerada.', description: `Versão ${artifact.version} criada a partir do modelo timbrado.` });
                    onGenerated(artifact);
                    onOpenChange(false);
                    reset();
                  },
                  onError: (error) => {
                    toast({
                      variant: 'destructive',
                      title: 'Não foi possível gerar a peça',
                      description: errorMessage(error, 'Tente novamente.'),
                    });
                  },
                },
              );
            }}
          >
            {generateMutation.isPending ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <Plus className="mr-2 h-3.5 w-3.5" />}
            Gerar peça
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ReimportDocxDialog({
  demandId,
  artifact,
  open,
  onOpenChange,
  onReimported,
}: {
  demandId: string;
  artifact: KlmLegalArtifactEditorVersion;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onReimported: (artifact: KlmLegalArtifactEditorVersion) => void;
}) {
  const { toast } = useToast();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const reimportMutation = useReimportLegalArtifactDocx(demandId);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (reimportMutation.isPending) return;
        onOpenChange(next);
        if (!next) setFile(null);
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Reimportar .docx editado</DialogTitle>
          <DialogDescription>
            Reimportar nunca sobrescreve a versão {artifact.version} nem mescla automaticamente — cria uma versão
            nova. Qualquer aprovação já concedida a esta peça é invalidada e precisa ser reenviada para revisão.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".docx"
            className="hidden"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          <Button variant="outline" className="justify-start" onClick={() => fileInputRef.current?.click()}>
            <FileUp className="mr-2 h-3.5 w-3.5" />
            {file ? file.name : 'Selecionar arquivo .docx'}
          </Button>
          <p className="text-2xs leading-4 text-muted-foreground">
            Versão base esperada: v{artifact.version} — se alguém já criou uma versão mais nova enquanto isto era
            editado, o backend recusa com conflito (409) em vez de perder a mudança concorrente.
          </p>
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={reimportMutation.isPending} onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            disabled={!file || reimportMutation.isPending}
            onClick={() => {
              if (!file) return;
              reimportMutation.mutate(
                { artifactId: artifact.id, expectedVersion: artifact.version, file },
                {
                  onSuccess: (newArtifact) => {
                    toast({
                      title: 'Nova versão registrada a partir do .docx.',
                      description: 'Aprovação anterior invalidada — envie para revisão de novo antes de protocolar.',
                    });
                    onReimported(newArtifact);
                    onOpenChange(false);
                    setFile(null);
                  },
                  onError: (error) => {
                    toast({
                      variant: 'destructive',
                      title: 'Não foi possível reimportar',
                      description: errorMessage(error, 'Tente novamente.'),
                    });
                  },
                },
              );
            }}
          >
            {reimportMutation.isPending ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <FileUp className="mr-2 h-3.5 w-3.5" />}
            Reimportar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ChecklistPanel({ demandId, artifactId }: { demandId: string; artifactId: string }) {
  const checklistQuery = useLegalArtifactChecklist(demandId, artifactId);
  const checklist = checklistQuery.data;

  return (
    <KlmPanel className="p-4">
      <KlmSectionTitle
        title="Checklist de protocolabilidade"
        description="Placeholder, procuração, custas e citações — sinaliza pendências, nunca protocola sozinho."
        action={
          checklist ? (
            <KlmPill tone={LEGAL_ARTIFACT_PROTOCOLABILITY_TONE[checklist.protocolabilityState]}>
              {LEGAL_ARTIFACT_PROTOCOLABILITY_LABELS[checklist.protocolabilityState]}
            </KlmPill>
          ) : undefined
        }
      />
      {checklistQuery.isPending && <p className="mt-3 text-xs text-muted-foreground">Carregando checklist…</p>}
      {checklistQuery.isError && (
        <p className="mt-3 text-xs text-destructive">{errorMessage(checklistQuery.error, 'Não foi possível carregar o checklist.')}</p>
      )}
      {checklist && (
        <div className="mt-3 grid gap-3 text-sm">
          <div className="flex flex-wrap gap-2">
            <KlmPill tone={checklist.hasPlaceholder ? 'red' : 'green'}>
              {checklist.hasPlaceholder ? 'Tem placeholder pendente' : 'Sem placeholder'}
            </KlmPill>
            <KlmPill tone={checklist.hasProcuracao ? 'green' : 'gold'}>
              {checklist.hasProcuracao ? 'Procuração presente' : 'Sem procuração'}
            </KlmPill>
            {checklist.hasCustasPagas !== null && (
              <KlmPill tone={checklist.hasCustasPagas ? 'green' : 'gold'}>
                {checklist.hasCustasPagas ? 'Custas pagas' : 'Custas pendentes'}
              </KlmPill>
            )}
          </div>
          {checklist.missingVariables.length > 0 && (
            <div>
              <p className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">Variáveis obrigatórias faltando</p>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {checklist.missingVariables.map((variable) => (
                  <KlmPill key={variable} tone="red">
                    {variable}
                  </KlmPill>
                ))}
              </div>
            </div>
          )}
          {checklist.pendingItems.length > 0 ? (
            <ul className="grid gap-1.5">
              {checklist.pendingItems.map((pending) => (
                <li key={`${pending.item}-${pending.detail}`} className="rounded-md border border-border bg-secondary/60 px-3 py-2 text-xs">
                  <span className="font-medium text-foreground">{pending.item}</span>
                  <span className="text-muted-foreground"> — {pending.detail}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
              Nenhuma pendência no checklist.
            </p>
          )}
        </div>
      )}
    </KlmPanel>
  );
}

export function LegalArtifactsTab({ demandId }: { demandId: string }) {
  const { toast } = useToast();
  const versionsQuery = useLegalArtifactVersions(demandId);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [generateOpen, setGenerateOpen] = useState(false);
  const [reimportOpen, setReimportOpen] = useState(false);

  const versions = versionsQuery.data ?? [];
  const ordered = sortedByRecency(versions);

  useEffect(() => {
    if (selectedId && ordered.some((version) => version.id === selectedId)) return;
    setSelectedId(ordered[0]?.id ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ordered.map((version) => version.id).join(',')]);

  const selected = ordered.find((version) => version.id === selectedId) ?? null;

  const generateSectionMutation = useGenerateLegalArtifactSection(demandId);
  const submitMutation = useSubmitLegalArtifact(demandId);
  const verifyMutation = useVerifyLegalArtifactCitations(demandId);
  const reviewMutation = useReviewLegalArtifact(demandId);

  // Troca de versão selecionada invalida a conferência de citações e a
  // revisão adversarial exibidas — nunca mostra o veredito de uma versão
  // diferente da que está aberta agora (estado honesto).
  useEffect(() => {
    verifyMutation.reset();
    reviewMutation.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  if (versionsQuery.isError && isLegalDemandAccessDeniedError(versionsQuery.error)) {
    return (
      <KlmPanel className="p-6">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary">
            <ShieldAlert className="h-4 w-4 text-muted-foreground" />
          </span>
          <div>
            <p className="text-sm font-semibold text-foreground">Módulo jurídico não contratado ou sem permissão</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {errorMessage(versionsQuery.error, 'Sua conta não tem acesso a este módulo no momento.')}
            </p>
          </div>
        </div>
      </KlmPanel>
    );
  }

  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[300px_1fr]">
      <KlmPanel className="flex min-w-0 flex-col gap-3 p-4">
        <KlmSectionTitle
          title="Versões da demanda"
          description="Peças geradas ou registradas manualmente, mais recente primeiro."
          action={
            <Button size="sm" onClick={() => setGenerateOpen(true)}>
              <Plus className="mr-1.5 h-3.5 w-3.5" />
              Gerar
            </Button>
          }
        />
        {versionsQuery.isPending && <p className="text-xs text-muted-foreground">Carregando versões…</p>}
        {versionsQuery.isError && !isLegalDemandAccessDeniedError(versionsQuery.error) && (
          <p className="text-xs text-destructive">{errorMessage(versionsQuery.error, 'Não foi possível carregar as versões.')}</p>
        )}
        {!versionsQuery.isPending && ordered.length === 0 && (
          <p className="text-xs text-muted-foreground">
            Nenhuma peça ainda. Gere a primeira a partir do modelo timbrado do escritório.
          </p>
        )}
        <ul className="grid gap-1.5">
          {ordered.map((version) => (
            <li key={version.id}>
              <button
                type="button"
                onClick={() => setSelectedId(version.id)}
                className={cn(
                  'flex w-full min-w-0 flex-col gap-1 rounded-lg border px-3 py-2 text-left transition-colors',
                  version.id === selectedId
                    ? 'border-primary/40 bg-primary/5'
                    : 'border-border bg-card hover:bg-secondary/60',
                )}
              >
                <span className="truncate text-sm font-medium text-foreground">{version.title}</span>
                <span className="flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground">
                  <span>{LEGAL_ARTIFACT_KIND_LABELS[version.kind]} · v{version.version}</span>
                  <KlmPill tone={LEGAL_ARTIFACT_PROTOCOLABILITY_TONE[version.protocolabilityState]}>
                    {LEGAL_ARTIFACT_PROTOCOLABILITY_LABELS[version.protocolabilityState]}
                  </KlmPill>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </KlmPanel>

      <div className="flex min-w-0 flex-col gap-4">
        {!selected ? (
          <KlmPanel className="p-6">
            <p className="text-sm text-muted-foreground">Selecione ou gere uma peça para abrir o editor.</p>
          </KlmPanel>
        ) : (
          <>
            <KlmPanel className="p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="truncate text-lg font-semibold text-foreground">{selected.title}</h3>
                  <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                    <span>{LEGAL_ARTIFACT_KIND_LABELS[selected.kind]} · versão {selected.version}</span>
                    <KlmPill tone="muted">{LEGAL_ARTIFACT_STATUS_LABELS[selected.status] ?? selected.status}</KlmPill>
                    <KlmPill tone={selected.authorType === 'agent' ? 'blue' : 'muted'}>
                      {selected.authorType === 'agent' ? `Gerado por agente${selected.authorAgentKey ? ` (${selected.authorAgentKey})` : ''}` : 'Autoria humana'}
                    </KlmPill>
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <ExportDocxButton demandId={demandId} artifact={selected} />
                  <Button size="sm" variant="outline" onClick={() => setReimportOpen(true)}>
                    <FileUp className="mr-1.5 h-3.5 w-3.5" />
                    Reimportar .docx
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={submitMutation.isPending}
                    onClick={() => {
                      submitMutation.mutate(selected.id, {
                        onSuccess: () => toast({ title: 'Enviado para aprovação humana.' }),
                        onError: (error) =>
                          toast({ variant: 'destructive', title: 'Não foi possível enviar para aprovação', description: errorMessage(error, 'Tente novamente.') }),
                      });
                    }}
                  >
                    {submitMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Send className="mr-1.5 h-3.5 w-3.5" />}
                    Enviar para aprovação
                  </Button>
                </div>
              </div>
            </KlmPanel>

            <KlmPanel className="p-4">
              <KlmSectionTitle title="Seções" description="Regenerar uma seção cria uma versão nova — nunca sobrescreve a atual." />
              {artifactSections(selected).length === 0 ? (
                <p className="mt-3 text-xs text-muted-foreground">Esta versão não tem seções estruturadas no conteúdo.</p>
              ) : (
                <ul className="mt-3 grid gap-2">
                  {artifactSections(selected).map((section) => (
                    <li key={section.id} className="rounded-lg border border-border p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-foreground">{section.title}</p>
                          <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">{section.body}</p>
                        </div>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={generateSectionMutation.isPending}
                          onClick={() => {
                            generateSectionMutation.mutate(
                              { artifactId: selected.id, payload: { sectionId: section.id, expectedVersion: selected.version } },
                              {
                                onSuccess: (newArtifact) => {
                                  toast({ title: 'Seção regenerada.', description: `Nova versão v${newArtifact.version} criada.` });
                                  setSelectedId(newArtifact.id);
                                },
                                onError: (error) =>
                                  toast({ variant: 'destructive', title: 'Não foi possível regenerar a seção', description: errorMessage(error, 'Tente novamente.') }),
                              },
                            );
                          }}
                        >
                          {generateSectionMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </KlmPanel>

            <ChecklistPanel demandId={demandId} artifactId={selected.id} />

            <KlmPanel className="p-4">
              <KlmSectionTitle
                title="Citações"
                description="Súmula, tema, REsp e lei citados — nunca marca 'verificada' sem uma fonte confirmada pelo provedor."
                action={
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={verifyMutation.isPending}
                    onClick={() => {
                      verifyMutation.mutate(selected.id, {
                        onSuccess: (run) => {
                          toast(
                            run.allVerified
                              ? { title: 'Todas as citações verificadas.' }
                              : { variant: 'destructive', title: 'Há citações sem fonte confirmada', description: 'Revise antes de enviar para aprovação.' },
                          );
                        },
                        onError: (error) =>
                          toast({ variant: 'destructive', title: 'Não foi possível conferir citações', description: errorMessage(error, 'Tente novamente.') }),
                      });
                    }}
                  >
                    {verifyMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="mr-1.5 h-3.5 w-3.5" />}
                    Verificar citações
                  </Button>
                }
              />
              {!verifyMutation.data && !verifyMutation.isPending && (
                <p className="mt-3 text-xs text-muted-foreground">Ainda não conferido nesta sessão — clique em "Verificar citações".</p>
              )}
              {verifyMutation.data && (
                verifyMutation.data.verifications.length === 0 ? (
                  <p className="mt-3 text-xs text-muted-foreground">Nenhuma citação (súmula/tema/REsp/lei) encontrada no conteúdo.</p>
                ) : (
                  <ul className="mt-3 grid gap-1.5">
                    {verifyMutation.data.verifications.map((citation) => (
                      <li key={citation.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-xs">
                        <span className="min-w-0 truncate font-medium text-foreground">{citation.citationText}</span>
                        <span className="flex items-center gap-2">
                          {citation.sourceReference && <span className="text-2xs text-muted-foreground">{citation.sourceReference}</span>}
                          <KlmPill tone={CITATION_STATUS_TONE[citation.status]}>{CITATION_STATUS_LABELS[citation.status]}</KlmPill>
                        </span>
                      </li>
                    ))}
                  </ul>
                )
              )}
            </KlmPanel>

            <KlmPanel className="p-4">
              <KlmSectionTitle
                title="Revisão adversarial"
                description="Achados por severidade — um achado crítico bloqueia a protocolabilidade desta versão."
                action={
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={reviewMutation.isPending}
                    onClick={() => {
                      reviewMutation.mutate(selected.id, {
                        onSuccess: (run) => {
                          toast(
                            run.isProtocolable
                              ? { title: 'Revisão concluída — sem achado crítico aberto.' }
                              : { variant: 'destructive', title: 'Revisão bloqueou a protocolabilidade', description: 'Há achado crítico aberto — corrija antes de protocolar.' },
                          );
                        },
                        onError: (error) =>
                          toast({ variant: 'destructive', title: 'Não foi possível rodar a revisão', description: errorMessage(error, 'Tente novamente.') }),
                      });
                    }}
                  >
                    {reviewMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Send className="mr-1.5 h-3.5 w-3.5" />}
                    Enviar para revisão
                  </Button>
                }
              />
              {!reviewMutation.data && !reviewMutation.isPending && (
                <p className="mt-3 text-xs text-muted-foreground">Ainda não revisado nesta sessão — clique em "Enviar para revisão".</p>
              )}
              {reviewMutation.data && (
                <div className="mt-3 grid gap-2">
                  {!reviewMutation.data.isProtocolable && (
                    <p className="flex items-center gap-1.5 rounded-md border border-red-700/20 bg-red-500/[0.08] px-3 py-2 text-xs font-medium text-[#a53e3c] dark:text-red-400">
                      <AlertTriangle className="h-3.5 w-3.5" />
                      Bloqueado por achado crítico — não protocolável nesta versão.
                    </p>
                  )}
                  {reviewMutation.data.findings.length === 0 ? (
                    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                      Nenhum achado nesta revisão.
                    </p>
                  ) : (
                    <ul className="grid gap-1.5">
                      {reviewMutation.data.findings.map((finding) => (
                        <li key={finding.id} className="rounded-md border border-border px-3 py-2 text-xs">
                          <div className="flex items-center gap-2">
                            <KlmPill tone={LEGAL_REVIEW_SEVERITY_TONE[finding.severity]}>{LEGAL_REVIEW_SEVERITY_LABELS[finding.severity]}</KlmPill>
                            <span className="font-medium text-foreground">{finding.kind}</span>
                          </div>
                          <p className="mt-1 text-muted-foreground">{finding.description}</p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </KlmPanel>
          </>
        )}
      </div>

      <GenerateArtifactDialog
        demandId={demandId}
        open={generateOpen}
        onOpenChange={setGenerateOpen}
        onGenerated={(artifact) => setSelectedId(artifact.id)}
      />
      {selected && (
        <ReimportDocxDialog
          demandId={demandId}
          artifact={selected}
          open={reimportOpen}
          onOpenChange={setReimportOpen}
          onReimported={(artifact) => setSelectedId(artifact.id)}
        />
      )}
    </div>
  );
}

function ExportDocxButton({ demandId, artifact }: { demandId: string; artifact: KlmLegalArtifactEditorVersion }) {
  const { toast } = useToast();
  const [isDownloading, setIsDownloading] = useState(false);

  return (
    <Button
      size="sm"
      variant="outline"
      disabled={isDownloading}
      onClick={async () => {
        setIsDownloading(true);
        try {
          const result = await klmApi.exportLegalArtifactDocx(demandId, artifact.id);
          const url = URL.createObjectURL(result.blob);
          const link = document.createElement('a');
          link.href = url;
          link.download = result.filename ?? `${artifact.title}.docx`;
          document.body.appendChild(link);
          link.click();
          link.remove();
          URL.revokeObjectURL(url);
        } catch (error) {
          toast({ variant: 'destructive', title: 'Não foi possível exportar', description: errorMessage(error, 'Tente novamente.') });
        } finally {
          setIsDownloading(false);
        }
      }}
    >
      {isDownloading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <FileDown className="mr-1.5 h-3.5 w-3.5" />}
      Exportar .docx
    </Button>
  );
}
