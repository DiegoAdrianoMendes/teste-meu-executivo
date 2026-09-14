import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Loader2, RotateCw, ShieldAlert } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useToast } from '@/hooks/use-toast';
import {
  deactivatePlatformAiCredential,
  fetchPlatformAiCredentials,
  savePlatformAiCredential,
  type PlatformAiCredentialStatus,
} from '@/services/platform';

const QUERY_KEY = ['platform-ai-credentials'] as const;
const MIN_KEY_LENGTH = 8;

const PROVIDER_LABELS: Record<string, string> = {
  gemini: 'Google Gemini',
  anthropic: 'Anthropic (Claude)',
  openai: 'OpenAI',
  maritaca: 'Maritaca (Sabiá)',
  vertex: 'Google Vertex AI',
};

const SOURCE_LABELS: Record<PlatformAiCredentialStatus['effectiveSource'], string> = {
  blackbox: 'Blackbox',
  environment: 'Variável de ambiente',
  none: 'Sem chave',
};

function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

function formatDateTime(value: string | null): string {
  if (!value) return 'Nunca';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Indisponível';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(date);
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return 'Erro ao processar requisição.';
}

function statusBadge(item: PlatformAiCredentialStatus) {
  if (!item.keyManaged) {
    return <Badge variant="outline" className="text-2xs">Service account</Badge>;
  }
  if (item.configured && item.isActive) {
    return <Badge className="text-2xs">Ativa</Badge>;
  }
  if (item.configured) {
    return <Badge variant="secondary" className="text-2xs">Desativada</Badge>;
  }
  return <Badge variant="outline" className="text-2xs">Não gravada</Badge>;
}

export function PlatformAiCredentialsPanel({
  enabled = true,
  canManage = false,
}: {
  enabled?: boolean;
  canManage?: boolean;
}) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [confirmingProvider, setConfirmingProvider] = useState<string | null>(null);

  const credentialsQuery = useQuery({
    queryKey: QUERY_KEY,
    queryFn: fetchPlatformAiCredentials,
    enabled,
    retry: false,
  });

  const saveMutation = useMutation({
    mutationFn: ({ provider, apiKey }: { provider: string; apiKey: string }) =>
      savePlatformAiCredential(provider, apiKey),
    // A chave digitada não pode sobreviver no cache do React Query.
    gcTime: 0,
    onSuccess: (_result, variables) => {
      setDrafts((current) => ({ ...current, [variables.provider]: '' }));
      toast({
        title: `Chave de ${providerLabel(variables.provider)} gravada.`,
        description: 'Vale em até 15 segundos em todos os processos da plataforma.',
      });
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
    onError: (error, variables) => {
      toast({
        title: `Não foi possível gravar a chave de ${providerLabel(variables.provider)}.`,
        description: errorMessage(error),
        variant: 'destructive',
      });
    },
  });

  const deactivateMutation = useMutation({
    mutationFn: (provider: string) => deactivatePlatformAiCredential(provider),
    gcTime: 0,
    onSuccess: (_result, provider) => {
      setConfirmingProvider(null);
      toast({
        title: `Chave de ${providerLabel(provider)} desativada.`,
        description: 'O gateway volta à variável de ambiente, se houver, em até 15 segundos.',
      });
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
    onError: (error, provider) => {
      setConfirmingProvider(null);
      toast({
        title: `Não foi possível desativar a chave de ${providerLabel(provider)}.`,
        description: errorMessage(error),
        variant: 'destructive',
      });
    },
  });

  const data = credentialsQuery.data ?? null;
  const gate = data?.gate ?? null;
  const providers = data?.providers ?? [];

  return (
    <Card data-testid="platform-ai-credentials-panel">
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 text-lg font-semibold">
            <KeyRound className="h-4 w-4 shrink-0" />
            Chaves dos provedores de IA
          </CardTitle>
          <CardDescription className="text-xs">
            Chave da plataforma, usada por todo escritório que não trouxer a própria. Precedência:
            chave do escritório (Configurações do workspace), depois esta, depois a variável de
            ambiente. A chave nunca é exibida; só os 4 últimos caracteres.
          </CardDescription>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-8 shrink-0 text-xs"
          onClick={() => {
            void credentialsQuery.refetch();
          }}
        >
          <RotateCw className="mr-1.5 h-3.5 w-3.5" />
          Atualizar
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {credentialsQuery.isPending ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Carregando chaves dos provedores…
          </div>
        ) : credentialsQuery.isError ? (
          <div className="rounded-lg border border-destructive/30 bg-card p-4 text-sm text-destructive">
            Não foi possível carregar as chaves dos provedores. Nenhum dado alternativo será
            exibido.
          </div>
        ) : (
          <>
            {gate && !gate.cipherConfigured && (
              <div
                role="alert"
                className="rounded-lg border border-[#a53e3c]/30 bg-[#a53e3c]/5 p-3 text-xs text-[#a53e3c] dark:text-red-400"
              >
                <p className="flex items-center gap-1.5 font-semibold">
                  <ShieldAlert className="h-3.5 w-3.5" /> Cifra de segredos ausente
                </p>
                <p className="mt-1">
                  Sem INTEGRATION_SECRET_ENCRYPTION_KEY forte no processo da API nenhuma chave pode
                  ser gravada. Corrija a variável antes de colar chaves aqui.
                </p>
              </div>
            )}
            {gate && !gate.modelApisEnabled && (
              <div
                role="status"
                className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-xs text-amber-800 dark:text-amber-300"
              >
                <p className="flex items-center gap-1.5 font-semibold">
                  <ShieldAlert className="h-3.5 w-3.5" /> Gate da plataforma fechado
                </p>
                <p className="mt-1">
                  As chaves gravadas aqui ficam prontas, mas nenhum modelo é chamado até a ECS da
                  API e do worker receber KLM_MODEL_APIS_ENABLED=true com KLM_AI_BUDGET_BACKEND,
                  KLM_AI_TELEMETRY_BACKEND e KLM_AI_CIRCUIT_BACKEND em redis.
                </p>
              </div>
            )}

            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead className="bg-secondary/40 text-2xs uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 text-left">Provedor</th>
                    <th className="px-3 py-2 text-left">Situação</th>
                    <th className="px-3 py-2 text-left">Chave</th>
                    <th className="px-3 py-2 text-left">Fonte efetiva</th>
                    <th className="px-3 py-2 text-left">Atualizada em</th>
                    {canManage && <th className="px-3 py-2 text-left">Ações</th>}
                  </tr>
                </thead>
                <tbody>
                  {providers.map((item) => {
                    const draft = drafts[item.provider] ?? '';
                    const canSave =
                      draft.trim().length >= MIN_KEY_LENGTH &&
                      !saveMutation.isPending &&
                      Boolean(gate?.cipherConfigured);
                    const confirming = confirmingProvider === item.provider;
                    return (
                      <tr
                        key={item.provider}
                        className="border-t border-border align-top"
                        data-testid={`platform-ai-credential-${item.provider}`}
                      >
                        <td className="px-3 py-2 font-medium">{providerLabel(item.provider)}</td>
                        <td className="px-3 py-2">{statusBadge(item)}</td>
                        <td className="px-3 py-2 font-mono text-xs tabular-nums">
                          {item.apiKeyMasked ?? 'sem chave'}
                        </td>
                        <td className="px-3 py-2">
                          <span className="font-medium">{SOURCE_LABELS[item.effectiveSource]}</span>
                          {item.reason && (
                            <p className="mt-0.5 max-w-xs text-2xs text-muted-foreground">
                              {item.reason}
                            </p>
                          )}
                        </td>
                        <td className="px-3 py-2 text-xs tabular-nums text-muted-foreground">
                          {formatDateTime(item.updatedAt)}
                        </td>
                        {canManage && (
                          <td className="px-3 py-2">
                            {item.keyManaged ? (
                              <div className="flex min-w-[18rem] flex-col gap-2">
                                <div className="flex items-center gap-2">
                                  <Input
                                    type="password"
                                    autoComplete="new-password"
                                    spellCheck={false}
                                    aria-label={`Nova chave de ${providerLabel(item.provider)}`}
                                    placeholder="Colar a chave nova"
                                    className="h-8 text-xs"
                                    value={draft}
                                    onChange={(event) =>
                                      setDrafts((current) => ({
                                        ...current,
                                        [item.provider]: event.target.value,
                                      }))
                                    }
                                  />
                                  <Button
                                    type="button"
                                    size="sm"
                                    className="h-8 shrink-0 text-xs"
                                    disabled={!canSave}
                                    onClick={() =>
                                      saveMutation.mutate(
                                        { provider: item.provider, apiKey: draft.trim() },
                                        {
                                          // Depois de terminar, a chave não fica em `variables`
                                          // (o reset roda após o estado final do observer).
                                          onSettled: () => saveMutation.reset(),
                                        },
                                      )
                                    }
                                  >
                                    {saveMutation.isPending &&
                                    saveMutation.variables?.provider === item.provider ? (
                                      <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                                    ) : null}
                                    {item.configured ? 'Trocar chave' : 'Salvar chave'}
                                  </Button>
                                </div>
                                {item.configured && item.isActive && (
                                  <div className="flex items-center gap-2">
                                    <Button
                                      type="button"
                                      variant={confirming ? 'destructive' : 'outline'}
                                      size="sm"
                                      className="h-7 text-2xs"
                                      disabled={deactivateMutation.isPending}
                                      onClick={() => {
                                        if (confirming) {
                                          deactivateMutation.mutate(item.provider);
                                        } else {
                                          setConfirmingProvider(item.provider);
                                        }
                                      }}
                                    >
                                      {confirming ? 'Confirmar desativação' : 'Desativar'}
                                    </Button>
                                    {confirming && (
                                      <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        className="h-7 text-2xs"
                                        onClick={() => setConfirmingProvider(null)}
                                      >
                                        Cancelar
                                      </Button>
                                    )}
                                  </div>
                                )}
                              </div>
                            ) : (
                              <p className="max-w-xs text-2xs text-muted-foreground">
                                Configurado por service account na ECS, não por chave.
                              </p>
                            )}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                  {providers.length === 0 && (
                    <tr>
                      <td colSpan={canManage ? 6 : 5} className="px-3 py-4 text-center text-xs text-muted-foreground">
                        Nenhum provedor no catálogo.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <p className="text-2xs text-muted-foreground">
              A chave só é validada na primeira chamada. Se estiver errada, a rota daquele
              provedor falha com evento visível em "Operação de IA" e o roteador segue para o
              próximo modelo da classe. Uma troca vale em até 15 segundos em todos os processos.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
