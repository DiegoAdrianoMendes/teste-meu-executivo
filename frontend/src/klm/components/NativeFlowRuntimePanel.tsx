import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { ExternalLink, Loader2, RefreshCw, ShieldCheck, Workflow } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import { klmApi } from '@/klm/api';

type NativeFlowRuntimePanelProps = {
  flowId: string;
  revision: number;
  isDirty: boolean;
  flowReady: boolean;
  canManage: boolean;
  canOpenEditor?: boolean;
};

export function NativeFlowRuntimePanel({
  flowId,
  revision,
  isDirty,
  flowReady,
  canManage,
  canOpenEditor = false,
}: NativeFlowRuntimePanelProps) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [notice, setNotice] = useState<string | null>(null);
  const contextQuery = useQuery({
    queryKey: ['klm', 'automation-flow', flowId],
    queryFn: () => klmApi.nativeFlowContext(flowId),
    retry: false,
  });
  const syncMutation = useMutation({
    mutationFn: () => klmApi.reconcileNativeFlow(flowId),
    onSuccess: async (result) => {
      setNotice(
        result.graphAction === 'unchanged'
          ? `Revisão ${result.revision} está íntegra no editor nativo.`
          : `Revisão ${result.revision} reconciliada: ${result.nodeCount} etapas e ${result.connectionCount} conexões.`,
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['klm', 'automation-flow', flowId] }),
        queryClient.invalidateQueries({ queryKey: ['klm', 'automation-context'] }),
      ]);
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'O editor nativo não pôde ser reconciliado.'),
  });
  const context = contextQuery.data;
  const current = Boolean(
    context?.linked
    && context.revision === revision
    && context.digestMatches
    && context.graphMatches,
  );
  const syncDisabled = !canManage || !flowReady || isDirty || syncMutation.isPending;

  const openMirror = () => {
    navigate(`/app/flows?flow=${flowId}`);
    setNotice('Editor visual nativo aberto na plataforma.');
  };

  return (
    <KlmPanel data-testid="automation-flow-runtime" className="overflow-hidden">
      <div className="flex flex-col gap-4 p-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center bg-[#031121] text-primary dark:border dark:border-white/10">
            {contextQuery.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Workflow className="h-4 w-4" />}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-xs font-semibold">Editor visual nativo desta revisão</p>
              <KlmPill tone={current ? 'green' : context?.linked ? 'gold' : 'muted'}>
                {current ? 'íntegro' : context?.staleLink ? 'vínculo obsoleto' : context?.linked ? 'desatualizado' : 'não criado'}
              </KlmPill>
              <KlmPill tone="muted">núcleo canônico</KlmPill>
            </div>
            <p className="mt-1 max-w-[720px] text-2xs leading-4 text-muted-foreground">
              O grafo, as revisões, o replay e a validação são executados diretamente na plataforma; publicação, modelos, webhooks, e-mail e demais efeitos externos continuam sob controles próprios.
            </p>
          </div>
        </div>

        <div className="grid shrink-0 grid-cols-3 gap-3 border-y py-3 lg:min-w-[300px] lg:border-y-0 lg:border-l lg:py-0 lg:pl-4">
          <div className="border-l-2 border-primary pl-2"><strong className="block text-sm">r{context?.revision ?? revision}</strong><span className="text-2xs text-muted-foreground">revisão</span></div>
          <div className="border-l-2 border-primary pl-2"><strong className="block text-sm">{context?.nodeCount ?? 0}</strong><span className="text-2xs text-muted-foreground">etapas</span></div>
          <div className="border-l-2 border-primary pl-2"><strong className="block text-sm">{context?.connectionCount ?? 0}</strong><span className="text-2xs text-muted-foreground">conexões</span></div>
        </div>

        <div className="flex shrink-0 flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-2xs"
            disabled={syncDisabled}
            onClick={() => syncMutation.mutate()}
            title={!canManage ? 'Permissão automations.manage necessária' : isDirty ? 'Salve o rascunho antes de reconciliar' : 'Reconciliar esta revisão na plataforma'}
          >
            {syncMutation.isPending ? <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> : <RefreshCw className="mr-1.5 h-3 w-3" />}
            Validar revisão
          </Button>
          <Button
            size="sm"
            className="h-8 bg-[#031121] text-2xs text-white hover:bg-[#031121]"
            disabled={!context?.linked || !current || !canOpenEditor}
            onClick={openMirror}
            title={!canOpenEditor ? 'Permissão de visualização de automações necessária' : 'Abrir o editor visual nativo'}
          >
            <ExternalLink className="mr-1.5 h-3 w-3" />
            Abrir editor
          </Button>
        </div>
      </div>
      {contextQuery.isError && (
        <div className="flex items-center gap-2 border-t bg-red-50 px-4 py-2 text-2xs text-red-700 dark:bg-red-950/20">
          <ShieldCheck className="h-3.5 w-3.5" />O núcleo nativo não confirmou a integridade deste flow. Nenhuma execução foi presumida.
        </div>
      )}
      {notice && <div aria-live="polite" className="border-t bg-secondary/70 px-4 py-2 text-2xs">{notice}</div>}
    </KlmPanel>
  );
}
