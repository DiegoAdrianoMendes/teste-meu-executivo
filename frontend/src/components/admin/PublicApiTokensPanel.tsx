import { useMemo, useState } from 'react';
import { Copy, KeyRound, ShieldCheck, TimerReset } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useToast } from '@/hooks/use-toast';
import type { PublicApiRequestLog, PublicApiScope, WorkspacePublicApiToken } from '@/types';

const AVAILABLE_SCOPES: PublicApiScope[] = [
  'contacts:read',
  'conversations:read',
  'leads:write',
  'matters:write',
  'notes:write',
  'templates:send',
  'campaigns:write',
];

interface PublicApiTokensPanelProps {
  baseUrl?: string | null;
  tokens: WorkspacePublicApiToken[];
  logs: PublicApiRequestLog[];
  loading?: boolean;
  readOnly?: boolean;
  onCreate: (payload: {
    label: string;
    scopes: PublicApiScope[];
    rateLimitPerMinute: number;
    expiresAt?: string | null;
  }) => Promise<{ plainTextToken: string }>;
  onRevoke: (tokenId: string) => Promise<void>;
}

export function PublicApiTokensPanel({
  baseUrl,
  tokens,
  logs,
  loading = false,
  readOnly = false,
  onCreate,
  onRevoke,
}: PublicApiTokensPanelProps) {
  const { toast } = useToast();
  const [label, setLabel] = useState('');
  const [rateLimitPerMinute, setRateLimitPerMinute] = useState('120');
  const [expiresAt, setExpiresAt] = useState('');
  const [selectedScopes, setSelectedScopes] = useState<PublicApiScope[]>(AVAILABLE_SCOPES);
  const [plainTextToken, setPlainTextToken] = useState('');
  const [saving, setSaving] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const stats = useMemo(() => ({
    total: tokens.length,
    active: tokens.filter((item) => item.isActive && !item.revokedAt).length,
    revoked: tokens.filter((item) => Boolean(item.revokedAt) || !item.isActive).length,
  }), [tokens]);

  const toggleScope = (scope: PublicApiScope) => {
    setSelectedScopes((current) => (
      current.includes(scope)
        ? current.filter((item) => item !== scope)
        : [...current, scope]
    ));
  };

  const handleCreate = async () => {
    if (!label.trim() || !selectedScopes.length) return;
    setSaving(true);
    try {
      const created = await onCreate({
        label: label.trim(),
        scopes: selectedScopes,
        rateLimitPerMinute: Number(rateLimitPerMinute) || 120,
        expiresAt: expiresAt || null,
      });
      setPlainTextToken(created.plainTextToken);
      setLabel('');
      setRateLimitPerMinute('120');
      setExpiresAt('');
      setSelectedScopes(AVAILABLE_SCOPES);
      toast({ title: 'Token da API externa gerado.' });
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Erro ao gerar token',
        description: error instanceof Error ? error.message : 'Não foi possível gerar o token agora.',
      });
    } finally {
      setSaving(false);
    }
  };

  const handleRevoke = async (tokenId: string) => {
    setRevokingId(tokenId);
    try {
      await onRevoke(tokenId);
      toast({ title: 'Token revogado.' });
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Erro ao revogar token',
        description: error instanceof Error ? error.message : 'Não foi possível revogar o token.',
      });
    } finally {
      setRevokingId(null);
    }
  };

  const copyToken = async () => {
    if (!plainTextToken) return;
    try {
      await navigator.clipboard.writeText(plainTextToken);
    } catch {
      // noop
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Carregando tokens da API externa...
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        <Card>
          <CardContent className="flex items-center gap-3 py-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
              <KeyRound className="h-4 w-4 text-primary" />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Tokens</p>
              <p className="text-2xl font-bold tabular-nums">{stats.total}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 py-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/10">
              <ShieldCheck className="h-4 w-4 text-emerald-600" />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Ativos</p>
              <p className="text-2xl font-bold tabular-nums">{stats.active}</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 py-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-500/10">
              <TimerReset className="h-4 w-4 text-amber-600" />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Revogados</p>
              <p className="text-2xl font-bold tabular-nums">{stats.revoked}</p>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.05fr,0.95fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Tokens da API externa</CardTitle>
            <CardDescription>
              Gere tokens por workspace com escopos, rate limit e auditoria. Use no header <span className="font-mono">X-KLM-API-Key</span>.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Rótulo do token</Label>
                <Input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Ex.: CRM parceiro" disabled={readOnly} />
              </div>
              <div className="space-y-1.5">
                <Label>Rate limit por minuto</Label>
                <Input value={rateLimitPerMinute} onChange={(event) => setRateLimitPerMinute(event.target.value)} inputMode="numeric" disabled={readOnly} />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>Expira em</Label>
              <Input type="datetime-local" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} disabled={readOnly} />
            </div>

            <div className="space-y-2">
              <Label>Escopos liberados</Label>
              <div className="flex flex-wrap gap-2">
                {AVAILABLE_SCOPES.map((scope) => {
                  const active = selectedScopes.includes(scope);
                  return (
                    <button
                      key={scope}
                      type="button"
                      onClick={() => toggleScope(scope)}
                      disabled={readOnly}
                      className={`rounded-full border px-3 py-1 text-xs transition ${
                        active ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground'
                      }`}
                    >
                      {scope}
                    </button>
                  );
                })}
              </div>
            </div>

            {!!plainTextToken && (
              <div className="rounded-lg border border-primary/20 bg-primary/5 p-3">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Token gerado agora</p>
                <p className="mt-2 break-all rounded-md bg-background px-3 py-2 font-mono text-xs">{plainTextToken}</p>
                <div className="mt-3 flex justify-end">
                  <Button type="button" variant="outline" size="sm" onClick={copyToken}>
                    <Copy className="mr-2 h-4 w-4" />
                    Copiar token
                  </Button>
                </div>
              </div>
            )}

            <div className="flex items-center justify-between rounded-md border border-dashed p-3 text-xs text-muted-foreground">
              <span>Base da API externa: {baseUrl ? `${baseUrl}/api/v1/public-api` : 'indisponível'}</span>
              <Button onClick={handleCreate} disabled={saving || readOnly || !label.trim() || !selectedScopes.length}>
                {saving ? 'Gerando token...' : readOnly ? 'Somente leitura' : 'Gerar token'}
              </Button>
            </div>

            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Token</TableHead>
                  <TableHead>Escopos</TableHead>
                  <TableHead>Uso</TableHead>
                  <TableHead>Ação</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tokens.map((token) => (
                  <TableRow key={token.id}>
                    <TableCell>
                      <div>
                        <p className="text-sm font-medium">{token.label}</p>
                        <p className="text-xs text-muted-foreground">{token.tokenPrefix}...</p>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1.5">
                        {token.scopes.map((scope) => (
                          <Badge key={scope} variant="outline">{scope}</Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      <p>{token.rateLimitPerMinute}/min</p>
                      <p>{token.lastUsedAt ? new Date(token.lastUsedAt).toLocaleString('pt-BR') : 'Nunca usado'}</p>
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void handleRevoke(token.id)}
                        disabled={readOnly || !token.isActive || revokingId === token.id}
                      >
                        {revokingId === token.id ? 'Revogando...' : 'Revogar'}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Auditoria recente</CardTitle>
            <CardDescription>
              Últimas chamadas autenticadas na API externa do workspace.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {logs.length ? logs.map((log) => (
              <div key={log.id} className="rounded-md border p-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium">{log.method} {log.path}</p>
                    <p className="text-xs text-muted-foreground">{new Date(log.requestedAt).toLocaleString('pt-BR')}</p>
                  </div>
                  <Badge variant={log.statusCode >= 400 ? 'destructive' : 'secondary'}>{log.statusCode}</Badge>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge variant="outline">{log.scope}</Badge>
                  {log.ipAddress && <Badge variant="outline">{log.ipAddress}</Badge>}
                </div>
              </div>
            )) : (
              <p className="text-sm text-muted-foreground">Ainda não há chamadas auditadas para os tokens deste workspace.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
