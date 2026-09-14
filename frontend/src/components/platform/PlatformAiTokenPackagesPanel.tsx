import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Clock3, Coins, Loader2, PackagePlus, RotateCcw, XCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { useToast } from '@/hooks/use-toast';
import {
  decidePlatformWorkspaceAiTokenPackageRequest,
  fetchPlatformAiTokenPackages,
  fetchPlatformWorkspaceAiTokenPackageRequests,
  revokePlatformWorkspaceAiTokenPackageRequest,
  savePlatformAiTokenPackage,
} from '@/services/platform';
import type { AiTokenPackage, AiTokenPackageUpsert } from '@/types';
import type { PlatformSearchResult } from '@/services/platform-search';

const emptyDraft: AiTokenPackageUpsert & { key: string } = {
  key: '',
  name: '',
  description: '',
  tokenQuantity: 100_000,
  commercialPriceCents: null,
  currency: 'BRL',
  isActive: true,
  sortOrder: 0,
};

function formatTokens(value: number): string {
  return new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 }).format(value);
}

function formatPrice(value: number | null | undefined, currency: string): string {
  if (value == null) return 'Preço não publicado';
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(value / 100);
}

const requestLabels = {
  requested: 'Aguardando suporte',
  approved: 'Liberado',
  rejected: 'Recusado',
  revoked: 'Revogado',
} as const;
const REQUEST_PAGE_SIZE = 10;

export function PlatformAiTokenPackagesPanel({
  isPlatformRoot,
  canManageSupport,
}: {
  isPlatformRoot: boolean;
  canManageSupport: boolean;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState('');
  const [selectedWorkspace, setSelectedWorkspace] = useState<PlatformSearchResult | null>(null);
  const [draft, setDraft] = useState(emptyDraft);
  const [requestPage, setRequestPage] = useState(0);
  const [decisionReason, setDecisionReason] = useState('');
  const catalogQuery = useQuery({
    queryKey: ['platform', 'ai-token-packages'],
    queryFn: fetchPlatformAiTokenPackages,
    staleTime: 30_000,
  });
  const requestsQuery = useQuery({
    queryKey: ['platform', 'ai-token-package-requests', selectedWorkspaceId, requestPage],
    queryFn: () => fetchPlatformWorkspaceAiTokenPackageRequests(selectedWorkspaceId, {
      limit: REQUEST_PAGE_SIZE,
      offset: requestPage * REQUEST_PAGE_SIZE,
    }),
    enabled: Boolean(selectedWorkspaceId),
    staleTime: 15_000,
  });
  useEffect(() => {
    setRequestPage(0);
    setDecisionReason('');
  }, [selectedWorkspaceId]);
  const saveMutation = useMutation({
    mutationFn: ({ key, payload }: { key: string; payload: AiTokenPackageUpsert }) => savePlatformAiTokenPackage(key, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform', 'ai-token-packages'] });
      setDraft(emptyDraft);
      toast({ title: 'Pacote publicado no catálogo.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Não foi possível salvar o pacote', description: error.message }),
  });
  const decisionMutation = useMutation({
    mutationFn: ({ requestId, decision }: { requestId: string; decision: 'approved' | 'rejected' }) => decidePlatformWorkspaceAiTokenPackageRequest(selectedWorkspaceId, requestId, decision, decisionReason.trim()),
    onSuccess: (request) => {
      setDecisionReason('');
      setRequestPage(0);
      void queryClient.invalidateQueries({ queryKey: ['platform', 'ai-token-package-requests', selectedWorkspaceId] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'ai-costs', selectedWorkspaceId] });
      toast({ title: request.status === 'approved' ? 'Tokens liberados para o mês atual.' : 'Solicitação recusada.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Decisão não aplicada', description: error.message }),
  });
  const revokeMutation = useMutation({
    mutationFn: (requestId: string) => revokePlatformWorkspaceAiTokenPackageRequest(selectedWorkspaceId, requestId, decisionReason.trim()),
    onSuccess: () => {
      setDecisionReason('');
      setRequestPage(0);
      void queryClient.invalidateQueries({ queryKey: ['platform', 'ai-token-package-requests', selectedWorkspaceId] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'ai-costs', selectedWorkspaceId] });
      toast({ title: 'Pacote revogado e limite recalculado.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Revogação não aplicada', description: error.message }),
  });

  const editPackage = (item: AiTokenPackage) => setDraft({
    key: item.key,
    name: item.name,
    description: item.description ?? '',
    tokenQuantity: item.tokenQuantity,
    commercialPriceCents: item.commercialPriceCents ?? null,
    currency: item.currency,
    isActive: item.isActive,
    sortOrder: item.sortOrder,
  });

  return (
    <div className="grid gap-4 xl:grid-cols-[0.9fr,1.1fr]">
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base"><Coins className="h-4 w-4 text-primary" />Catálogo comercial</CardTitle>
            <CardDescription>Preço de venda do pacote, não custo interno de provedor. A ativação sempre depende do suporte.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {catalogQuery.isLoading && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Carregando catálogo</p>}
            {(catalogQuery.data ?? []).map((item) => (
              <div key={item.id} className="flex items-center justify-between gap-3 rounded-lg border p-3">
                <div className="min-w-0"><p className="truncate text-sm font-semibold">{item.name}</p><p className="truncate text-xs tabular-nums text-muted-foreground">{formatTokens(item.tokenQuantity)} tokens · {formatPrice(item.commercialPriceCents, item.currency)}</p></div>
                <div className="flex shrink-0 items-center gap-2"><Badge variant="outline">{item.isActive ? 'Publicado' : 'Inativo'}</Badge>{isPlatformRoot && <Button type="button" size="sm" variant="outline" onClick={() => editPackage(item)}>Editar</Button>}</div>
              </div>
            ))}
            {!catalogQuery.isLoading && !(catalogQuery.data ?? []).length && <p className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">Nenhum pacote publicado.</p>}
          </CardContent>
        </Card>

        {isPlatformRoot && <Card>
          <CardHeader><CardTitle className="text-base">Publicar pacote</CardTitle><CardDescription>Somente o titular da plataforma altera quantidade e preço comercial.</CardDescription></CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5"><Label htmlFor="package-key">Chave</Label><Input id="package-key" value={draft.key} onChange={(event) => setDraft((current) => ({ ...current, key: event.target.value.toLowerCase() }))} placeholder="tokens-100k" /></div>
            <div className="space-y-1.5"><Label htmlFor="package-name">Nome</Label><Input id="package-name" value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} /></div>
            <div className="space-y-1.5"><Label htmlFor="package-tokens">Quantidade de tokens</Label><Input id="package-tokens" type="number" min={1} step={1} value={draft.tokenQuantity} onChange={(event) => setDraft((current) => ({ ...current, tokenQuantity: Number(event.target.value) }))} /></div>
            <div className="space-y-1.5"><Label htmlFor="package-price">Preço comercial em centavos</Label><Input id="package-price" type="number" min={0} step={1} value={draft.commercialPriceCents ?? ''} onChange={(event) => setDraft((current) => ({ ...current, commercialPriceCents: event.target.value === '' ? null : Number(event.target.value) }))} placeholder="Opcional" /></div>
            <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="package-description">Descrição</Label><Textarea id="package-description" value={draft.description ?? ''} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} /></div>
            <label className="flex items-center justify-between gap-3 rounded-lg border p-3 sm:col-span-2"><span className="text-sm font-medium">Publicado para as contas</span><Switch checked={draft.isActive} onCheckedChange={(isActive) => setDraft((current) => ({ ...current, isActive }))} /></label>
            <div className="flex gap-2 sm:col-span-2"><Button type="button" disabled={saveMutation.isPending || draft.key.length < 3 || draft.name.length < 2 || draft.tokenQuantity < 1} onClick={() => { const { key, ...payload } = draft; saveMutation.mutate({ key, payload }); }}>{saveMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Salvar pacote</Button><Button type="button" variant="outline" onClick={() => setDraft(emptyDraft)}>Limpar</Button></div>
          </CardContent>
        </Card>}
      </div>

      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2 text-base"><PackagePlus className="h-4 w-4 text-primary" />Liberação pelo suporte</CardTitle><CardDescription>Selecione a conta, revise a solicitação e tome uma decisão explícita. Não existe liberação automática.</CardDescription></CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5"><Label>Conta</Label><PlatformEntityCombobox entityType="accounts" purpose="ai_token_package_support" value={selectedWorkspaceId || null} onChange={(value, item) => { setSelectedWorkspaceId(value ?? ''); setSelectedWorkspace(item); }} label="Selecionar conta para liberação de tokens" placeholder="Pesquisar conta" allowClear /></div>
          {selectedWorkspace && <p className="text-xs text-muted-foreground">Conta selecionada: {selectedWorkspace.label}. Os créditos aprovados valem somente no período registrado.</p>}
          {selectedWorkspaceId ? <div className="space-y-1.5"><Label htmlFor="token-package-decision-reason">Motivo da decisão</Label><Textarea id="token-package-decision-reason" value={decisionReason} onChange={(event) => setDecisionReason(event.target.value)} maxLength={500} placeholder="Explique a liberação, recusa ou revogação (mínimo de 8 caracteres)." /></div> : null}
          {requestsQuery.isLoading && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Carregando solicitações</p>}
          {requestsQuery.isError ? <p role="alert" className="text-sm text-destructive">Não foi possível carregar as solicitações desta conta.</p> : null}
          {(requestsQuery.data?.items ?? []).map((request) => (
            <div key={request.id} className="rounded-lg border p-3">
              <div className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><p className="truncate text-sm font-semibold">{request.packageNameSnapshot}</p><p className="truncate text-xs tabular-nums text-muted-foreground">{formatTokens(request.tokenQuantitySnapshot)} tokens · {formatPrice(request.commercialPriceCentsSnapshot, request.currencySnapshot)}</p></div><Badge className="shrink-0" variant="outline">{requestLabels[request.status]}</Badge></div>
              <p className="mt-2 text-xs text-muted-foreground">Período: {new Date(`${request.periodStart}T00:00:00`).toLocaleDateString('pt-BR')} a {new Date(`${request.periodEnd}T00:00:00`).toLocaleDateString('pt-BR')}.</p>
              {canManageSupport && request.status === 'requested' && <div className="mt-3 flex flex-wrap gap-2"><Button type="button" size="sm" disabled={decisionMutation.isPending || decisionReason.trim().length < 8} onClick={() => decisionMutation.mutate({ requestId: request.id, decision: 'approved' })}><CheckCircle2 className="mr-2 h-3.5 w-3.5" />Liberar tokens</Button><Button type="button" size="sm" variant="outline" disabled={decisionMutation.isPending || decisionReason.trim().length < 8} onClick={() => decisionMutation.mutate({ requestId: request.id, decision: 'rejected' })}><XCircle className="mr-2 h-3.5 w-3.5" />Recusar</Button></div>}
              {canManageSupport && request.status === 'approved' && <Button type="button" size="sm" variant="outline" className="mt-3" disabled={revokeMutation.isPending || decisionReason.trim().length < 8} onClick={() => revokeMutation.mutate(request.id)}><RotateCcw className="mr-2 h-3.5 w-3.5" />Revogar</Button>}
            </div>
          ))}
          {!selectedWorkspaceId && <p className="flex items-center gap-2 rounded-lg border border-dashed p-3 text-sm text-muted-foreground"><Clock3 className="h-4 w-4" />Selecione uma conta para revisar pedidos.</p>}
          {selectedWorkspaceId && !requestsQuery.isLoading && !requestsQuery.isError && !(requestsQuery.data?.items ?? []).length && <p className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">Nenhuma solicitação nesta conta.</p>}
          {requestsQuery.data ? <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><span>{requestsQuery.data.total === 0 ? '0 solicitações' : `${requestsQuery.data.offset + 1}–${requestsQuery.data.offset + requestsQuery.data.items.length} de ${requestsQuery.data.total}`}</span><div className="flex gap-2"><Button type="button" size="sm" variant="outline" disabled={requestPage === 0 || requestsQuery.isFetching} onClick={() => setRequestPage((page) => Math.max(0, page - 1))}>Anterior</Button><Button type="button" size="sm" variant="outline" disabled={!requestsQuery.data.hasMore || requestsQuery.isFetching} onClick={() => setRequestPage((page) => page + 1)}>Próxima</Button></div></div> : null}
        </CardContent>
      </Card>
    </div>
  );
}
