import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, LibraryBig, Loader2, Pencil, Plus, Save } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  archiveCreativeLibraryItem,
  fetchCreativeLibraryPage,
  saveCreativeLibraryItem,
  type CreativeChannel,
  type CreativeKind,
  type CreativeLibraryItem,
  type CreativeStatus,
} from '@/services/blackbox-studios';

const emptyForm = {
  id: '',
  name: '',
  kind: 'creative' as CreativeKind,
  channel: 'meta' as CreativeChannel,
  objective: '',
  status: 'draft' as CreativeStatus,
  tags: '',
  assetUrl: '',
  copyText: '',
  notes: '',
};

export default function BlackboxCreativeLibrary() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [form, setForm] = useState(emptyForm);
  const [filter, setFilter] = useState<'active' | CreativeStatus | 'all'>('active');
  const [offset, setOffset] = useState(0);
  const pageSize = 25;
  const library = useQuery({
    queryKey: ['blackbox', 'creative-library', filter, offset],
    queryFn: () => fetchCreativeLibraryPage({ status: filter, limit: pageSize, offset }),
  });
  useEffect(() => {
    if (!library.error) return;
    toast({
      title: 'Não foi possível carregar a biblioteca',
      description: (library.error as Error).message,
      variant: 'destructive',
    });
  }, [library.error, toast]);
  useEffect(() => {
    if (!library.data || offset === 0 || library.data.items.length > 0) return;
    setOffset(Math.max(0, offset - pageSize));
  }, [library.data, offset]);

  const save = useMutation({
    mutationFn: () => saveCreativeLibraryItem({
      id: form.id || undefined,
      name: form.name,
      kind: form.kind,
      channel: form.channel,
      objective: form.objective,
      status: form.status,
      tags: form.tags.split(',').map((tag) => tag.trim()).filter(Boolean),
      assetUrl: form.assetUrl || undefined,
      copyText: form.copyText || undefined,
      notes: form.notes || undefined,
      expectedEtag: library.data?.etag ?? undefined,
    }),
    onSuccess: async () => {
      setForm(emptyForm);
      setOffset(0);
      await queryClient.invalidateQueries({ queryKey: ['blackbox', 'creative-library'] });
      toast({ title: 'Biblioteca atualizada', description: 'Uma nova versão auditável foi registrada.' });
    },
    onError: (error: Error) => toast({
      title: 'Não foi possível salvar',
      description: error.message,
      variant: 'destructive',
    }),
  });
  const archive = useMutation({
    mutationFn: (itemId: string) => archiveCreativeLibraryItem(
      itemId,
      library.data?.etag ?? undefined,
    ),
    onSuccess: async () => {
      setOffset(0);
      await queryClient.invalidateQueries({ queryKey: ['blackbox', 'creative-library'] });
      toast({ title: 'Item arquivado' });
    },
  });
  const items = library.data?.items ?? [];

  const editItem = (item: CreativeLibraryItem) => setForm({
    id: item.id,
    name: item.name,
    kind: item.kind,
    channel: item.channel,
    objective: item.objective,
    status: item.status,
    tags: item.tags.join(', '),
    assetUrl: item.assetUrl ?? '',
    copyText: item.copyText ?? '',
    notes: item.notes ?? '',
  });

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-5 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <LibraryBig className="h-5 w-5 text-blue-600" />
            <h1 className="text-xl font-semibold">Biblioteca master de campanhas</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Criativos e campanhas de referência da plataforma. Acesso exclusivo do titular.
          </p>
        </div>
        <Badge variant="outline">Versão {library.data?.currentVersion ?? 0}</Badge>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(360px,0.75fr)_minmax(0,1.25fr)]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{form.id ? 'Editar item' : 'Novo item'}</CardTitle>
            <CardDescription>
              A publicação aqui organiza o acervo; não dispara anúncios nem mensagens.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="creative-name">Nome</Label>
              <Input id="creative-name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="space-y-2">
                <Label>Tipo</Label>
                <Select value={form.kind} onValueChange={(value: CreativeKind) => setForm({ ...form, kind: value })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="creative">Criativo</SelectItem>
                    <SelectItem value="campaign">Campanha</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Canal</Label>
                <Select value={form.channel} onValueChange={(value: CreativeChannel) => setForm({ ...form, channel: value })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="meta">Meta</SelectItem>
                    <SelectItem value="google">Google</SelectItem>
                    <SelectItem value="tiktok">TikTok</SelectItem>
                    <SelectItem value="linkedin">LinkedIn</SelectItem>
                    <SelectItem value="organic">Orgânico</SelectItem>
                    <SelectItem value="other">Outro</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Situação</Label>
                <Select value={form.status} onValueChange={(value: CreativeStatus) => setForm({ ...form, status: value })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="draft">Rascunho</SelectItem>
                    <SelectItem value="approved">Aprovado</SelectItem>
                    <SelectItem value="archived">Arquivado</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="creative-objective">Objetivo</Label>
              <Input id="creative-objective" value={form.objective} onChange={(event) => setForm({ ...form, objective: event.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="creative-tags">Etiquetas, separadas por vírgula</Label>
              <Input id="creative-tags" value={form.tags} onChange={(event) => setForm({ ...form, tags: event.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="creative-url">Link do ativo</Label>
              <Input id="creative-url" value={form.assetUrl} onChange={(event) => setForm({ ...form, assetUrl: event.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="creative-copy">Texto ou roteiro</Label>
              <Textarea id="creative-copy" value={form.copyText} onChange={(event) => setForm({ ...form, copyText: event.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="creative-notes">Notas internas</Label>
              <Textarea id="creative-notes" value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button disabled={form.name.trim().length < 2 || form.objective.trim().length < 2 || save.isPending} onClick={() => save.mutate()}>
                {save.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                Salvar versão
              </Button>
              {form.id && (
                <Button variant="outline" onClick={() => setForm(emptyForm)}>
                  <Plus className="mr-2 h-4 w-4" /> Novo item
                </Button>
              )}
            </div>
          </CardContent>
        </Card>

        <Card className="min-w-0">
          <CardHeader className="flex-row items-start justify-between gap-3">
            <div>
              <CardTitle className="text-base">Acervo da plataforma</CardTitle>
              <CardDescription>
                {library.data ? `${library.data.total} item(ns) no filtro atual.` : 'Carregando acervo...'}
              </CardDescription>
            </div>
            <Select value={filter} onValueChange={(value) => {
              setFilter(value as 'active' | CreativeStatus | 'all');
              setOffset(0);
            }}>
              <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="active">Ativos</SelectItem>
                <SelectItem value="approved">Aprovados</SelectItem>
                <SelectItem value="draft">Rascunhos</SelectItem>
                <SelectItem value="archived">Arquivados</SelectItem>
                <SelectItem value="all">Todos</SelectItem>
              </SelectContent>
            </Select>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Nome</TableHead>
                  <TableHead>Canal</TableHead>
                  <TableHead>Objetivo</TableHead>
                  <TableHead>Situação</TableHead>
                  <TableHead className="text-right">Ações</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {library.isLoading && (
                  <TableRow>
                    <TableCell colSpan={5} className="py-10 text-center text-muted-foreground">
                      <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
                      Carregando itens do acervo...
                    </TableCell>
                  </TableRow>
                )}
                {library.error && (
                  <TableRow>
                    <TableCell colSpan={5} className="py-10 text-center text-destructive">
                      Não foi possível carregar o acervo.
                    </TableCell>
                  </TableRow>
                )}
                {items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell>
                      <div className="font-medium">{item.name}</div>
                      <div className="text-xs text-muted-foreground">{item.kind === 'campaign' ? 'Campanha' : 'Criativo'}</div>
                    </TableCell>
                    <TableCell>{item.channel}</TableCell>
                    <TableCell className="max-w-64 whitespace-normal">{item.objective}</TableCell>
                    <TableCell><Badge variant="outline">{item.status}</Badge></TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        <Button size="icon" variant="ghost" title="Editar" onClick={() => editItem(item)}>
                          <Pencil className="h-4 w-4" />
                        </Button>
                        {item.status !== 'archived' && (
                          <Button size="icon" variant="ghost" title="Arquivar" onClick={() => archive.mutate(item.id)}>
                            <Archive className="h-4 w-4" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
                {!library.isLoading && !library.error && items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="py-10 text-center text-muted-foreground">
                      Nenhum item encontrado neste filtro.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
          {library.data && library.data.total > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t px-6 py-4 text-sm text-muted-foreground">
              <span>
                {offset + 1}–{Math.min(offset + items.length, library.data.total)} de {library.data.total}
              </span>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={offset === 0 || library.isFetching}
                  onClick={() => setOffset(Math.max(0, offset - pageSize))}
                >
                  Anterior
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!library.data.hasMore || library.isFetching}
                  onClick={() => setOffset(offset + pageSize)}
                >
                  Próxima
                </Button>
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
