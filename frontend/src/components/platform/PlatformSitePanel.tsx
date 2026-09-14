import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  fetchPlatformSiteFaq,
  fetchPlatformSiteHome,
  fetchPlatformSiteLegal,
  fetchPlatformSiteMedia,
  fetchPlatformSiteSettings,
  publishPlatformSiteResource,
  savePlatformSiteFaq,
  savePlatformSiteHome,
  savePlatformSiteLegal,
  savePlatformSiteMedia,
  savePlatformSiteSettings,
} from '@/services/platform';
import type {
  PlatformSiteDocumentEnvelope,
  PublicFaqDocument,
  PublicHomeDocument,
  PublicHomeSection,
  PublicLegalDocument,
  PublicMediaLibrary,
  PublicSiteSettings,
} from '@/types';
import { Eye, Globe, Image as ImageIcon, Save, Send, ShieldCheck } from 'lucide-react';

type SiteResource = 'settings' | 'home' | 'faq' | 'media' | 'terms' | 'privacy';

function splitLines(value: string): string[] {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean);
}

function joinLines(items?: string[] | null): string {
  return (items ?? []).join('\n');
}

function ResourceHeader<T>({
  title,
  envelope,
  onSave,
  onPublish,
  reason,
  reasonId,
  onReasonChange,
  saving,
  publishing,
}: {
  title: string;
  envelope?: PlatformSiteDocumentEnvelope<T> | null;
  onSave: () => void;
  onPublish: () => void;
  reason: string;
  reasonId: string;
  onReasonChange: (value: string) => void;
  saving?: boolean;
  publishing?: boolean;
}) {
  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,28rem)_auto] lg:items-end">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <h3 className="text-base font-semibold">{title}</h3>
          {envelope && <Badge variant="outline">v{envelope.version}</Badge>}
        </div>
        {envelope && (
          <p className="text-xs text-muted-foreground">
            Atualizado em {new Date(envelope.updatedAt).toLocaleString('pt-BR')}
            {envelope.publishedAt ? ` · Publicado em ${new Date(envelope.publishedAt).toLocaleString('pt-BR')}` : ' · Ainda não publicado'}
          </p>
        )}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={reasonId}>Motivo da alteração ou publicação</Label>
        <Input
          id={reasonId}
          value={reason}
          maxLength={500}
          onChange={(event) => onReasonChange(event.target.value)}
          placeholder="Ex.: atualização aprovada na revisão editorial de 28/07."
        />
        <p className="text-xs text-muted-foreground">Obrigatório, entre 8 e 500 caracteres.</p>
      </div>
      <div className="flex flex-wrap items-center gap-2 lg:justify-end">
        <Button variant="outline" size="sm" onClick={onSave} disabled={saving || reason.trim().length < 8}>
          <Save className="mr-1.5 h-3.5 w-3.5" />
          Salvar rascunho
        </Button>
        <Button size="sm" onClick={onPublish} disabled={publishing || reason.trim().length < 8}>
          <Send className="mr-1.5 h-3.5 w-3.5" />
          Publicar
        </Button>
      </div>
    </div>
  );
}

export function PlatformSitePanel() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({ queryKey: ['platform-site', 'settings'], queryFn: fetchPlatformSiteSettings, staleTime: 30_000 });
  const homeQuery = useQuery({ queryKey: ['platform-site', 'home'], queryFn: fetchPlatformSiteHome, staleTime: 30_000 });
  const faqQuery = useQuery({ queryKey: ['platform-site', 'faq'], queryFn: fetchPlatformSiteFaq, staleTime: 30_000 });
  const mediaQuery = useQuery({ queryKey: ['platform-site', 'media'], queryFn: fetchPlatformSiteMedia, staleTime: 30_000 });
  const termsQuery = useQuery({ queryKey: ['platform-site', 'terms'], queryFn: () => fetchPlatformSiteLegal('terms'), staleTime: 30_000 });
  const privacyQuery = useQuery({ queryKey: ['platform-site', 'privacy'], queryFn: () => fetchPlatformSiteLegal('privacy'), staleTime: 30_000 });

  const [settingsDraft, setSettingsDraft] = useState<PublicSiteSettings | null>(null);
  const [homeDraft, setHomeDraft] = useState<PublicHomeDocument | null>(null);
  const [faqDraft, setFaqDraft] = useState<PublicFaqDocument | null>(null);
  const [mediaDraft, setMediaDraft] = useState<PublicMediaLibrary | null>(null);
  const [termsDraft, setTermsDraft] = useState<PublicLegalDocument | null>(null);
  const [privacyDraft, setPrivacyDraft] = useState<PublicLegalDocument | null>(null);
  const [reasons, setReasons] = useState<Record<SiteResource, string>>({
    settings: '', home: '', faq: '', media: '', terms: '', privacy: '',
  });

  const setReason = (resource: SiteResource, value: string) => {
    setReasons((current) => ({ ...current, [resource]: value }));
  };

  useEffect(() => {
    setSettingsDraft(settingsQuery.data?.draft ?? null);
  }, [settingsQuery.data]);
  useEffect(() => {
    setHomeDraft(homeQuery.data?.draft ?? null);
  }, [homeQuery.data]);
  useEffect(() => {
    setFaqDraft(faqQuery.data?.draft ?? null);
  }, [faqQuery.data]);
  useEffect(() => {
    setMediaDraft(mediaQuery.data?.draft ?? null);
  }, [mediaQuery.data]);
  useEffect(() => {
    setTermsDraft(termsQuery.data?.draft ?? null);
  }, [termsQuery.data]);
  useEffect(() => {
    setPrivacyDraft(privacyQuery.data?.draft ?? null);
  }, [privacyQuery.data]);

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['platform-site'] });
    void queryClient.invalidateQueries({ queryKey: ['public-home'] });
    void queryClient.invalidateQueries({ queryKey: ['public-legal'] });
    void queryClient.invalidateQueries({ queryKey: ['public-site-settings'] });
  };

  const saveSettingsMutation = useMutation({
    mutationFn: ({ payload, reason }: { payload: PublicSiteSettings; reason: string }) => savePlatformSiteSettings(payload, reason),
    onSuccess: () => {
      invalidateAll();
      toast({ title: 'Configurações do site salvas.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao salvar', description: error.message }),
  });
  const saveHomeMutation = useMutation({
    mutationFn: ({ payload, reason }: { payload: PublicHomeDocument; reason: string }) => savePlatformSiteHome(payload, reason),
    onSuccess: () => {
      invalidateAll();
      toast({ title: 'Home salva como rascunho.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao salvar', description: error.message }),
  });
  const saveFaqMutation = useMutation({
    mutationFn: ({ payload, reason }: { payload: PublicFaqDocument; reason: string }) => savePlatformSiteFaq(payload, reason),
    onSuccess: () => {
      invalidateAll();
      toast({ title: 'FAQ salvo como rascunho.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao salvar', description: error.message }),
  });
  const saveMediaMutation = useMutation({
    mutationFn: ({ payload, reason }: { payload: PublicMediaLibrary; reason: string }) => savePlatformSiteMedia(payload, reason),
    onSuccess: () => {
      invalidateAll();
      toast({ title: 'Biblioteca de mídias salva.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao salvar', description: error.message }),
  });
  const saveTermsMutation = useMutation({
    mutationFn: ({ payload, reason }: { payload: PublicLegalDocument; reason: string }) => savePlatformSiteLegal('terms', payload, reason),
    onSuccess: () => {
      invalidateAll();
      toast({ title: 'Termos salvos.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao salvar', description: error.message }),
  });
  const savePrivacyMutation = useMutation({
    mutationFn: ({ payload, reason }: { payload: PublicLegalDocument; reason: string }) => savePlatformSiteLegal('privacy', payload, reason),
    onSuccess: () => {
      invalidateAll();
      toast({ title: 'Privacidade salva.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao salvar', description: error.message }),
  });
  const publishMutation = useMutation({
    mutationFn: ({ resource, reason }: { resource: SiteResource; reason: string }) => publishPlatformSiteResource(resource, reason),
    onSuccess: (_, { resource }) => {
      invalidateAll();
      toast({ title: `Recurso publicado: ${resource}.` });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Erro ao publicar', description: error.message }),
  });

  const homeSections = [...(homeDraft?.sections ?? [])].sort((a, b) => a.order - b.order);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Globe className="h-4 w-4" />
          Site Público
        </CardTitle>
        <CardDescription>
          Edite a home pública, o FAQ, as mídias e os documentos legais da Meu Executivo. O conteúdo publicado aparece no site sem mexer em código.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="settings" className="space-y-4">
          <TabsList className="flex h-auto flex-wrap justify-start gap-2 bg-transparent p-0">
            <TabsTrigger value="settings">Configurações</TabsTrigger>
            <TabsTrigger value="home">Home</TabsTrigger>
            <TabsTrigger value="media">Mídias</TabsTrigger>
            <TabsTrigger value="faq">FAQ</TabsTrigger>
            <TabsTrigger value="terms">Termos</TabsTrigger>
            <TabsTrigger value="privacy">Privacidade</TabsTrigger>
            <TabsTrigger value="seo">SEO</TabsTrigger>
          </TabsList>

          <TabsContent value="settings" className="space-y-4">
            <ResourceHeader
              title="Configurações globais"
              envelope={settingsQuery.data}
              reason={reasons.settings}
              reasonId="platform-site-settings-reason"
              onReasonChange={(value) => setReason('settings', value)}
              onSave={() => settingsDraft && saveSettingsMutation.mutate({ payload: settingsDraft, reason: reasons.settings.trim() })}
              onPublish={() => publishMutation.mutate({ resource: 'settings', reason: reasons.settings.trim() })}
              saving={saveSettingsMutation.isPending}
              publishing={publishMutation.isPending}
            />
            {settingsDraft && (
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>Nome da marca</Label>
                  <Input value={settingsDraft.brandName} onChange={(e) => setSettingsDraft({ ...settingsDraft, brandName: e.target.value })} />
                </div>
                <div className="space-y-1.5">
                  <Label>Tagline</Label>
                  <Input value={settingsDraft.tagline} onChange={(e) => setSettingsDraft({ ...settingsDraft, tagline: e.target.value })} />
                </div>
                <div className="space-y-1.5">
                  <Label>WhatsApp comercial</Label>
                  <Input value={settingsDraft.whatsappUrl} onChange={(e) => setSettingsDraft({ ...settingsDraft, whatsappUrl: e.target.value })} />
                </div>
                <div className="space-y-1.5">
                  <Label>Label do botão flutuante</Label>
                  <Input value={settingsDraft.floatingWhatsappLabel} onChange={(e) => setSettingsDraft({ ...settingsDraft, floatingWhatsappLabel: e.target.value })} />
                </div>
                <div className="space-y-1.5 md:col-span-2">
                  <Label>Mensagem inicial do WhatsApp</Label>
                  <Textarea rows={3} value={settingsDraft.whatsappMessage} onChange={(e) => setSettingsDraft({ ...settingsDraft, whatsappMessage: e.target.value })} />
                </div>
                <div className="space-y-1.5">
                  <Label>CTA principal</Label>
                  <Input value={settingsDraft.primaryCtaLabel} onChange={(e) => setSettingsDraft({ ...settingsDraft, primaryCtaLabel: e.target.value })} />
                </div>
                <div className="space-y-1.5">
                  <Label>CTA secundário</Label>
                  <Input value={settingsDraft.secondaryCtaLabel} onChange={(e) => setSettingsDraft({ ...settingsDraft, secondaryCtaLabel: e.target.value })} />
                </div>
                <div className="space-y-1.5">
                  <Label>Seção do CTA secundário</Label>
                  <Input value={settingsDraft.secondaryCtaSectionId} onChange={(e) => setSettingsDraft({ ...settingsDraft, secondaryCtaSectionId: e.target.value })} />
                </div>
                <div className="space-y-1.5">
                  <Label>E-mail de suporte</Label>
                  <Input value={settingsDraft.supportEmail} onChange={(e) => setSettingsDraft({ ...settingsDraft, supportEmail: e.target.value })} />
                </div>
              </div>
            )}
          </TabsContent>

          <TabsContent value="home" className="space-y-4">
            <ResourceHeader
              title="Landing one-page"
              envelope={homeQuery.data}
              reason={reasons.home}
              reasonId="platform-site-home-reason"
              onReasonChange={(value) => setReason('home', value)}
              onSave={() => homeDraft && saveHomeMutation.mutate({ payload: homeDraft, reason: reasons.home.trim() })}
              onPublish={() => publishMutation.mutate({ resource: 'home', reason: reasons.home.trim() })}
              saving={saveHomeMutation.isPending}
              publishing={publishMutation.isPending}
            />
            <div className="space-y-4">
              {homeSections.map((section, index) => (
                <Card key={section.key}>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-sm">Seção {index + 1}: {section.navLabel}</CardTitle>
                    <CardDescription>Âncora `#{section.key}`</CardDescription>
                  </CardHeader>
                  <CardContent className="grid gap-3">
                    <div className="grid gap-3 md:grid-cols-3">
                      <div className="space-y-1.5">
                        <Label>Key</Label>
                        <Input
                          value={section.key}
                          onChange={(e) => {
                            const next = [...homeSections];
                            next[index] = { ...section, key: e.target.value };
                            setHomeDraft({ ...(homeDraft as PublicHomeDocument), sections: next });
                          }}
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label>Label da navegação</Label>
                        <Input
                          value={section.navLabel}
                          onChange={(e) => {
                            const next = [...homeSections];
                            next[index] = { ...section, navLabel: e.target.value };
                            setHomeDraft({ ...(homeDraft as PublicHomeDocument), sections: next });
                          }}
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label>Ordem</Label>
                        <Input
                          type="number"
                          value={section.order}
                          onChange={(e) => {
                            const next = [...homeSections];
                            next[index] = { ...section, order: Number(e.target.value || 0) };
                            setHomeDraft({ ...(homeDraft as PublicHomeDocument), sections: next });
                          }}
                        />
                      </div>
                    </div>
                    <div className="space-y-1.5">
                      <Label>Título</Label>
                      <Input
                        value={section.title}
                        onChange={(e) => {
                          const next = [...homeSections];
                          next[index] = { ...section, title: e.target.value };
                          setHomeDraft({ ...(homeDraft as PublicHomeDocument), sections: next });
                        }}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label>Subtítulo</Label>
                      <Textarea
                        rows={2}
                        value={section.subtitle ?? ''}
                        onChange={(e) => {
                          const next = [...homeSections];
                          next[index] = { ...section, subtitle: e.target.value };
                          setHomeDraft({ ...(homeDraft as PublicHomeDocument), sections: next });
                        }}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label>Corpo</Label>
                      <Textarea
                        rows={4}
                        value={section.body ?? ''}
                        onChange={(e) => {
                          const next = [...homeSections];
                          next[index] = { ...section, body: e.target.value };
                          setHomeDraft({ ...(homeDraft as PublicHomeDocument), sections: next });
                        }}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label>Bullets (uma por linha)</Label>
                      <Textarea
                        rows={4}
                        value={joinLines(section.bullets)}
                        onChange={(e) => {
                          const next = [...homeSections];
                          next[index] = { ...section, bullets: splitLines(e.target.value) };
                          setHomeDraft({ ...(homeDraft as PublicHomeDocument), sections: next });
                        }}
                      />
                    </div>
                    <div className="grid gap-3 md:grid-cols-2">
                      <div className="space-y-1.5">
                        <Label>CTA primário</Label>
                        <Input
                          value={section.cta?.primaryLabel ?? ''}
                          onChange={(e) => {
                            const next = [...homeSections];
                            next[index] = { ...section, cta: { ...(section.cta ?? {}), primaryLabel: e.target.value } };
                            setHomeDraft({ ...(homeDraft as PublicHomeDocument), sections: next });
                          }}
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label>CTA secundário</Label>
                        <Input
                          value={section.cta?.secondaryLabel ?? ''}
                          onChange={(e) => {
                            const next = [...homeSections];
                            next[index] = { ...section, cta: { ...(section.cta ?? {}), secondaryLabel: e.target.value } };
                            setHomeDraft({ ...(homeDraft as PublicHomeDocument), sections: next });
                          }}
                        />
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </TabsContent>

          <TabsContent value="media" className="space-y-4">
            <ResourceHeader
              title="Biblioteca de mídias"
              envelope={mediaQuery.data}
              reason={reasons.media}
              reasonId="platform-site-media-reason"
              onReasonChange={(value) => setReason('media', value)}
              onSave={() => mediaDraft && saveMediaMutation.mutate({ payload: mediaDraft, reason: reasons.media.trim() })}
              onPublish={() => publishMutation.mutate({ resource: 'media', reason: reasons.media.trim() })}
              saving={saveMediaMutation.isPending}
              publishing={publishMutation.isPending}
            />
            <div className="space-y-4">
              {mediaDraft?.items.map((asset, index) => (
                <Card key={asset.key}>
                  <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-sm">
                      <ImageIcon className="h-4 w-4" />
                      {asset.label}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="grid gap-3 md:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label>Label</Label>
                      <Input
                        value={asset.label}
                        onChange={(e) => {
                          const next = [...(mediaDraft?.items ?? [])];
                          next[index] = { ...asset, label: e.target.value };
                          setMediaDraft({ items: next });
                        }}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label>Key</Label>
                      <Input
                        value={asset.key}
                        onChange={(e) => {
                          const next = [...(mediaDraft?.items ?? [])];
                          next[index] = { ...asset, key: e.target.value };
                          setMediaDraft({ items: next });
                        }}
                      />
                    </div>
                    <div className="space-y-1.5 md:col-span-2">
                      <Label>Poster URL</Label>
                      <Input
                        value={asset.posterUrl ?? ''}
                        onChange={(e) => {
                          const next = [...(mediaDraft?.items ?? [])];
                          next[index] = { ...asset, posterUrl: e.target.value };
                          setMediaDraft({ items: next });
                        }}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label>MP4 URL</Label>
                      <Input
                        value={asset.mp4Url ?? ''}
                        onChange={(e) => {
                          const next = [...(mediaDraft?.items ?? [])];
                          next[index] = { ...asset, mp4Url: e.target.value };
                          setMediaDraft({ items: next });
                        }}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label>WebM URL</Label>
                      <Input
                        value={asset.webmUrl ?? ''}
                        onChange={(e) => {
                          const next = [...(mediaDraft?.items ?? [])];
                          next[index] = { ...asset, webmUrl: e.target.value };
                          setMediaDraft({ items: next });
                        }}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label>GIF URL</Label>
                      <Input
                        value={asset.gifUrl ?? ''}
                        onChange={(e) => {
                          const next = [...(mediaDraft?.items ?? [])];
                          next[index] = { ...asset, gifUrl: e.target.value };
                          setMediaDraft({ items: next });
                        }}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label>Layout variant</Label>
                      <Input
                        value={asset.layoutVariant}
                        onChange={(e) => {
                          const next = [...(mediaDraft?.items ?? [])];
                          next[index] = { ...asset, layoutVariant: e.target.value };
                          setMediaDraft({ items: next });
                        }}
                      />
                    </div>
                    <div className="space-y-1.5 md:col-span-2">
                      <Label>Alt</Label>
                      <Input
                        value={asset.alt ?? ''}
                        onChange={(e) => {
                          const next = [...(mediaDraft?.items ?? [])];
                          next[index] = { ...asset, alt: e.target.value };
                          setMediaDraft({ items: next });
                        }}
                      />
                    </div>
                    <div className="space-y-1.5 md:col-span-2">
                      <Label>Legenda</Label>
                      <Textarea
                        rows={2}
                        value={asset.caption ?? ''}
                        onChange={(e) => {
                          const next = [...(mediaDraft?.items ?? [])];
                          next[index] = { ...asset, caption: e.target.value };
                          setMediaDraft({ items: next });
                        }}
                      />
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </TabsContent>

          <TabsContent value="faq" className="space-y-4">
            <ResourceHeader
              title="Perguntas frequentes"
              envelope={faqQuery.data}
              reason={reasons.faq}
              reasonId="platform-site-faq-reason"
              onReasonChange={(value) => setReason('faq', value)}
              onSave={() => faqDraft && saveFaqMutation.mutate({ payload: faqDraft, reason: reasons.faq.trim() })}
              onPublish={() => publishMutation.mutate({ resource: 'faq', reason: reasons.faq.trim() })}
              saving={saveFaqMutation.isPending}
              publishing={publishMutation.isPending}
            />
            <div className="space-y-3">
              {faqDraft?.items.map((item, index) => (
                <Card key={`${item.question}-${index}`}>
                  <CardContent className="grid gap-3 pt-6">
                    <div className="space-y-1.5">
                      <Label>Pergunta</Label>
                      <Input
                        value={item.question}
                        onChange={(e) => {
                          const next = [...(faqDraft?.items ?? [])];
                          next[index] = { ...item, question: e.target.value };
                          setFaqDraft({ items: next });
                        }}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label>Resposta</Label>
                      <Textarea
                        rows={4}
                        value={item.answer}
                        onChange={(e) => {
                          const next = [...(faqDraft?.items ?? [])];
                          next[index] = { ...item, answer: e.target.value };
                          setFaqDraft({ items: next });
                        }}
                      />
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </TabsContent>

          <TabsContent value="terms" className="space-y-4">
            <ResourceHeader
              title="Termos de Uso"
              envelope={termsQuery.data}
              reason={reasons.terms}
              reasonId="platform-site-terms-reason"
              onReasonChange={(value) => setReason('terms', value)}
              onSave={() => termsDraft && saveTermsMutation.mutate({ payload: termsDraft, reason: reasons.terms.trim() })}
              onPublish={() => publishMutation.mutate({ resource: 'terms', reason: reasons.terms.trim() })}
              saving={saveTermsMutation.isPending}
              publishing={publishMutation.isPending}
            />
            {termsDraft && (
              <div className="grid gap-4">
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label>Título</Label>
                    <Input value={termsDraft.title} onChange={(e) => setTermsDraft({ ...termsDraft, title: e.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Última atualização</Label>
                    <Input value={termsDraft.updatedLabel} onChange={(e) => setTermsDraft({ ...termsDraft, updatedLabel: e.target.value })} />
                  </div>
                </div>
                {termsDraft.sections.map((section, index) => (
                  <Card key={`${section.heading}-${index}`}>
                    <CardContent className="grid gap-3 pt-6">
                      <Input
                        value={section.heading}
                        onChange={(e) => {
                          const next = [...termsDraft.sections];
                          next[index] = { ...section, heading: e.target.value };
                          setTermsDraft({ ...termsDraft, sections: next });
                        }}
                      />
                      <Textarea
                        rows={4}
                        value={section.body}
                        onChange={(e) => {
                          const next = [...termsDraft.sections];
                          next[index] = { ...section, body: e.target.value };
                          setTermsDraft({ ...termsDraft, sections: next });
                        }}
                      />
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </TabsContent>

          <TabsContent value="privacy" className="space-y-4">
            <ResourceHeader
              title="Política de Privacidade"
              envelope={privacyQuery.data}
              reason={reasons.privacy}
              reasonId="platform-site-privacy-reason"
              onReasonChange={(value) => setReason('privacy', value)}
              onSave={() => privacyDraft && savePrivacyMutation.mutate({ payload: privacyDraft, reason: reasons.privacy.trim() })}
              onPublish={() => publishMutation.mutate({ resource: 'privacy', reason: reasons.privacy.trim() })}
              saving={savePrivacyMutation.isPending}
              publishing={publishMutation.isPending}
            />
            {privacyDraft && (
              <div className="grid gap-4">
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label>Título</Label>
                    <Input value={privacyDraft.title} onChange={(e) => setPrivacyDraft({ ...privacyDraft, title: e.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Última atualização</Label>
                    <Input value={privacyDraft.updatedLabel} onChange={(e) => setPrivacyDraft({ ...privacyDraft, updatedLabel: e.target.value })} />
                  </div>
                </div>
                {privacyDraft.sections.map((section, index) => (
                  <Card key={`${section.heading}-${index}`}>
                    <CardContent className="grid gap-3 pt-6">
                      <Input
                        value={section.heading}
                        onChange={(e) => {
                          const next = [...privacyDraft.sections];
                          next[index] = { ...section, heading: e.target.value };
                          setPrivacyDraft({ ...privacyDraft, sections: next });
                        }}
                      />
                      <Textarea
                        rows={4}
                        value={section.body}
                        onChange={(e) => {
                          const next = [...privacyDraft.sections];
                          next[index] = { ...section, body: e.target.value };
                          setPrivacyDraft({ ...privacyDraft, sections: next });
                        }}
                      />
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </TabsContent>

          <TabsContent value="seo" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <Eye className="h-4 w-4" />
                  SEO e metadados
                </CardTitle>
                <CardDescription>
                  O SEO principal da home e dos documentos legais é editado dentro dos próprios recursos. Aqui fica um lembrete visual do que está publicado.
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-3 md:grid-cols-3">
                <div className="rounded-lg border p-3">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Home</p>
                  <p className="mt-1 text-sm font-medium">{homeDraft?.seo.title ?? '—'}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{homeDraft?.seo.description ?? '—'}</p>
                </div>
                <div className="rounded-lg border p-3">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Termos</p>
                  <p className="mt-1 text-sm font-medium">{termsDraft?.seo.title ?? '—'}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{termsDraft?.seo.description ?? '—'}</p>
                </div>
                <div className="rounded-lg border p-3">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Privacidade</p>
                  <p className="mt-1 text-sm font-medium">{privacyDraft?.seo.title ?? '—'}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{privacyDraft?.seo.description ?? '—'}</p>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
