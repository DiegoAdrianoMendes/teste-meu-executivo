import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { confirmPlatformBlogMediaAsset, createPlatformBlogMediaAsset, fetchPlatformBlogMediaAssetsPage } from '@/services/platform';
import { Image as ImageIcon, UploadCloud, Video, type LucideIcon } from 'lucide-react';
import type { SiteMediaAsset } from '@/types';

export function PlatformMediaLibraryPanel() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [label, setLabel] = useState('');
  const [altText, setAltText] = useState('');
  const [caption, setCaption] = useState('');
  const [operationReason, setOperationReason] = useState('');
  const [assetPage, setAssetPage] = useState(0);
  const assetPageSize = 24;
  const normalizedReason = operationReason.trim();
  const reasonValid = normalizedReason.length >= 8;

  const assetsQuery = useQuery({
    queryKey: ['platform-blog', 'media', assetPage],
    queryFn: () => fetchPlatformBlogMediaAssetsPage(assetPageSize, assetPage * assetPageSize),
    staleTime: 15_000,
  });

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!selectedFile) throw new Error('Selecione um arquivo para upload.');
      const presign = await createPlatformBlogMediaAsset(
        {
          label: label || selectedFile.name,
          filename: selectedFile.name,
          mimeType: selectedFile.type || 'application/octet-stream',
          altText: altText || null,
          caption: caption || null,
          source: 'upload',
        },
        normalizedReason,
      );

      const uploadResponse = await fetch(presign.uploadUrl, {
        method: 'PUT',
        headers: {
          'Content-Type': selectedFile.type || 'application/octet-stream',
        },
        body: selectedFile,
      });

      if (!uploadResponse.ok) {
        throw new Error('Falha ao subir o arquivo para o bucket público.');
      }

      return confirmPlatformBlogMediaAsset(presign.asset.id, normalizedReason);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['platform-blog', 'media'] });
      setSelectedFile(null);
      setLabel('');
      setAltText('');
      setCaption('');
      toast({ title: 'Asset enviado para a biblioteca global.' });
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Erro no upload', description: error.message });
    },
  });

  const grouped = useMemo(() => {
    const assets = assetsQuery.data?.items ?? [];
    return {
      image: assets.filter((asset) => asset.fileKind === 'image'),
      video: assets.filter((asset) => asset.fileKind === 'video'),
      document: assets.filter((asset) => asset.fileKind !== 'image' && asset.fileKind !== 'video'),
    };
  }, [assetsQuery.data]);
  const assetGroups: Array<{ title: string; items: SiteMediaAsset[]; Icon: LucideIcon }> = [
    { title: 'Imagens', items: grouped.image, Icon: ImageIcon },
    { title: 'Vídeos', items: grouped.video, Icon: Video },
    { title: 'Outros arquivos', items: grouped.document, Icon: UploadCloud },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">Biblioteca de mídia</h2>
        <p className="text-sm text-muted-foreground">
          Upload multi-formato com reutilização no blog, na landing e em publicações do Google Business Profile.
        </p>
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.95fr,1.05fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Novo asset</CardTitle>
            <CardDescription>Imagens, vídeos e documentos com metadata pronta para SEO e social cards.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label>Arquivo</Label>
              <Input
                type="file"
                accept=".jpg,.jpeg,.png,.webp,.gif,.svg,.mp4,.webm,.pdf"
                onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Label editorial</Label>
              <Input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Ex.: Hero do artigo sobre intake jurídico" />
            </div>
            <div className="space-y-1.5">
              <Label>Alt text</Label>
              <Input value={altText} onChange={(event) => setAltText(event.target.value)} placeholder="Texto alternativo para SEO e acessibilidade" />
            </div>
            <div className="space-y-1.5">
              <Label>Legenda</Label>
              <Textarea rows={3} value={caption} onChange={(event) => setCaption(event.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="media-operation-reason">Motivo do upload</Label>
              <Input
                id="media-operation-reason"
                value={operationReason}
                onChange={(event) => setOperationReason(event.target.value)}
                maxLength={500}
                placeholder="Ex.: nova capa aprovada para o artigo de onboarding"
              />
            </div>
            <Button className="w-full" onClick={() => uploadMutation.mutate()} disabled={!selectedFile || !reasonValid || uploadMutation.isPending}>
              <UploadCloud className="mr-2 h-4 w-4" />
              {uploadMutation.isPending ? 'Enviando...' : 'Enviar para a biblioteca'}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Assets disponíveis</CardTitle>
            <CardDescription>Prontos para capa, OG image, landing, sitemap multimídia e Google Business.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {assetGroups.map(({ title, items, Icon }) => (
              <div key={title} className="space-y-3">
                <div className="flex items-center gap-2">
                  <Icon className="h-4 w-4 text-primary" />
                  <p className="text-sm font-semibold">{title}</p>
                </div>
                <div className="grid gap-3">
                  {items.map((asset) => (
                    <div key={asset.id} className="rounded-2xl border p-4">
                      <div className="flex items-start justify-between gap-4">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold">{asset.label}</p>
                          <p className="mt-1 truncate text-xs text-muted-foreground">{asset.mimeType} · {asset.status}</p>
                        </div>
                        <Badge className="shrink-0" variant="outline">{asset.fileKind}</Badge>
                      </div>
                      {asset.publicUrl ? (
                        <a href={asset.publicUrl} target="_blank" rel="noreferrer" className="mt-3 inline-flex text-xs text-primary hover:underline">
                          Abrir asset publicado
                        </a>
                      ) : null}
                      {asset.altText ? <p className="mt-2 text-xs text-muted-foreground">ALT: {asset.altText}</p> : null}
                    </div>
                  ))}
                  {!items.length ? (
                    <div className="rounded-2xl border border-dashed p-4 text-sm text-muted-foreground">
                      Nenhum item deste tipo ainda.
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
            {assetsQuery.data ? (
              <div className="flex flex-col gap-2 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-muted-foreground">
                  {assetsQuery.data.total
                    ? `${assetPage * assetPageSize + 1}–${Math.min((assetPage + 1) * assetPageSize, assetsQuery.data.total)} de ${assetsQuery.data.total}`
                    : 'Nenhum asset encontrado'}
                </p>
                <div className="flex gap-2">
                  <Button type="button" variant="outline" size="sm" onClick={() => setAssetPage((current) => Math.max(0, current - 1))} disabled={assetPage === 0 || assetsQuery.isFetching}>
                    Anterior
                  </Button>
                  <Button type="button" variant="outline" size="sm" onClick={() => setAssetPage((current) => current + 1)} disabled={!assetsQuery.data.hasMore || assetsQuery.isFetching}>
                    Próxima
                  </Button>
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
