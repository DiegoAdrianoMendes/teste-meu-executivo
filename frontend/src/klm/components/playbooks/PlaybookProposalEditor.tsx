import { useEffect, useState } from 'react';
import { FileText, Monitor, Plus, Smartphone, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { KlmPill } from '@/klm/components/Primitives';
import { cn } from '@/lib/utils';
import { downloadLocalStorageObject } from '@/services/local-storage';
import type { PlaybookContent } from '@/services/playbooks';

interface Props {
  content: PlaybookContent;
  onChange: (content: PlaybookContent) => void;
}

type PreviewMode = 'desktop' | 'whatsapp';

function listText(values: string[]) {
  return values.join('\n');
}

function textList(value: string) {
  return value.split('\n').map((item) => item.trim()).filter(Boolean);
}

function Slide({
  index,
  title,
  children,
  content,
  logoUrl,
}: {
  index: string;
  title: string;
  children: React.ReactNode;
  content: PlaybookContent;
  logoUrl: string | null;
}) {
  const brand = content.brandKit;
  return (
    <article
      className="relative flex aspect-[16/9] min-h-40 flex-col overflow-hidden border p-5 shadow-sm"
      style={{
        backgroundColor: brand.backgroundColor,
        borderColor: `${brand.secondaryColor}33`,
        color: brand.textColor,
        fontFamily: brand.bodyFont,
      }}
    >
      <span className="absolute left-0 top-0 h-full w-1.5" style={{ backgroundColor: brand.secondaryColor }} />
      <div className="flex items-center justify-between gap-3">
        <strong className="flex min-w-0 items-center gap-2 text-[9px] uppercase" style={{ color: brand.primaryColor }}>
          {logoUrl ? <img src={logoUrl} alt="" className="h-5 w-5 object-contain" /> : null}
          <span className="truncate">{brand.brandName}</span>
        </strong>
        <span className="text-[9px] opacity-45">{index}</span>
      </div>
      <h4 className="mt-4 text-[16px] font-semibold" style={{ fontFamily: brand.headingFont }}>{title}</h4>
      <div className="mt-3 flex-1 text-[9px] leading-4 opacity-70">{children}</div>
    </article>
  );
}

function ProposalPreview({ mode, content, logoUrl }: { mode: PreviewMode; content: PlaybookContent; logoUrl: string | null }) {
  const slideProps = { content, logoUrl };
  const accentBorder = { borderColor: content.brandKit.secondaryColor };
  const slides = [
    <Slide {...slideProps} key="cover" index="01" title="Seu direito à saúde, tratado com urgência."><p>Atuação nacional em negativas de plano de saúde, com estratégia individual e contato contínuo pelo WhatsApp.</p><div className="mt-auto flex gap-2 pt-3"><KlmPill tone="black">+1.000 casos concluídos</KlmPill><KlmPill tone="gold">média menor que 24h*</KlmPill></div></Slide>,
    <Slide {...slideProps} key="firm" index="02" title="Referência nacional em Direito da Saúde"><p>Equipe especializada, mais de 300 casos em andamento e avaliação 5,0 no Google.</p><div className="mt-4 grid grid-cols-3 gap-2 text-center font-semibold"><span className="border-y py-2" style={accentBorder}>G1</span><span className="border-y py-2" style={accentBorder}>R7</span><span className="border-y py-2" style={accentBorder}>Estadão</span></div></Slide>,
    <Slide {...slideProps} key="case" index="03" title="A estratégia para o seu caso"><p>Organizamos a negativa, o relatório médico, o ciclo de tratamento e as evidências de urgência para definir a medida adequada.</p><ol className="mt-3 grid grid-cols-3 gap-2"><li className="border-t pt-2" style={accentBorder}>Análise documental</li><li className="border-t pt-2" style={accentBorder}>Protocolo proativo</li><li className="border-t pt-2" style={accentBorder}>Acompanhamento</li></ol></Slide>,
    <Slide {...slideProps} key="fees" index="04" title="Condições transparentes"><p>Honorários iniciais a partir de R$ {content.commercial.minimumInitialFeeBrl.toLocaleString('pt-BR', { minimumFractionDigits: 2 })}, com opções previstas no playbook e validade de {content.commercial.proposalValidityDays} dias.</p><p className="mt-3 border-l-2 pl-2" style={accentBorder}>Cada condição final depende da análise e da aprovação aplicável ao caso.</p></Slide>,
    <Slide {...slideProps} key="close" index="05" title="Vamos proteger o que é essencial."><p>{content.proposals.callToAction}</p><p className="mt-4">Após a decisão, seguimos no acompanhamento, em eventual recurso e nas atualizações ao cliente. O WhatsApp permanece disponível.</p><span className="mt-auto text-[10px] font-semibold" style={{ color: content.brandKit.primaryColor }}>{content.brandKit.brandName}</span></Slide>,
  ];

  if (mode === 'whatsapp') {
    return <div className="mx-auto max-w-[330px] space-y-3 rounded-[28px] border-[7px] border-[#111] bg-[#ece5dd] p-3 shadow-xl">{slides.map((slide, index) => <div key={index} className="overflow-hidden rounded-md">{slide}</div>)}</div>;
  }
  return <div className="grid gap-3 xl:grid-cols-2">{slides}</div>;
}

export function PlaybookProposalEditor({ content, onChange }: Props) {
  const [previewMode, setPreviewMode] = useState<PreviewMode>('desktop');
  const [logoUrl, setLogoUrl] = useState<string | null>(null);
  const proposals = content.proposals;
  const update = <K extends keyof typeof proposals>(key: K, value: (typeof proposals)[K]) =>
    onChange({ ...content, proposals: { ...proposals, [key]: value } });

  useEffect(() => {
    if (!content.brandKit.logoObjectId || !content.brandKit.logoObjectVersion) {
      setLogoUrl(null);
      return;
    }
    let disposed = false;
    let objectUrl: string | null = null;
    void downloadLocalStorageObject(content.brandKit.logoObjectId, content.brandKit.logoObjectVersion)
      .then((blob) => {
        if (disposed) return;
        objectUrl = URL.createObjectURL(blob);
        setLogoUrl(objectUrl);
      })
      .catch(() => {
        if (!disposed) setLogoUrl(null);
      });
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [content.brandKit.logoObjectId, content.brandKit.logoObjectVersion]);

  return (
    <div className="grid min-h-[660px] xl:grid-cols-[minmax(340px,.75fr)_minmax(0,1.25fr)]">
      <section className="space-y-5 border-b p-4 md:p-5 xl:border-b-0 xl:border-r">
        <div className="flex items-center gap-2"><FileText className="h-4 w-4 text-primary" /><div><h2 className="text-[14px] font-semibold">Propostas e pós-venda</h2><p className="text-[10px] text-muted-foreground">Conteúdo obrigatório e rastreamento da oferta.</p></div></div>
        <div className="space-y-1.5"><Label className="text-[11px]">Template PDF</Label><Input value={proposals.desktopTemplate} onChange={(event) => update('desktopTemplate', event.target.value)} /></div>
        <div className="space-y-1.5"><Label className="text-[11px]">Template WhatsApp</Label><Input value={proposals.whatsappTemplate} onChange={(event) => update('whatsappTemplate', event.target.value)} /></div>
        <div className="space-y-1.5"><Label className="text-[11px]">Chamada para ação</Label><Textarea className="min-h-20 text-xs" value={proposals.callToAction} onChange={(event) => update('callToAction', event.target.value)} /></div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
          <div className="space-y-1.5"><Label className="text-[11px]">Blocos obrigatórios</Label><Textarea className="min-h-32 text-xs leading-5" value={listText(proposals.requiredBlocks)} onChange={(event) => update('requiredBlocks', textList(event.target.value))} /></div>
          <div className="space-y-1.5"><Label className="text-[11px]">Eventos analíticos</Label><Textarea className="min-h-32 text-xs leading-5" value={listText(proposals.analyticsEvents)} onChange={(event) => update('analyticsEvents', textList(event.target.value))} /></div>
        </div>
        <label className="flex items-center justify-between rounded-md border px-3 py-3 text-[11px]"><span><strong className="block">Aprovação antes do envio</strong><span className="text-[9px] text-muted-foreground">Impede envio automático da proposta final.</span></span><Switch checked={proposals.approvalRequired} onCheckedChange={(checked) => update('approvalRequired', checked)} /></label>
        <div className="space-y-1.5"><Label className="text-[11px]">Plano após a decisão</Label><Textarea className="min-h-28 text-xs leading-5" value={listText(content.postSale)} onChange={(event) => onChange({ ...content, postSale: textList(event.target.value) })} /></div>
        <div>
          <div className="flex items-center justify-between"><Label className="text-[11px]">Experimentos controlados</Label><Button type="button" size="sm" variant="outline" onClick={() => onChange({ ...content, experiments: [...content.experiments, { id: `experiment-${crypto.randomUUID()}`, name: 'Novo experimento', variant: '', control: '', metric: '', stopCriteria: '' }] })}><Plus className="mr-1.5 h-3.5 w-3.5" />Experimento</Button></div>
          <div className="mt-2 space-y-2">{content.experiments.map((experiment, index) => <div key={experiment.id} className="grid gap-2 rounded-md border p-2 sm:grid-cols-2"><Input value={experiment.name} onChange={(event) => onChange({ ...content, experiments: content.experiments.map((item, itemIndex) => itemIndex === index ? { ...item, name: event.target.value } : item) })} placeholder="Nome" /><div className="flex gap-1"><Input value={experiment.metric} onChange={(event) => onChange({ ...content, experiments: content.experiments.map((item, itemIndex) => itemIndex === index ? { ...item, metric: event.target.value } : item) })} placeholder="Métrica" /><Button type="button" size="icon" variant="ghost" aria-label="Remover experimento" onClick={() => onChange({ ...content, experiments: content.experiments.filter((_, itemIndex) => itemIndex !== index) })}><Trash2 className="h-4 w-4" /></Button></div><Input value={experiment.control} onChange={(event) => onChange({ ...content, experiments: content.experiments.map((item, itemIndex) => itemIndex === index ? { ...item, control: event.target.value } : item) })} placeholder="Controle" /><Input value={experiment.variant} onChange={(event) => onChange({ ...content, experiments: content.experiments.map((item, itemIndex) => itemIndex === index ? { ...item, variant: event.target.value } : item) })} placeholder="Variante" /><Input className="sm:col-span-2" value={experiment.stopCriteria} onChange={(event) => onChange({ ...content, experiments: content.experiments.map((item, itemIndex) => itemIndex === index ? { ...item, stopCriteria: event.target.value } : item) })} placeholder="Critério de parada" /></div>)}</div>
        </div>
      </section>

      <section className="min-w-0 bg-secondary/35 p-4 md:p-5">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div><h3 className="text-[12px] font-semibold">Prévia de marca</h3><p className="text-[9px] text-muted-foreground">Conteúdo ilustrativo; os dados reais vêm do Card e das preferências da marca.</p></div>
          <div className="inline-grid grid-cols-2 rounded-md border bg-background p-1">
            <Button type="button" size="sm" variant={previewMode === 'desktop' ? 'default' : 'ghost'} className="h-7 text-[10px]" onClick={() => setPreviewMode('desktop')}><Monitor className="mr-1.5 h-3.5 w-3.5" />PDF</Button>
            <Button type="button" size="sm" variant={previewMode === 'whatsapp' ? 'default' : 'ghost'} className="h-7 text-[10px]" onClick={() => setPreviewMode('whatsapp')}><Smartphone className="mr-1.5 h-3.5 w-3.5" />WhatsApp</Button>
          </div>
        </div>
        <div className={cn(previewMode === 'desktop' ? 'max-w-4xl' : 'py-2')}><ProposalPreview mode={previewMode} content={content} logoUrl={logoUrl} /></div>
      </section>
    </div>
  );
}
