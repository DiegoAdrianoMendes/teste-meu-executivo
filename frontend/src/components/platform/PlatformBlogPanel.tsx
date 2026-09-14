import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  assistPlatformBlogPost,
  autofillPlatformBlogSeo,
  createPlatformBlogPost,
  duplicatePlatformBlogPost,
  fetchPlatformBlogPost,
  fetchPlatformBlogPostsPage,
  fetchPlatformBlogSettings,
  fetchPlatformBlogTaxonomy,
  generatePlatformBlogCover,
  publishPlatformBlogPost,
  publishPlatformBlogResource,
  savePlatformBlogPost,
  savePlatformBlogSettings,
  savePlatformBlogTaxonomy,
  schedulePlatformBlogPost,
  archivePlatformBlogPost,
} from '@/services/platform';
import type { BlogBlock, BlogPost, BlogSettings, BlogTaxonomy, IndexingPolicy, PlatformDocumentEnvelope } from '@/types';
import { Bot, CalendarClock, Copy, Plus, Save, Send, Sparkles, Wand2 } from 'lucide-react';

function splitCsv(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function joinCsv(items?: string[] | null): string {
  return (items ?? []).join(', ');
}

function emptyPostDraft(): Partial<BlogPost> {
  return {
    title: '',
    subtitle: '',
    excerpt: '',
    authorName: 'Equipe Meu Executivo',
    authorRole: 'Editorial',
    categories: [],
    tags: [],
    contentBlocks: [
      { type: 'heading', content: 'Introdução', level: 2, items: [], metadata: {} },
      { type: 'paragraph', content: 'Comece seu artigo aqui.', items: [], metadata: {} },
    ],
    seo: {
      title: '',
      description: '',
      canonicalUrl: '',
      ogImageUrl: '',
      keywords: [],
      socialCopy: '',
    },
    indexing: {
      indexable: true,
      includeInSitemap: true,
      includeInRss: true,
      includeInLlms: true,
    },
  };
}

function DocHeader<T>({
  title,
  envelope,
  onSave,
  onPublish,
  saving,
  publishing,
  disabled,
}: {
  title: string;
  envelope?: PlatformDocumentEnvelope<T> | null;
  onSave: () => void;
  onPublish: () => void;
  saving?: boolean;
  publishing?: boolean;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <h3 className="text-base font-semibold">{title}</h3>
          {envelope ? <Badge variant="outline">v{envelope.version}</Badge> : null}
        </div>
        {envelope ? (
          <p className="text-xs text-muted-foreground">
            Atualizado em {new Date(envelope.updatedAt).toLocaleString('pt-BR')}
            {envelope.publishedAt ? ` · Publicado em ${new Date(envelope.publishedAt).toLocaleString('pt-BR')}` : ' · Ainda não publicado'}
          </p>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={onSave} disabled={saving || disabled}>
          <Save className="mr-1.5 h-3.5 w-3.5" />
          Salvar rascunho
        </Button>
        <Button size="sm" onClick={onPublish} disabled={publishing || disabled}>
          <Send className="mr-1.5 h-3.5 w-3.5" />
          Publicar
        </Button>
      </div>
    </div>
  );
}

export function PlatformBlogPanel() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [selectedPostId, setSelectedPostId] = useState<string | null>(null);
  const [postDraft, setPostDraft] = useState<Partial<BlogPost> | null>(null);
  const [scheduleAt, setScheduleAt] = useState('');
  const [settingsDraft, setSettingsDraft] = useState<BlogSettings | null>(null);
  const [taxonomyDraft, setTaxonomyDraft] = useState<BlogTaxonomy | null>(null);
  const [operationReason, setOperationReason] = useState('');
  const [postPage, setPostPage] = useState(0);
  const postPageSize = 25;
  const normalizedReason = operationReason.trim();
  const reasonValid = normalizedReason.length >= 8;
  const postsQuery = useQuery({
    queryKey: ['platform-blog', 'posts', postPage],
    queryFn: () => fetchPlatformBlogPostsPage(postPageSize, postPage * postPageSize),
    staleTime: 15_000,
  });
  const postQuery = useQuery({
    queryKey: ['platform-blog', 'post', selectedPostId],
    queryFn: () => fetchPlatformBlogPost(selectedPostId!),
    enabled: Boolean(selectedPostId),
    staleTime: 15_000,
  });
  const settingsQuery = useQuery({
    queryKey: ['platform-blog', 'settings'],
    queryFn: fetchPlatformBlogSettings,
    staleTime: 30_000,
  });
  const taxonomyQuery = useQuery({
    queryKey: ['platform-blog', 'taxonomy'],
    queryFn: fetchPlatformBlogTaxonomy,
    staleTime: 30_000,
  });

  useEffect(() => {
    const posts = postsQuery.data?.items ?? [];
    if ((!selectedPostId || !posts.some((post) => post.id === selectedPostId)) && posts[0]) {
      setSelectedPostId(posts[0].id);
    } else if (!posts.length) {
      setSelectedPostId(null);
      setPostDraft(null);
    }
  }, [postsQuery.data, selectedPostId]);

  useEffect(() => {
    if (postQuery.data) {
      setPostDraft(postQuery.data);
      setScheduleAt(postQuery.data.scheduledFor ? postQuery.data.scheduledFor.slice(0, 16) : '');
    }
  }, [postQuery.data]);

  useEffect(() => {
    if (settingsQuery.data?.draft) {
      setSettingsDraft(settingsQuery.data.draft as BlogSettings);
    }
  }, [settingsQuery.data]);

  useEffect(() => {
    if (taxonomyQuery.data?.draft) {
      setTaxonomyDraft(taxonomyQuery.data.draft as BlogTaxonomy);
    }
  }, [taxonomyQuery.data]);

  const invalidateBlog = () => {
    void queryClient.invalidateQueries({ queryKey: ['platform-blog'] });
    void queryClient.invalidateQueries({ queryKey: ['public-blog'] });
  };

  const createMutation = useMutation({
    mutationFn: ({ payload, reason }: { payload: Record<string, unknown>; reason: string }) =>
      createPlatformBlogPost(payload, reason),
    onSuccess: (post) => {
      invalidateBlog();
      setSelectedPostId(post.id);
      toast({ title: 'Post criado no CMS editorial.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao criar post', description: error.message }),
  });
  const saveMutation = useMutation({
    mutationFn: ({ postId, payload, reason }: { postId: string; payload: Record<string, unknown>; reason: string }) =>
      savePlatformBlogPost(postId, payload, reason),
    onSuccess: () => {
      invalidateBlog();
      toast({ title: 'Rascunho salvo.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao salvar rascunho', description: error.message }),
  });
  const publishMutation = useMutation({
    mutationFn: ({ postId, reason }: { postId: string; reason: string }) =>
      publishPlatformBlogPost(postId, reason),
    onSuccess: () => {
      invalidateBlog();
      toast({ title: 'Post publicado.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao publicar', description: error.message }),
  });
  const scheduleMutation = useMutation({
    mutationFn: ({ postId, publishAt, reason }: { postId: string; publishAt: string; reason: string }) =>
      schedulePlatformBlogPost(postId, publishAt, reason),
    onSuccess: () => {
      invalidateBlog();
      toast({ title: 'Post agendado.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao agendar', description: error.message }),
  });
  const duplicateMutation = useMutation({
    mutationFn: ({ postId, reason }: { postId: string; reason: string }) =>
      duplicatePlatformBlogPost(postId, reason),
    onSuccess: (post) => {
      invalidateBlog();
      setSelectedPostId(post.id);
      toast({ title: 'Post duplicado.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao duplicar', description: error.message }),
  });
  const archiveMutation = useMutation({
    mutationFn: ({ postId, reason }: { postId: string; reason: string }) =>
      archivePlatformBlogPost(postId, reason),
    onSuccess: () => {
      invalidateBlog();
      toast({ title: 'Post arquivado.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao arquivar', description: error.message }),
  });
  const seoMutation = useMutation({
    mutationFn: ({ postId, reason }: { postId: string; reason: string }) =>
      autofillPlatformBlogSeo(postId, reason),
    onSuccess: (payload) => {
      setPostDraft((current) => current ? ({
        ...current,
        slug: payload.slug,
        excerpt: payload.excerpt,
        seo: payload.seo,
        seoAudit: payload.seoAudit,
      }) : current);
      toast({ title: 'SEO preenchido automaticamente.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao rodar SEO', description: error.message }),
  });
  const assistMutation = useMutation({
    mutationFn: ({ postId, action, reason }: { postId: string; action: string; reason: string }) =>
      assistPlatformBlogPost(postId, { action }, reason),
    onSuccess: (payload) => {
      setPostDraft((current) => current ? ({
        ...current,
        contentBlocks: payload.contentBlocks,
        excerpt: payload.excerpt ?? current.excerpt,
        seo: current.seo ? { ...current.seo, keywords: payload.suggestedKeywords } : current.seo,
      }) : current);
      toast({ title: 'Sugestão editorial aplicada ao rascunho.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro no assistente editorial', description: error.message }),
  });
  const coverMutation = useMutation({
    mutationFn: ({ postId, reason }: { postId: string; reason: string }) =>
      generatePlatformBlogCover(postId, reason),
    onSuccess: (payload) => {
      setPostDraft((current) => current ? ({
        ...current,
        coverMedia: {
          assetId: payload.asset.id,
          url: payload.asset.publicUrl,
          alt: payload.asset.altText,
          mimeType: payload.asset.mimeType,
        },
        ogMedia: {
          assetId: payload.asset.id,
          url: payload.asset.publicUrl,
          alt: payload.asset.altText,
          mimeType: payload.asset.mimeType,
        },
      }) : current);
      toast({ title: 'Capa editorial gerada.', description: `Provider: ${payload.providerUsed}` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao gerar capa', description: error.message }),
  });

  const saveSettingsMutation = useMutation({
    mutationFn: ({ payload, reason }: { payload: BlogSettings; reason: string }) =>
      savePlatformBlogSettings(payload, reason),
    onSuccess: () => {
      invalidateBlog();
      toast({ title: 'Configurações editoriais salvas.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao salvar configurações', description: error.message }),
  });
  const saveTaxonomyMutation = useMutation({
    mutationFn: ({ payload, reason }: { payload: BlogTaxonomy; reason: string }) =>
      savePlatformBlogTaxonomy(payload, reason),
    onSuccess: () => {
      invalidateBlog();
      toast({ title: 'Taxonomia salva.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao salvar taxonomia', description: error.message }),
  });
  const publishDocMutation = useMutation({
    mutationFn: ({ resource, reason }: { resource: 'blog_settings' | 'blog_taxonomy'; reason: string }) =>
      publishPlatformBlogResource(resource, reason),
    onSuccess: (_, variables) => {
      invalidateBlog();
      toast({ title: `Documento publicado: ${variables.resource}.` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao publicar documento', description: error.message }),
  });

  const selectedPost = useMemo(
    () => postsQuery.data?.items.find((post) => post.id === selectedPostId) ?? postsQuery.data?.items[0] ?? null,
    [postsQuery.data, selectedPostId],
  );

  const saveCurrentPost = () => {
    if (!selectedPostId || !postDraft) return;
    saveMutation.mutate({
      postId: selectedPostId,
      payload: {
        title: postDraft.title,
        slug: postDraft.slug,
        subtitle: postDraft.subtitle,
        excerpt: postDraft.excerpt,
        authorName: postDraft.authorName,
        authorRole: postDraft.authorRole,
        categories: postDraft.categories ?? [],
        tags: postDraft.tags ?? [],
        featured: postDraft.featured ?? false,
        contentBlocks: postDraft.contentBlocks ?? [],
        coverMedia: postDraft.coverMedia,
        ogMedia: postDraft.ogMedia,
        seo: postDraft.seo,
        indexing: postDraft.indexing,
        aiConfig: postDraft.aiConfig ?? {},
      },
      reason: normalizedReason,
    });
  };

  const updateBlock = (index: number, patch: Partial<BlogBlock>) => {
    setPostDraft((current) => {
      if (!current) return current;
      const nextBlocks = [...(current.contentBlocks ?? [])];
      nextBlocks[index] = {
        ...nextBlocks[index],
        ...patch,
        items: patch.items ?? nextBlocks[index]?.items ?? [],
        metadata: patch.metadata ?? nextBlocks[index]?.metadata ?? {},
      };
      return { ...current, contentBlocks: nextBlocks };
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-lg font-semibold">Blog & editorial</h2>
          <p className="text-sm text-muted-foreground">
            CMS avançado para rascunho, publicação, agendamento, SEO one-click e operação editorial da marca.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() =>
              createMutation.mutate({
                payload: {
                  title: 'Novo artigo Meu Executivo',
                  subtitle: 'Rascunho inicial do CMS editorial.',
                  categories: ['Atendimento jurídico'],
                  tags: ['klm advogados'],
                  contentBlocks: emptyPostDraft().contentBlocks,
                },
                reason: normalizedReason,
              })
            }
            disabled={!reasonValid || createMutation.isPending}
          >
            <Plus className="mr-2 h-4 w-4" />
            Novo post
          </Button>
        </div>
      </div>

      <div className="space-y-1.5 rounded-2xl border bg-muted/20 p-4">
        <Label htmlFor="blog-operation-reason">Motivo da alteração</Label>
        <Input
          id="blog-operation-reason"
          value={operationReason}
          onChange={(event) => setOperationReason(event.target.value)}
          maxLength={500}
          placeholder="Ex.: revisão editorial aprovada para a campanha de agosto"
        />
        <p className="text-xs text-muted-foreground">
          Obrigatório, de 8 a 500 caracteres. O motivo integra a auditoria e o histórico.
        </p>
      </div>

      <Tabs defaultValue="posts" className="space-y-4">
        <TabsList className="flex h-auto flex-wrap justify-start gap-2 bg-transparent p-0">
          <TabsTrigger value="posts">Posts</TabsTrigger>
          <TabsTrigger value="settings">Configurações</TabsTrigger>
          <TabsTrigger value="taxonomy">Categorias & tags</TabsTrigger>
        </TabsList>

        <TabsContent value="posts" className="space-y-4">
          <div className="grid gap-4 xl:grid-cols-[340px,minmax(0,1fr)]">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Fila editorial</CardTitle>
                <CardDescription>Rascunhos, posts agendados, publicados e arquivados.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {(postsQuery.data?.items ?? []).map((post) => (
                  <button
                    key={post.id}
                    type="button"
                    onClick={() => setSelectedPostId(post.id)}
                    className={`w-full rounded-2xl border p-4 text-left transition-colors ${selectedPost?.id === post.id ? 'border-primary bg-primary/5' : 'hover:bg-muted/40'}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="line-clamp-2 text-sm font-semibold">{post.title}</p>
                        <p className="mt-1 truncate text-xs tabular-nums text-muted-foreground">{post.authorName} · score SEO {post.seoAudit.score}</p>
                      </div>
                      <Badge className="shrink-0" variant={post.status === 'published' ? 'secondary' : post.status === 'scheduled' ? 'outline' : 'default'}>
                        {post.status}
                      </Badge>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {post.categories.slice(0, 2).map((category) => (
                        <Badge key={category} variant="outline">{category}</Badge>
                      ))}
                    </div>
                  </button>
                ))}
                {postsQuery.data ? (
                  <div className="space-y-2 border-t pt-3">
                    <p className="text-xs text-muted-foreground">
                      {postsQuery.data.total
                        ? `${postPage * postPageSize + 1}–${Math.min((postPage + 1) * postPageSize, postsQuery.data.total)} de ${postsQuery.data.total}`
                        : 'Nenhum post encontrado'}
                    </p>
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setPostPage((current) => Math.max(0, current - 1))}
                        disabled={postPage === 0 || postsQuery.isFetching}
                      >
                        Anterior
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setPostPage((current) => current + 1)}
                        disabled={!postsQuery.data.hasMore || postsQuery.isFetching}
                      >
                        Próxima
                      </Button>
                    </div>
                  </div>
                ) : null}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="space-y-3">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                  <div>
                    <CardTitle className="text-base">Editor avançado</CardTitle>
                    <CardDescription>
                      Estrutura por blocos, SEO técnico, imagem de capa fallback, publicação imediata e agendamento.
                    </CardDescription>
                  </div>
                  {selectedPost ? (
                    <div className="flex flex-wrap gap-2">
                      <Button variant="outline" size="sm" onClick={saveCurrentPost} disabled={!selectedPostId || !reasonValid || saveMutation.isPending}>
                        <Save className="mr-1.5 h-3.5 w-3.5" />
                        Salvar
                      </Button>
                      <Button size="sm" onClick={() => selectedPostId && publishMutation.mutate({ postId: selectedPostId, reason: normalizedReason })} disabled={!selectedPostId || !reasonValid || publishMutation.isPending}>
                        <Send className="mr-1.5 h-3.5 w-3.5" />
                        Publicar agora
                      </Button>
                    </div>
                  ) : null}
                </div>
              </CardHeader>
              <CardContent className="space-y-5">
                {postDraft ? (
                  <>
                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="space-y-1.5 md:col-span-2">
                        <Label>Título do artigo</Label>
                        <Input value={postDraft.title ?? ''} onChange={(event) => setPostDraft({ ...postDraft, title: event.target.value })} />
                      </div>
                      <div className="space-y-1.5 md:col-span-2">
                        <Label>Subtítulo</Label>
                        <Textarea rows={2} value={postDraft.subtitle ?? ''} onChange={(event) => setPostDraft({ ...postDraft, subtitle: event.target.value })} />
                      </div>
                      <div className="space-y-1.5">
                        <Label>Slug</Label>
                        <Input value={postDraft.slug ?? ''} onChange={(event) => setPostDraft({ ...postDraft, slug: event.target.value })} />
                      </div>
                      <div className="space-y-1.5">
                        <Label>Status editorial</Label>
                        <Input value={selectedPost?.status ?? 'draft'} disabled />
                      </div>
                      <div className="space-y-1.5">
                        <Label>Autor</Label>
                        <Input value={postDraft.authorName ?? ''} onChange={(event) => setPostDraft({ ...postDraft, authorName: event.target.value })} />
                      </div>
                      <div className="space-y-1.5">
                        <Label>Cargo</Label>
                        <Input value={postDraft.authorRole ?? ''} onChange={(event) => setPostDraft({ ...postDraft, authorRole: event.target.value })} />
                      </div>
                      <div className="space-y-1.5">
                        <Label>Categorias</Label>
                        <Input value={joinCsv(postDraft.categories)} onChange={(event) => setPostDraft({ ...postDraft, categories: splitCsv(event.target.value) })} />
                      </div>
                      <div className="space-y-1.5">
                        <Label>Tags</Label>
                        <Input value={joinCsv(postDraft.tags)} onChange={(event) => setPostDraft({ ...postDraft, tags: splitCsv(event.target.value) })} />
                      </div>
                      <div className="space-y-1.5 md:col-span-2">
                        <Label>Excerpt</Label>
                        <Textarea rows={3} value={postDraft.excerpt ?? ''} onChange={(event) => setPostDraft({ ...postDraft, excerpt: event.target.value })} />
                      </div>
                    </div>

                    <div className="rounded-2xl border bg-muted/30 p-4">
                      <div className="flex flex-wrap gap-2">
                        <Button variant="outline" size="sm" onClick={() => selectedPostId && seoMutation.mutate({ postId: selectedPostId, reason: normalizedReason })} disabled={!reasonValid}>
                          <Sparkles className="mr-1.5 h-3.5 w-3.5" />
                          SEO one-click
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => selectedPostId && assistMutation.mutate({ postId: selectedPostId, action: 'outline', reason: normalizedReason })} disabled={!reasonValid}>
                          <Bot className="mr-1.5 h-3.5 w-3.5" />
                          Gerar outline
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => selectedPostId && assistMutation.mutate({ postId: selectedPostId, action: 'faq', reason: normalizedReason })} disabled={!reasonValid}>
                          <Bot className="mr-1.5 h-3.5 w-3.5" />
                          Sugerir FAQ
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => selectedPostId && coverMutation.mutate({ postId: selectedPostId, reason: normalizedReason })} disabled={!reasonValid}>
                          <Wand2 className="mr-1.5 h-3.5 w-3.5" />
                          Gerar capa IA
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => selectedPostId && duplicateMutation.mutate({ postId: selectedPostId, reason: normalizedReason })} disabled={!reasonValid}>
                          <Copy className="mr-1.5 h-3.5 w-3.5" />
                          Duplicar
                        </Button>
                      </div>
                      <div className="mt-4 grid gap-4 md:grid-cols-[1fr,220px]">
                        <div className="rounded-xl bg-background p-4">
                          <p className="text-sm font-medium">SEO atual</p>
                          <p className="mt-2 text-xs text-muted-foreground">Score {postDraft.seoAudit?.score ?? 0}/100 · {postDraft.seoAudit?.readingTimeMinutes ?? 1} min de leitura</p>
                          <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
                            {(postDraft.seoAudit?.suggestions ?? []).slice(0, 4).map((suggestion) => (
                              <li key={suggestion}>• {suggestion}</li>
                            ))}
                          </ul>
                        </div>
                        <div className="space-y-2 rounded-xl bg-background p-4">
                          <p className="text-sm font-medium">Agendamento</p>
                          <Input type="datetime-local" value={scheduleAt} onChange={(event) => setScheduleAt(event.target.value)} />
                          <Button
                            size="sm"
                            className="w-full"
                            onClick={() => selectedPostId && scheduleAt && scheduleMutation.mutate({ postId: selectedPostId, publishAt: new Date(scheduleAt).toISOString(), reason: normalizedReason })}
                            disabled={!reasonValid || !scheduleAt}
                          >
                            <CalendarClock className="mr-1.5 h-3.5 w-3.5" />
                            Agendar publicação
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            className="w-full"
                            onClick={() => selectedPostId && archiveMutation.mutate({ postId: selectedPostId, reason: normalizedReason })}
                            disabled={!reasonValid}
                          >
                            Arquivar
                          </Button>
                        </div>
                      </div>
                    </div>

                    <Tabs defaultValue="content" className="space-y-4">
                      <TabsList className="flex h-auto flex-wrap justify-start gap-2 bg-transparent p-0">
                        <TabsTrigger value="content">Conteúdo</TabsTrigger>
                        <TabsTrigger value="seo">SEO</TabsTrigger>
                        <TabsTrigger value="indexing">Indexação</TabsTrigger>
                      </TabsList>

                      <TabsContent value="content" className="space-y-4">
                        {(postDraft.contentBlocks ?? []).map((block, index) => (
                          <div key={`${block.type}-${index}`} className="rounded-2xl border p-4">
                            <div className="grid gap-4 md:grid-cols-2">
                              <div className="space-y-1.5">
                                <Label>Tipo de bloco</Label>
                                <Select value={block.type} onValueChange={(value) => updateBlock(index, { type: value })}>
                                  <SelectTrigger><SelectValue /></SelectTrigger>
                                  <SelectContent>
                                    <SelectItem value="paragraph">Parágrafo</SelectItem>
                                    <SelectItem value="heading">Heading</SelectItem>
                                    <SelectItem value="quote">Quote</SelectItem>
                                    <SelectItem value="list">Lista</SelectItem>
                                    <SelectItem value="callout">Callout</SelectItem>
                                    <SelectItem value="faq">FAQ</SelectItem>
                                  </SelectContent>
                                </Select>
                              </div>
                              <div className="space-y-1.5">
                                <Label>Título do bloco</Label>
                                <Input value={block.title ?? ''} onChange={(event) => updateBlock(index, { title: event.target.value })} />
                              </div>
                              <div className="space-y-1.5 md:col-span-2">
                                <Label>Conteúdo</Label>
                                <Textarea rows={block.type === 'paragraph' ? 5 : 3} value={block.content ?? ''} onChange={(event) => updateBlock(index, { content: event.target.value })} />
                              </div>
                              <div className="space-y-1.5 md:col-span-2">
                                <Label>Itens (um por linha)</Label>
                                <Textarea rows={4} value={(block.items ?? []).join('\n')} onChange={(event) => updateBlock(index, { items: event.target.value.split('\n').map((item) => item.trim()).filter(Boolean) })} />
                              </div>
                            </div>
                            <div className="mt-4 flex justify-end">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() =>
                                  setPostDraft((current) =>
                                    current
                                      ? { ...current, contentBlocks: (current.contentBlocks ?? []).filter((_, currentIndex) => currentIndex !== index) }
                                      : current,
                                  )
                                }
                              >
                                Remover bloco
                              </Button>
                            </div>
                          </div>
                        ))}
                        <Button
                          variant="outline"
                          onClick={() =>
                            setPostDraft((current) =>
                              current
                                ? {
                                    ...current,
                                    contentBlocks: [
                                      ...(current.contentBlocks ?? []),
                                      { type: 'paragraph', content: '', items: [], metadata: {} },
                                    ],
                                  }
                                : current,
                            )
                          }
                        >
                          <Plus className="mr-2 h-4 w-4" />
                          Adicionar bloco
                        </Button>
                      </TabsContent>

                      <TabsContent value="seo" className="space-y-4">
                        <div className="grid gap-4 md:grid-cols-2">
                          <div className="space-y-1.5 md:col-span-2">
                            <Label>Meta title</Label>
                            <Input
                              value={postDraft.seo?.title ?? ''}
                              onChange={(event) => setPostDraft({
                                ...postDraft,
                                seo: { ...(postDraft.seo ?? { title: '', description: '', keywords: [] }), title: event.target.value },
                              })}
                            />
                          </div>
                          <div className="space-y-1.5 md:col-span-2">
                            <Label>Meta description</Label>
                            <Textarea
                              rows={3}
                              value={postDraft.seo?.description ?? ''}
                              onChange={(event) => setPostDraft({
                                ...postDraft,
                                seo: { ...(postDraft.seo ?? { title: '', description: '', keywords: [] }), description: event.target.value },
                              })}
                            />
                          </div>
                          <div className="space-y-1.5">
                            <Label>Canonical</Label>
                            <Input
                              value={postDraft.seo?.canonicalUrl ?? ''}
                              onChange={(event) => setPostDraft({
                                ...postDraft,
                                seo: { ...(postDraft.seo ?? { title: '', description: '', keywords: [] }), canonicalUrl: event.target.value },
                              })}
                            />
                          </div>
                          <div className="space-y-1.5">
                            <Label>OG image</Label>
                            <Input
                              value={postDraft.seo?.ogImageUrl ?? ''}
                              onChange={(event) => setPostDraft({
                                ...postDraft,
                                seo: { ...(postDraft.seo ?? { title: '', description: '', keywords: [] }), ogImageUrl: event.target.value },
                              })}
                            />
                          </div>
                          <div className="space-y-1.5">
                            <Label>Keywords</Label>
                            <Input
                              value={joinCsv(postDraft.seo?.keywords)}
                              onChange={(event) => setPostDraft({
                                ...postDraft,
                                seo: { ...(postDraft.seo ?? { title: '', description: '', keywords: [] }), keywords: splitCsv(event.target.value) },
                              })}
                            />
                          </div>
                          <div className="space-y-1.5">
                            <Label>Social copy</Label>
                            <Input
                              value={postDraft.seo?.socialCopy ?? ''}
                              onChange={(event) => setPostDraft({
                                ...postDraft,
                                seo: { ...(postDraft.seo ?? { title: '', description: '', keywords: [] }), socialCopy: event.target.value },
                              })}
                            />
                          </div>
                        </div>
                      </TabsContent>

                      <TabsContent value="indexing" className="space-y-4">
                        <div className="grid gap-3 md:grid-cols-2">
                          {[
                            ['Indexável', 'indexable'],
                            ['Entrar no sitemap', 'includeInSitemap'],
                            ['Entrar no RSS', 'includeInRss'],
                            ['Entrar no llms.txt', 'includeInLlms'],
                          ].map(([label, key]) => (
                            <div key={key} className="flex items-center justify-between rounded-2xl border px-4 py-3">
                              <div>
                                <p className="text-sm font-medium">{label}</p>
                                <p className="text-xs text-muted-foreground">Controla visibilidade e descoberta automática.</p>
                              </div>
                              <Switch
                                checked={Boolean(postDraft.indexing?.[key as keyof IndexingPolicy])}
                                onCheckedChange={(checked) => setPostDraft({
                                  ...postDraft,
                                  indexing: {
                                    ...(postDraft.indexing ?? {
                                      indexable: true,
                                      includeInSitemap: true,
                                      includeInRss: true,
                                      includeInLlms: true,
                                    }),
                                    [key]: checked,
                                  },
                                })}
                              />
                            </div>
                          ))}
                        </div>
                      </TabsContent>
                    </Tabs>
                  </>
                ) : (
                  <div className="rounded-2xl border border-dashed p-8 text-sm text-muted-foreground">
                    Selecione um post na fila editorial ou crie um novo para abrir o editor.
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="settings" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Configurações globais do blog</CardTitle>
              <CardDescription>Nome da página, CTA comercial, provider de IA editorial e SEO do índice.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <DocHeader
                title="Documento editorial"
                envelope={settingsQuery.data}
                onSave={() => settingsDraft && saveSettingsMutation.mutate({ payload: settingsDraft, reason: normalizedReason })}
                onPublish={() => publishDocMutation.mutate({ resource: 'blog_settings', reason: normalizedReason })}
                saving={saveSettingsMutation.isPending}
                publishing={publishDocMutation.isPending}
                disabled={!reasonValid}
              />
              {settingsDraft ? (
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label>Título da página</Label>
                    <Input value={settingsDraft.pageTitle} onChange={(event) => setSettingsDraft({ ...settingsDraft, pageTitle: event.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>CTA label</Label>
                    <Input value={settingsDraft.ctaLabel} onChange={(event) => setSettingsDraft({ ...settingsDraft, ctaLabel: event.target.value })} />
                  </div>
                  <div className="space-y-1.5 md:col-span-2">
                    <Label>Intro do blog</Label>
                    <Textarea rows={3} value={settingsDraft.intro} onChange={(event) => setSettingsDraft({ ...settingsDraft, intro: event.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>CTA URL</Label>
                    <Input value={settingsDraft.ctaUrl} onChange={(event) => setSettingsDraft({ ...settingsDraft, ctaUrl: event.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Provider de texto</Label>
                    <Input value={settingsDraft.aiProviderText} onChange={(event) => setSettingsDraft({ ...settingsDraft, aiProviderText: event.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Provider de imagem</Label>
                    <Input value={settingsDraft.aiProviderImage} onChange={(event) => setSettingsDraft({ ...settingsDraft, aiProviderImage: event.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Meta title</Label>
                    <Input value={settingsDraft.seo.title} onChange={(event) => setSettingsDraft({ ...settingsDraft, seo: { ...settingsDraft.seo, title: event.target.value } })} />
                  </div>
                  <div className="space-y-1.5 md:col-span-2">
                    <Label>Meta description</Label>
                    <Textarea rows={3} value={settingsDraft.seo.description} onChange={(event) => setSettingsDraft({ ...settingsDraft, seo: { ...settingsDraft.seo, description: event.target.value } })} />
                  </div>
                </div>
              ) : null}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="taxonomy" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Taxonomia editorial</CardTitle>
              <CardDescription>Categorias e tags globais disponíveis para o blog premium da marca.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <DocHeader
                title="Categorias & tags"
                envelope={taxonomyQuery.data}
                onSave={() => taxonomyDraft && saveTaxonomyMutation.mutate({ payload: taxonomyDraft, reason: normalizedReason })}
                onPublish={() => publishDocMutation.mutate({ resource: 'blog_taxonomy', reason: normalizedReason })}
                saving={saveTaxonomyMutation.isPending}
                publishing={publishDocMutation.isPending}
                disabled={!reasonValid}
              />
              {taxonomyDraft ? (
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label>Categorias</Label>
                    <Textarea rows={8} value={(taxonomyDraft.categories ?? []).join('\n')} onChange={(event) => setTaxonomyDraft({ ...taxonomyDraft, categories: event.target.value.split('\n').map((item) => item.trim()).filter(Boolean) })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Tags</Label>
                    <Textarea rows={8} value={(taxonomyDraft.tags ?? []).join('\n')} onChange={(event) => setTaxonomyDraft({ ...taxonomyDraft, tags: event.target.value.split('\n').map((item) => item.trim()).filter(Boolean) })} />
                  </div>
                </div>
              ) : null}
              <Separator />
              <div className="rounded-2xl border bg-muted/20 p-4 text-sm text-muted-foreground">
                Use categorias para pilares editoriais e tags para entidades recorrentes de SEO. O mesmo catálogo alimenta o índice público e o editor de posts.
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
