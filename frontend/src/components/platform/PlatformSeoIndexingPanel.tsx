import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  fetchPlatformBlogIndexing,
  fetchPlatformBlogPostMetrics,
  publishPlatformBlogResource,
  savePlatformBlogIndexing,
} from '@/services/platform';
import type { BlogIndexingSettings } from '@/types';
import { Globe, Search, Send, type LucideIcon } from 'lucide-react';

export function PlatformSeoIndexingPanel() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<BlogIndexingSettings | null>(null);
  const [operationReason, setOperationReason] = useState('');
  const normalizedReason = operationReason.trim();
  const reasonValid = normalizedReason.length >= 8;
  const indexingQuery = useQuery({
    queryKey: ['platform-blog', 'indexing'],
    queryFn: fetchPlatformBlogIndexing,
    staleTime: 30_000,
  });
  const metricsQuery = useQuery({
    queryKey: ['platform-blog', 'posts', 'metrics'],
    queryFn: fetchPlatformBlogPostMetrics,
    staleTime: 15_000,
  });

  useEffect(() => {
    if (indexingQuery.data?.draft) {
      setDraft(indexingQuery.data.draft as BlogIndexingSettings);
    }
  }, [indexingQuery.data]);

  const saveMutation = useMutation({
    mutationFn: ({ payload, reason }: { payload: BlogIndexingSettings; reason: string }) =>
      savePlatformBlogIndexing(payload, reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform-blog', 'indexing'] });
      toast({ title: 'Indexação global salva.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao salvar indexação', description: error.message }),
  });

  const publishMutation = useMutation({
    mutationFn: (reason: string) => publishPlatformBlogResource('blog_indexing', reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform-blog', 'indexing'] });
      toast({ title: 'Documento de indexação publicado.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao publicar indexação', description: error.message }),
  });

  const metricValue = (value: number | null | undefined, suffix = '') => {
    if (metricsQuery.isPending) return '…';
    if (metricsQuery.isError) return 'Indisponível';
    if (metricsQuery.data?.dataState !== 'available' || value == null) return 'Dados insuficientes';
    return `${value}${suffix}`;
  };
  const metrics: Array<[string, string, LucideIcon]> = [
    ['Posts indexáveis', metricValue(metricsQuery.data?.indexable), Search],
    ['No sitemap', metricValue(metricsQuery.data?.includedInSitemap), Globe],
    ['No llms.txt', metricValue(metricsQuery.data?.includedInLlms), Send],
    ['Score médio SEO', metricValue(metricsQuery.data?.averageSeoScore, '/100'), Search],
  ];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">SEO & indexação</h2>
        <p className="text-sm text-muted-foreground">
          Controle de sitemap, robots, llms.txt, feeds e governança de indexação por documento.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {metrics.map(([title, value, Icon]) => (
          <Card key={String(title)}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-xs font-medium text-muted-foreground">{title}</CardTitle>
              <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold tabular-nums">{value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Política global</CardTitle>
          <CardDescription>Fonte automática para `sitemap.xml`, `robots.txt`, `rss.xml` e `llms.txt`.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">v{indexingQuery.data?.version ?? 1}</Badge>
            {indexingQuery.data?.publishedAt ? (
              <Badge variant="secondary">Publicado</Badge>
            ) : (
              <Badge variant="outline">Rascunho</Badge>
            )}
          </div>
          {draft ? (
            <>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>Site URL</Label>
                  <Input value={draft.siteUrl} onChange={(event) => setDraft({ ...draft, siteUrl: event.target.value })} />
                </div>
                <div className="space-y-1.5">
                  <Label>Resumo para LLMs</Label>
                  <Input value={draft.llmsSummary} onChange={(event) => setDraft({ ...draft, llmsSummary: event.target.value })} />
                </div>
              </div>

              <div className="grid gap-3 md:grid-cols-3">
                {[
                  ['Sitemap automático', 'sitemapEnabled'],
                  ['RSS habilitado', 'rssEnabled'],
                  ['llms.txt habilitado', 'llmsEnabled'],
                ].map(([label, key]) => (
                  <div key={key} className="flex items-center justify-between rounded-2xl border px-4 py-3">
                    <div>
                      <p className="text-sm font-medium">{label}</p>
                      <p className="text-xs text-muted-foreground">Saída pública gerada a partir do conteúdo publicado.</p>
                    </div>
                    <Switch
                      checked={Boolean(draft[key as keyof BlogIndexingSettings])}
                      onCheckedChange={(checked) => setDraft({ ...draft, [key]: checked })}
                    />
                  </div>
                ))}
              </div>

              <div className="space-y-1.5">
                <Label>robots.txt</Label>
                <Textarea rows={8} value={draft.robotsPolicy} onChange={(event) => setDraft({ ...draft, robotsPolicy: event.target.value })} />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="indexing-operation-reason">Motivo da alteração</Label>
                <Input
                  id="indexing-operation-reason"
                  value={operationReason}
                  onChange={(event) => setOperationReason(event.target.value)}
                  maxLength={500}
                  placeholder="Ex.: ajuste de indexação aprovado para o novo domínio"
                />
                <p className="text-xs text-muted-foreground">
                  Obrigatório, de 8 a 500 caracteres, e registrado na auditoria.
                </p>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => saveMutation.mutate({ payload: draft, reason: normalizedReason })} disabled={!reasonValid || saveMutation.isPending}>
                  Salvar rascunho
                </Button>
                <Button onClick={() => publishMutation.mutate(normalizedReason)} disabled={!reasonValid || publishMutation.isPending}>
                  Publicar indexação
                </Button>
              </div>
            </>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
