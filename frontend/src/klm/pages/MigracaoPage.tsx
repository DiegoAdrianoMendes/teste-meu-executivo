import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Database,
  FileUp,
  ListChecks,
  Loader2,
  RotateCcw,
  ShieldAlert,
  TrafficCone,
  UploadCloud,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import { useToast } from '@/hooks/use-toast';
import {
  klmApi,
  type KlmMigrationDivergence,
  type KlmMigrationJob,
  type KlmMigrationSourceSystem,
} from '@/klm/api';

type WizardStep = 1 | 2 | 3 | 4 | 5 | 6 | 7;

const stepLabels: Record<WizardStep, string> = {
  1: 'Origem',
  2: 'Arquivo',
  3: 'Prévia',
  4: 'Importar',
  5: 'Divergências',
  6: 'Semáforo de corte',
  7: 'Rollback',
};

type SourceOption = {
  value: KlmMigrationSourceSystem;
  label: string;
  detail: string;
  ready: boolean;
};

const sourceOptions: SourceOption[] = [
  { value: 'universal_csv', label: 'Universal (CSV)', detail: 'Planilha própria ou exportada de qualquer sistema, mapeada por domínio.', ready: true },
  { value: 'universal_xlsx', label: 'Universal (XLSX)', detail: 'Mesma leitura universal, em Excel.', ready: true },
  { value: 'zip_docs', label: 'Pacote de documentos (ZIP)', detail: 'Arquivos e anexos do caso, um ZIP por lote.', ready: true },
  { value: 'json', label: 'JSON estruturado', detail: 'Exportação já normalizada por domínio.', ready: true },
  { value: 'astrea', label: 'Astrea', detail: 'Aguardando arquivo real de exportação do Astrea para mapear o layout — sem suporte fingido.', ready: false },
  { value: 'advbox', label: 'ADVBOX', detail: 'Aguardando arquivo real de exportação do ADVBOX para mapear o layout — sem suporte fingido.', ready: false },
  { value: 'datajuri', label: 'DataJuri', detail: 'Aguardando arquivo real de exportação do DataJuri para mapear o layout — sem suporte fingido.', ready: false },
];

const domainLabels: Record<string, string> = {
  clientes: 'Clientes',
  contatos: 'Contatos',
  partes: 'Partes',
  casos: 'Casos',
  processos: 'Processos',
  tarefas: 'Tarefas',
  prazos: 'Prazos',
  documentos: 'Documentos',
  contratos: 'Contratos',
  honorarios: 'Honorários',
  despesas: 'Despesas',
  recebimentos: 'Recebimentos',
  tags: 'Tags',
};

type PreviewRow = { staged: number; validos: number; invalidos: number; duplicados: number; ambiguos: number };
type ReconciliationRow = {
  origem: number;
  importado: number;
  mesclado: number;
  rejeitado: number;
  invalidos: number;
  aguardandoImportador: number;
};

function asPreviewSummary(job: KlmMigrationJob | undefined): Record<string, PreviewRow> {
  return (job?.previewSummary ?? {}) as Record<string, PreviewRow>;
}

function asReconciliationSummary(job: KlmMigrationJob | undefined): Record<string, ReconciliationRow> {
  return (job?.reconciliationSummary ?? {}) as Record<string, ReconciliationRow>;
}

const semaphoreTone: Record<string, 'red' | 'gold' | 'green'> = { vermelho: 'red', amarelo: 'gold', verde: 'green' };
const semaphoreLabel: Record<string, string> = {
  vermelho: 'Vermelho — pendência crítica não resolvida',
  amarelo: 'Amarelo — pendências de atenção, sem crítica aberta',
  verde: 'Verde — nenhuma pendência aberta',
};

export default function MigracaoPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [step, setStep] = useState<WizardStep>(1);
  const [sourceSystem, setSourceSystem] = useState<KlmMigrationSourceSystem>('universal_csv');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [cutoverDecisionInput, setCutoverDecisionInput] = useState<'prosseguir' | 'nao_prosseguir'>('nao_prosseguir');
  const [cutoverNotes, setCutoverNotes] = useState('');
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [divergenceNotes, setDivergenceNotes] = useState<Record<string, string>>({});

  const jobQuery = useQuery({
    queryKey: ['klm', 'migration', 'job', jobId],
    queryFn: () => klmApi.getMigrationJob(jobId!),
    enabled: Boolean(jobId),
  });
  const job = jobQuery.data;

  const divergencesQuery = useQuery({
    queryKey: ['klm', 'migration', 'job', jobId, 'divergencias'],
    queryFn: () => klmApi.listMigrationDivergences(jobId!),
    enabled: Boolean(jobId) && step >= 5,
  });
  const divergences = divergencesQuery.data ?? [];
  const openDivergences = divergences.filter((item) => !item.resolvido);

  const previewSummary = asPreviewSummary(job);
  const previewDomains = Object.keys(previewSummary);
  const reconciliationSummary = asReconciliationSummary(job);
  const reconciliationDomains = Object.keys(reconciliationSummary);

  const createJobMutation = useMutation({
    mutationFn: () => {
      if (!selectedFile) throw new Error('Selecione um arquivo antes de enviar.');
      return klmApi.createMigrationJob({ file: selectedFile, sourceSystem });
    },
    onSuccess: async (createdJob) => {
      setUploadError(null);
      setJobId(createdJob.id);
      if (createdJob.status === 'erro') {
        setUploadError(createdJob.errorMessage ?? 'O arquivo foi recebido, mas não pôde ser processado.');
        return;
      }
      await previewMutation.mutateAsync(createdJob.id);
    },
    onError: (error: Error) => setUploadError(error.message),
  });

  const previewMutation = useMutation({
    mutationFn: (targetJobId: string) => klmApi.buildMigrationPreview(targetJobId),
    onSuccess: async (updatedJob) => {
      queryClient.setQueryData(['klm', 'migration', 'job', updatedJob.id], updatedJob);
      setStep(3);
    },
    onError: () => toast({ title: 'Não foi possível calcular a prévia', variant: 'destructive' }),
  });

  const importBatchMutation = useMutation({
    mutationFn: () => klmApi.importMigrationBatch(jobId!, 50),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'migration', 'job', jobId] });
      if (result.status === 'reconciliando') {
        await reconcileMutation.mutateAsync();
      }
    },
    onError: () => toast({ title: 'Falha ao importar o lote', variant: 'destructive' }),
  });

  const reconcileMutation = useMutation({
    mutationFn: () => klmApi.reconcileMigrationJob(jobId!),
    onSuccess: async (updatedJob) => {
      queryClient.setQueryData(['klm', 'migration', 'job', updatedJob.id], updatedJob);
      await queryClient.invalidateQueries({ queryKey: ['klm', 'migration', 'job', jobId, 'divergencias'] });
      setStep(5);
    },
    onError: () => toast({ title: 'Falha ao reconciliar o job', variant: 'destructive' }),
  });

  const resolveDivergenceMutation = useMutation({
    mutationFn: ({ divergenceId, note }: { divergenceId: string; note: string }) =>
      klmApi.resolveMigrationDivergence(jobId!, divergenceId, note || null),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'migration', 'job', jobId, 'divergencias'] });
      toast({ title: 'Divergência resolvida' });
    },
    onError: () => toast({ title: 'Não foi possível resolver a divergência', variant: 'destructive' }),
  });

  const cutoverMutation = useMutation({
    mutationFn: () => klmApi.decideMigrationCutover(jobId!, cutoverDecisionInput, cutoverNotes || null),
    onSuccess: async (updatedJob) => {
      queryClient.setQueryData(['klm', 'migration', 'job', updatedJob.id], updatedJob);
      toast({ title: `Decisão registrada: ${cutoverDecisionInput === 'prosseguir' ? 'prosseguir' : 'não prosseguir'}` });
      setStep(7);
    },
    onError: () => toast({ title: 'Não foi possível registrar a decisão', variant: 'destructive' }),
  });

  const rollbackMutation = useMutation({
    mutationFn: () => klmApi.rollbackMigrationJob(jobId!),
    onSuccess: async (updatedJob) => {
      queryClient.setQueryData(['klm', 'migration', 'job', updatedJob.id], updatedJob);
      toast({ title: 'Job revertido — o que foi mesclado em registros existentes foi preservado.' });
    },
    onError: () => toast({ title: 'Não foi possível reverter o job', variant: 'destructive' }),
  });

  const semaphore = job?.semaphore ?? null;
  const cutoverAlreadyDecided = Boolean(job?.cutoverDecision);
  const blockedByRedSemaphore = semaphore === 'vermelho' && cutoverDecisionInput === 'prosseguir' && !riskAcknowledged;

  const importProgress = useMemo(() => {
    if (!job) return 0;
    if (job.status === 'concluido' || job.status === 'reconciliando') return 100;
    if (job.status === 'importando') return 60;
    if (job.status === 'previa') return 10;
    return 0;
  }, [job]);

  const rollbackEligible = job != null && job.status !== 'revertido';

  const goTo = (target: WizardStep) => setStep(target);

  return (
    <div className="mx-auto max-w-[1220px] space-y-5">
      <header>
        <p className="text-2xs font-semibold uppercase text-[#0057d8]">Gestão / Onboarding</p>
        <h2 className="mt-2 text-2xl font-semibold">Migração de dados</h2>
        <p className="mt-1 max-w-3xl text-sm leading-5 text-muted-foreground">
          Importador universal do workspace: envie o arquivo de exportação, confira a prévia por domínio,
          resolva divergências e só então decida o corte — o semáforo informa o risco, não garante o resultado.
        </p>
      </header>

      <KlmPanel className="p-3">
        <ol className="flex flex-wrap items-center gap-2 text-2xs">
          {(Object.keys(stepLabels).map(Number) as WizardStep[]).map((n) => (
            <li key={n} className="flex items-center gap-2">
              <button
                type="button"
                disabled={n > step && !(n <= step)}
                onClick={() => n <= Math.max(step, jobId ? 7 : 1) && n <= step && goTo(n)}
                className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-medium ${
                  n === step
                    ? 'border-primary bg-primary/10 text-[#0057d8]'
                    : n < step
                      ? 'border-emerald-600/30 bg-emerald-500/10 text-[#0b7435]'
                      : 'border-border text-muted-foreground'
                }`}
              >
                {n < step ? <CheckCircle2 className="h-3.5 w-3.5" /> : <CircleDashed className="h-3.5 w-3.5" />}
                {n}. {stepLabels[n]}
              </button>
              {n < 7 && <ChevronRight className="h-3 w-3 text-muted-foreground" />}
            </li>
          ))}
        </ol>
      </KlmPanel>

      {step === 1 && (
        <KlmPanel className="p-5">
          <KlmSectionTitle
            title="1. Escolha a origem"
            description="Sistemas com layout mapeado importam direto. Astrea, ADVBOX e DataJuri ainda não têm suporte real — aparecem aqui para deixar claro o que falta, não para fingir compatibilidade."
          />
          <RadioGroup value={sourceSystem} onValueChange={(value) => setSourceSystem(value as KlmMigrationSourceSystem)} className="mt-4 gap-3">
            {sourceOptions.map((option) => (
              <label
                key={option.value}
                htmlFor={`source-${option.value}`}
                className={`flex items-start gap-3 rounded-lg border p-3 text-sm ${option.ready ? 'cursor-pointer' : 'cursor-not-allowed opacity-70'}`}
              >
                <RadioGroupItem id={`source-${option.value}`} value={option.value} aria-label={`Origem ${option.label}`} disabled={!option.ready} className="mt-0.5" />
                <span className="min-w-0">
                  <span className="flex items-center gap-2">
                    <strong>{option.label}</strong>
                    {option.ready ? <KlmPill tone="green">pronto</KlmPill> : <KlmPill tone="muted">aguardando arquivo real de exportação</KlmPill>}
                  </span>
                  <span className="mt-1 block text-2xs text-muted-foreground">{option.detail}</span>
                </span>
              </label>
            ))}
          </RadioGroup>
          <div className="mt-5 flex justify-end">
            <Button onClick={() => setStep(2)} disabled={!sourceOptions.find((o) => o.value === sourceSystem)?.ready}>
              Continuar <ChevronRight className="ml-1.5 h-3.5 w-3.5" />
            </Button>
          </div>
        </KlmPanel>
      )}

      {step === 2 && (
        <KlmPanel className="p-5">
          <KlmSectionTitle title="2. Enviar arquivo" description={`Origem selecionada: ${sourceOptions.find((o) => o.value === sourceSystem)?.label}.`} />
          <input
            ref={fileInputRef}
            type="file"
            className="sr-only"
            aria-label="Selecionar arquivo de migração"
            accept=".csv,.xlsx,.zip,.json"
            onChange={(event) => {
              const file = event.target.files?.[0] ?? null;
              setSelectedFile(file);
              setUploadError(null);
            }}
          />
          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
            <Button variant="outline" onClick={() => fileInputRef.current?.click()}>
              <FileUp className="mr-2 h-3.5 w-3.5" />
              {selectedFile ? selectedFile.name : 'Selecionar arquivo (CSV/XLSX/ZIP/JSON)'}
            </Button>
            <Button
              onClick={() => createJobMutation.mutate()}
              disabled={!selectedFile || createJobMutation.isPending || previewMutation.isPending}
            >
              {createJobMutation.isPending || previewMutation.isPending ? (
                <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
              ) : (
                <UploadCloud className="mr-2 h-3.5 w-3.5" />
              )}
              Enviar e calcular prévia
            </Button>
          </div>
          {uploadError && (
            <div className="mt-4 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-2xs text-[#a53e3c]">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{uploadError}</span>
            </div>
          )}
        </KlmPanel>
      )}

      {step === 3 && (
        <KlmPanel className="p-5">
          <KlmSectionTitle
            title="3. Prévia por domínio"
            description="Contagens calculadas em cima do que foi lido do arquivo — antes de qualquer registro entrar no sistema."
            action={<KlmPill tone="blue">{job?.status ?? '—'}</KlmPill>}
          />
          <div className="mt-4 overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Domínio</TableHead>
                  <TableHead className="text-right">Lidos</TableHead>
                  <TableHead className="text-right">Válidos</TableHead>
                  <TableHead className="text-right">Inválidos</TableHead>
                  <TableHead className="text-right">Duplicados</TableHead>
                  <TableHead className="text-right">Ambíguos</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {previewDomains.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="py-6 text-center text-xs text-muted-foreground">
                      {jobQuery.isLoading || previewMutation.isPending ? 'Calculando prévia...' : 'Nenhum registro reconhecido no arquivo.'}
                    </TableCell>
                  </TableRow>
                )}
                {previewDomains.map((domain) => {
                  const row = previewSummary[domain];
                  return (
                    <TableRow key={domain}>
                      <TableCell className="font-medium">{domainLabels[domain] ?? domain}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.staged}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.validos}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.invalidos}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.duplicados}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.ambiguos}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <div className="mt-5 flex justify-end">
            <Button onClick={() => setStep(4)} disabled={previewDomains.length === 0}>
              Iniciar importação <ChevronRight className="ml-1.5 h-3.5 w-3.5" />
            </Button>
          </div>
        </KlmPanel>
      )}

      {step === 4 && (
        <KlmPanel className="p-5">
          <KlmSectionTitle
            title="4. Importar em lotes"
            description="Clientes, casos, processos, recebimentos e despesas importam automaticamente. Os demais domínios ficam preservados em staging até um importador dedicado."
            action={<KlmPill tone="blue">{job?.status ?? '—'}</KlmPill>}
          />
          <div className="mt-4 space-y-3">
            <KlmProgress value={importProgress} tone={job?.status === 'concluido' ? 'green' : 'gold'} />
            <div className="flex items-center gap-3 text-2xs text-muted-foreground">
              <Database className="h-3.5 w-3.5" />
              Situação do job: <strong className="text-foreground">{job?.status ?? 'carregando'}</strong>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => importBatchMutation.mutate()}
                disabled={importBatchMutation.isPending || reconcileMutation.isPending || job?.status === 'concluido'}
              >
                {(importBatchMutation.isPending || reconcileMutation.isPending) && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}
                Importar próximo lote (50)
              </Button>
              {job?.status === 'concluido' && (
                <Button variant="outline" onClick={() => setStep(5)}>
                  Ver divergências <ChevronRight className="ml-1.5 h-3.5 w-3.5" />
                </Button>
              )}
            </div>
          </div>
        </KlmPanel>
      )}

      {step === 5 && (
        <KlmPanel className="p-5">
          <KlmSectionTitle
            title="5. Divergências"
            description="Cada item aqui foi encontrado na reconciliação — resolver não apaga o histórico, só marca como tratado."
            action={<KlmPill tone={openDivergences.length > 0 ? 'red' : 'green'}>{openDivergences.length} em aberto</KlmPill>}
          />
          <div className="mt-4 space-y-3">
            {divergencesQuery.isLoading && <p className="text-xs text-muted-foreground">Carregando divergências...</p>}
            {!divergencesQuery.isLoading && divergences.length === 0 && (
              <p className="text-xs text-muted-foreground">Nenhuma divergência registrada nesta migração.</p>
            )}
            {divergences.map((item: KlmMigrationDivergence) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="flex items-center gap-2">
                    <KlmPill tone={item.severity === 'critica' ? 'red' : 'gold'}>{item.severity === 'critica' ? 'Crítica' : 'Atenção'}</KlmPill>
                    {item.domain && <KlmPill tone="muted">{domainLabels[item.domain] ?? item.domain}</KlmPill>}
                    <span className="text-2xs text-muted-foreground">{item.tipo}</span>
                  </span>
                  <KlmPill tone={item.resolvido ? 'green' : 'muted'}>{item.resolvido ? 'Resolvida' : 'Aberta'}</KlmPill>
                </div>
                <p className="mt-2 text-xs leading-5">{item.detalhe}</p>
                {!item.resolvido && (
                  <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                    <Textarea
                      value={divergenceNotes[item.id] ?? ''}
                      onChange={(event) => setDivergenceNotes((prev) => ({ ...prev, [item.id]: event.target.value }))}
                      placeholder="Nota de resolução (opcional)"
                      className="min-h-[38px] text-xs"
                    />
                    <Button
                      variant="outline"
                      className="shrink-0"
                      onClick={() => resolveDivergenceMutation.mutate({ divergenceId: item.id, note: divergenceNotes[item.id] ?? '' })}
                      disabled={resolveDivergenceMutation.isPending}
                    >
                      Resolver
                    </Button>
                  </div>
                )}
                {item.resolvido && item.resolutionNote && (
                  <p className="mt-2 text-2xs text-muted-foreground">Nota: {item.resolutionNote}</p>
                )}
              </div>
            ))}
          </div>
          <div className="mt-5 flex justify-end">
            <Button onClick={() => setStep(6)}>
              Ir para o semáforo de corte <ChevronRight className="ml-1.5 h-3.5 w-3.5" />
            </Button>
          </div>
        </KlmPanel>
      )}

      {step === 6 && (
        <KlmPanel className="p-5">
          <KlmSectionTitle
            title="6. Semáforo de corte"
            description="O semáforo resume o estado das divergências no momento da reconciliação. Ele informa o risco — não garante que os dados estão corretos; a decisão de virar a chave é sempre humana."
          />
          <div className="mt-4 flex items-center gap-3">
            <TrafficCone className="h-5 w-5 text-muted-foreground" />
            <KlmPill tone={semaphore ? semaphoreTone[semaphore] : 'muted'}>
              {semaphore ? semaphoreLabel[semaphore] : 'Reconciliação ainda não calculada'}
            </KlmPill>
          </div>

          {reconciliationDomains.length > 0 && (
            <div className="mt-4 overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Domínio</TableHead>
                    <TableHead className="text-right">Origem</TableHead>
                    <TableHead className="text-right">Importado</TableHead>
                    <TableHead className="text-right">Mesclado</TableHead>
                    <TableHead className="text-right">Rejeitado</TableHead>
                    <TableHead className="text-right">Aguardando importador</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {reconciliationDomains.map((domain) => {
                    const row = reconciliationSummary[domain];
                    return (
                      <TableRow key={domain}>
                        <TableCell className="font-medium">{domainLabels[domain] ?? domain}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.origem}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.importado}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.mesclado}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.rejeitado}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.aguardandoImportador}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}

          <div className="mt-5 space-y-3 rounded-lg border p-4">
            <p className="text-sm font-semibold">Decisão de corte</p>
            {cutoverAlreadyDecided ? (
              <p className="text-xs text-muted-foreground">
                Decisão já registrada: <strong className="text-foreground">{job?.cutoverDecision?.decision}</strong>.
              </p>
            ) : (
              <>
                <RadioGroup
                  value={cutoverDecisionInput}
                  onValueChange={(value) => {
                    setCutoverDecisionInput(value as 'prosseguir' | 'nao_prosseguir');
                    setRiskAcknowledged(false);
                  }}
                  className="gap-2"
                >
                  <label className="flex items-center gap-2 text-xs" htmlFor="cutover-prosseguir">
                    <RadioGroupItem id="cutover-prosseguir" value="prosseguir" aria-label="Prosseguir com o corte" />
                    Prosseguir com o corte
                  </label>
                  <label className="flex items-center gap-2 text-xs" htmlFor="cutover-nao-prosseguir">
                    <RadioGroupItem id="cutover-nao-prosseguir" value="nao_prosseguir" aria-label="Não prosseguir agora" />
                    Não prosseguir agora
                  </label>
                </RadioGroup>
                {semaphore === 'vermelho' && cutoverDecisionInput === 'prosseguir' && (
                  <label className="flex items-start gap-2 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-2xs text-[#a53e3c]">
                    <input
                      type="checkbox"
                      aria-label="Confirmo que estou ciente do risco do corte"
                      className="mt-0.5"
                      checked={riskAcknowledged}
                      onChange={(event) => setRiskAcknowledged(event.target.checked)}
                    />
                    <span className="flex items-start gap-1.5">
                      <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      O semáforo está vermelho — há pendência crítica não resolvida. Estou ciente do risco e decido
                      prosseguir mesmo assim.
                    </span>
                  </label>
                )}
                <Textarea
                  value={cutoverNotes}
                  onChange={(event) => setCutoverNotes(event.target.value)}
                  placeholder="Justificativa da decisão (opcional)"
                  className="min-h-[60px] text-xs"
                />
                <Button
                  onClick={() => cutoverMutation.mutate()}
                  disabled={cutoverMutation.isPending || blockedByRedSemaphore || job?.status !== 'concluido'}
                >
                  {cutoverMutation.isPending && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}
                  Registrar decisão
                </Button>
                {blockedByRedSemaphore && (
                  <p className="text-2xs text-[#a53e3c]">Confirme a ciência do risco acima para prosseguir com semáforo vermelho.</p>
                )}
              </>
            )}
          </div>
        </KlmPanel>
      )}

      {step === 7 && (
        <KlmPanel className="p-5">
          <KlmSectionTitle
            title="7. Rollback"
            description="Reverte o job inteiro: desfaz o que foi criado nesta migração e preserva o que já foi mesclado em registros existentes."
            action={<KlmPill tone={job?.status === 'revertido' ? 'red' : 'green'}>{job?.status ?? '—'}</KlmPill>}
          />
          <div className="mt-4 space-y-3 rounded-lg border p-4 text-xs">
            <div className="flex items-center gap-2">
              <ListChecks className="h-4 w-4 text-muted-foreground" />
              <span>
                Job <strong className="tabular-nums">{job?.id?.slice(0, 8)}</strong> · origem{' '}
                <strong>{sourceOptions.find((o) => o.value === job?.sourceSystem)?.label ?? job?.sourceSystem}</strong>
              </span>
            </div>
            {job?.status === 'revertido' ? (
              <p className="text-muted-foreground">Este job já foi revertido em {job.rolledBackAt ? new Date(job.rolledBackAt).toLocaleString('pt-BR') : '—'}.</p>
            ) : (
              <Button variant="outline" onClick={() => rollbackMutation.mutate()} disabled={!rollbackEligible || rollbackMutation.isPending}>
                {rollbackMutation.isPending && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}
                <RotateCcw className="mr-2 h-3.5 w-3.5" />
                Reverter este job
              </Button>
            )}
          </div>
        </KlmPanel>
      )}
    </div>
  );
}
