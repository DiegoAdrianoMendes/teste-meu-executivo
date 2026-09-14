import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Archive,
  ArrowDown,
  ArrowUp,
  BookOpenText,
  Braces,
  Copy,
  Download,
  FileText,
  Image,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Search,
  Trash2,
  Upload,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/use-toast';
import { cn } from '@/lib/utils';
import {
  downloadLocalStorageObject,
  fetchLocalStorageObjects,
  saveBlob,
  uploadLocalStorageObject,
} from '@/services/local-storage';
import type {
  PlaybookContent,
  PlaybookDocumentTemplate,
  PlaybookDocumentVariable,
  PlaybookFaqEntry,
  PlaybookResponseTemplate,
} from '@/services/playbooks';

type Props = {
  content: PlaybookContent;
  onChange: (content: PlaybookContent) => void;
};

const VARIABLE_PATTERN = /{{\s*([a-z][a-z0-9_.-]+)\s*}}/g;

function csv(values: string[]) {
  return values.join(', ');
}

function parseCsv(value: string) {
  return [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))];
}

function identifier(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function variablesIn(value: string) {
  return [...value.matchAll(VARIABLE_PATTERN)].map((match) => match[1]);
}

function withSectionVariables(template: PlaybookDocumentTemplate) {
  const discovered = template.sections.flatMap((section) => variablesIn(`${section.title}\n${section.body}`));
  return { ...template, requiredVariables: [...new Set([...template.requiredVariables, ...discovered])] };
}

function LibraryMetric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="border-r border-border px-4 py-3 last:border-r-0">
      <p className="text-[9px] uppercase text-muted-foreground">{label}</p>
      <p className="mt-1 text-[18px] font-semibold">{value}</p>
    </div>
  );
}

function EmptySelection({ label }: { label: string }) {
  return <div className="flex min-h-72 items-center justify-center text-[11px] text-muted-foreground">Selecione {label} para editar.</div>;
}

export function PlaybookLibraryEditor({ content, onChange }: Props) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const logoInputRef = useRef<HTMLInputElement>(null);
  const sourceInputRef = useRef<HTMLInputElement>(null);
  const [logoUrl, setLogoUrl] = useState<string | null>(null);
  const [documentSearch, setDocumentSearch] = useState('');
  const [faqSearch, setFaqSearch] = useState('');
  const [responseSearch, setResponseSearch] = useState('');
  const [variableSearch, setVariableSearch] = useState('');
  const [selectedDocumentId, setSelectedDocumentId] = useState(content.documentTemplates[0]?.id ?? null);
  const [selectedSectionId, setSelectedSectionId] = useState(content.documentTemplates[0]?.sections[0]?.id ?? null);
  const [selectedFaqId, setSelectedFaqId] = useState(content.faqs[0]?.id ?? null);
  const [selectedResponseId, setSelectedResponseId] = useState(content.responseTemplates[0]?.id ?? null);
  const [selectedVariableKey, setSelectedVariableKey] = useState(content.documentVariables[0]?.key ?? null);
  const storageQuery = useQuery({
    queryKey: ['local-storage', 'playbook-library'],
    queryFn: () => fetchLocalStorageObjects(false),
  });

  const availableObjects = useMemo(
    () => (storageQuery.data ?? []).filter((item) => item.state === 'available'),
    [storageQuery.data],
  );
  const imageObjects = useMemo(
    () => availableObjects.filter((item) => item.declaredContentType.startsWith('image/')),
    [availableObjects],
  );

  useEffect(() => {
    if (!content.brandKit.logoObjectId || !content.brandKit.logoObjectVersion) {
      setLogoUrl(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    void downloadLocalStorageObject(content.brandKit.logoObjectId, content.brandKit.logoObjectVersion)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setLogoUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setLogoUrl(null);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [content.brandKit.logoObjectId, content.brandKit.logoObjectVersion]);

  useEffect(() => {
    if (!content.documentTemplates.some((item) => item.id === selectedDocumentId)) {
      setSelectedDocumentId(content.documentTemplates[0]?.id ?? null);
    }
  }, [content.documentTemplates, selectedDocumentId]);
  useEffect(() => {
    if (!content.faqs.some((item) => item.id === selectedFaqId)) setSelectedFaqId(content.faqs[0]?.id ?? null);
  }, [content.faqs, selectedFaqId]);
  useEffect(() => {
    if (!content.responseTemplates.some((item) => item.id === selectedResponseId)) setSelectedResponseId(content.responseTemplates[0]?.id ?? null);
  }, [content.responseTemplates, selectedResponseId]);
  useEffect(() => {
    if (!content.documentVariables.some((item) => item.key === selectedVariableKey)) setSelectedVariableKey(content.documentVariables[0]?.key ?? null);
  }, [content.documentVariables, selectedVariableKey]);

  const setBrand = (patch: Partial<PlaybookContent['brandKit']>) => {
    onChange({ ...content, brandKit: { ...content.brandKit, ...patch } });
  };
  const replaceDocument = (document: PlaybookDocumentTemplate) => {
    onChange({
      ...content,
      documentTemplates: content.documentTemplates.map((item) => item.id === document.id ? withSectionVariables(document) : item),
    });
  };
  const replaceFaq = (faq: PlaybookFaqEntry) => {
    onChange({ ...content, faqs: content.faqs.map((item) => item.id === faq.id ? faq : item) });
  };
  const replaceResponse = (response: PlaybookResponseTemplate) => {
    onChange({ ...content, responseTemplates: content.responseTemplates.map((item) => item.id === response.id ? response : item) });
  };
  const replaceVariable = (previousKey: string, variable: PlaybookDocumentVariable) => {
    onChange({ ...content, documentVariables: content.documentVariables.map((item) => item.key === previousKey ? variable : item) });
    setSelectedVariableKey(variable.key);
  };

  const logoUpload = useMutation({
    mutationFn: (file: File) => uploadLocalStorageObject(file),
    onSuccess: async (stored) => {
      setBrand({ logoObjectId: stored.objectId, logoObjectVersion: stored.version, logoFilename: stored.originalFilename });
      await queryClient.invalidateQueries({ queryKey: ['local-storage'] });
      toast({ title: 'Logo versionado na biblioteca local.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Logo não enviado', description: error.message }),
  });
  const sourceUpload = useMutation({
    mutationFn: (file: File) => uploadLocalStorageObject(file),
    onSuccess: async (stored) => {
      const selected = content.documentTemplates.find((item) => item.id === selectedDocumentId);
      if (selected) replaceDocument({ ...selected, sourceObjectId: stored.objectId, sourceObjectVersion: stored.version, sourceFilename: stored.originalFilename, templateVersion: selected.templateVersion + 1 });
      await queryClient.invalidateQueries({ queryKey: ['local-storage'] });
      toast({ title: 'Arquivo-fonte anexado com versão fixa.' });
    },
    onError: (error: Error) => toast({ variant: 'destructive', title: 'Modelo não enviado', description: error.message }),
  });

  const selectedDocument = content.documentTemplates.find((item) => item.id === selectedDocumentId) ?? null;
  const selectedFaq = content.faqs.find((item) => item.id === selectedFaqId) ?? null;
  const selectedResponse = content.responseTemplates.find((item) => item.id === selectedResponseId) ?? null;
  const selectedVariable = content.documentVariables.find((item) => item.key === selectedVariableKey) ?? null;
  const documents = content.documentTemplates.filter((item) => `${item.name} ${item.documentType} ${item.channel}`.toLocaleLowerCase('pt-BR').includes(documentSearch.trim().toLocaleLowerCase('pt-BR')));
  const faqs = content.faqs.filter((item) => `${item.category} ${item.question} ${item.keywords.join(' ')}`.toLocaleLowerCase('pt-BR').includes(faqSearch.trim().toLocaleLowerCase('pt-BR')));
  const responses = content.responseTemplates.filter((item) => `${item.name} ${item.intent} ${item.body}`.toLocaleLowerCase('pt-BR').includes(responseSearch.trim().toLocaleLowerCase('pt-BR')));
  const variables = content.documentVariables.filter((item) => `${item.key} ${item.label} ${item.sourcePath}`.toLocaleLowerCase('pt-BR').includes(variableSearch.trim().toLocaleLowerCase('pt-BR')));

  const addDocument = () => {
    const next: PlaybookDocumentTemplate = {
      id: identifier('document'), name: 'Novo modelo', documentType: 'other', channel: 'document', outputFormat: 'pdf', layout: 'letterhead_a4', templateVersion: 1, status: 'draft', sections: [{ id: identifier('section'), title: 'Nova seção', body: 'Conteúdo sujeito à revisão.', required: true }], requiredVariables: [], applicableStages: [], applicableSubthemes: [], agentKeys: [], sourceObjectId: null, sourceObjectVersion: null, sourceFilename: null, approvalRequired: true, active: false,
    };
    onChange({ ...content, documentTemplates: [...content.documentTemplates, next] });
    setSelectedDocumentId(next.id);
    setSelectedSectionId(next.sections[0].id);
  };
  const duplicateDocument = () => {
    if (!selectedDocument) return;
    const next = structuredClone(selectedDocument);
    next.id = identifier('document');
    next.name = `${selectedDocument.name} · cópia`;
    next.status = 'draft';
    next.active = false;
    next.templateVersion = 1;
    next.sections = next.sections.map((section) => ({ ...section, id: identifier('section') }));
    next.sourceObjectId = null;
    next.sourceObjectVersion = null;
    next.sourceFilename = null;
    onChange({ ...content, documentTemplates: [...content.documentTemplates, next] });
    setSelectedDocumentId(next.id);
    setSelectedSectionId(next.sections[0]?.id ?? null);
  };

  return (
    <div className="min-h-[640px]">
      <div className="grid grid-cols-2 border-b bg-secondary/25 sm:grid-cols-3 lg:grid-cols-6">
        <LibraryMetric label="Logo" value={content.brandKit.logoObjectId ? 'Versionado' : 'Pendente'} />
        <LibraryMetric label="Documentos" value={content.documentTemplates.length} />
        <LibraryMetric label="Aprovados" value={content.documentTemplates.filter((item) => item.status === 'approved' && item.active).length} />
        <LibraryMetric label="FAQ" value={content.faqs.filter((item) => item.active).length} />
        <LibraryMetric label="Respostas" value={content.responseTemplates.filter((item) => item.active).length} />
        <LibraryMetric label="Variáveis" value={content.documentVariables.length} />
      </div>

      <Tabs defaultValue="brand" orientation="vertical" className="grid min-h-[580px] md:grid-cols-[190px_minmax(0,1fr)]">
        <TabsList className="h-auto min-h-full w-full flex-row justify-start overflow-x-auto rounded-none border-b bg-secondary/20 p-2 md:flex-col md:justify-start md:border-b-0 md:border-r">
          {[
            ['brand', 'Marca e timbrado', Image],
            ['documents', 'Documentos', FileText],
            ['faq', 'FAQ aprovada', BookOpenText],
            ['responses', 'Respostas', MessageSquareText],
            ['variables', 'Variáveis', Braces],
          ].map(([value, label, Icon]) => <TabsTrigger key={String(value)} value={String(value)} className="h-9 w-auto shrink-0 justify-start gap-2 px-3 text-[10px] md:w-full"><Icon className="h-3.5 w-3.5" />{String(label)}</TabsTrigger>)}
        </TabsList>

        <TabsContent value="brand" className="m-0 p-4 lg:p-5">
          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
            <div className="space-y-5">
              <section>
                <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3"><div><h3 className="text-[15px] font-semibold">Identidade do escritório</h3><p className="text-[10px] text-muted-foreground">Usada em propostas, contratos, procurações, petições e comunicações.</p></div><Button type="button" variant="outline" size="sm" onClick={() => logoInputRef.current?.click()} disabled={logoUpload.isPending}>{logoUpload.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}Enviar logo</Button><input ref={logoInputRef} type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) logoUpload.mutate(file); event.currentTarget.value = ''; }} /></div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5"><Label>Nome da marca</Label><Input aria-label="Nome da marca" value={content.brandKit.brandName} onChange={(event) => setBrand({ brandName: event.target.value })} /></div>
                  <div className="space-y-1.5"><Label>Logo armazenado</Label><Select value={content.brandKit.logoObjectId ? `${content.brandKit.logoObjectId}:${content.brandKit.logoObjectVersion}` : 'none'} onValueChange={(value) => { if (value === 'none') { setBrand({ logoObjectId: null, logoObjectVersion: null, logoFilename: null }); return; } const selected = imageObjects.find((item) => `${item.objectId}:${item.version}` === value); if (selected) setBrand({ logoObjectId: selected.objectId, logoObjectVersion: selected.version, logoFilename: selected.originalFilename }); }}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sem logo</SelectItem>{imageObjects.map((item) => <SelectItem key={`${item.objectId}:${item.version}`} value={`${item.objectId}:${item.version}`}>{item.originalFilename} · v{item.version}</SelectItem>)}</SelectContent></Select></div>
                  {([['primaryColor', 'Preto institucional'], ['secondaryColor', 'Dourado'], ['accentColor', 'Verde de ação'], ['textColor', 'Texto'], ['backgroundColor', 'Fundo']] as const).map(([key, label]) => <div key={key} className="space-y-1.5"><Label>{label}</Label><div className="flex gap-2"><input aria-label={`Amostra ${label}`} type="color" className="h-10 w-11 rounded border bg-transparent p-1" value={content.brandKit[key]} onChange={(event) => setBrand({ [key]: event.target.value })} /><Input value={content.brandKit[key]} onChange={(event) => setBrand({ [key]: event.target.value.toUpperCase() })} /></div></div>)}
                  <div className="space-y-1.5"><Label>Fonte de títulos</Label><Input value={content.brandKit.headingFont} onChange={(event) => setBrand({ headingFont: event.target.value })} /></div>
                  <div className="space-y-1.5"><Label>Fonte de texto</Label><Input value={content.brandKit.bodyFont} onChange={(event) => setBrand({ bodyFont: event.target.value })} /></div>
                </div>
              </section>
              <section>
                <div className="flex items-center justify-between border-b pb-3"><div><h3 className="text-[15px] font-semibold">Papel timbrado</h3><p className="text-[10px] text-muted-foreground">Cabeçalho, rodapé, assinatura e nota padrão.</p></div><Switch checked={content.brandKit.letterheadEnabled} onCheckedChange={(checked) => setBrand({ letterheadEnabled: checked })} /></div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5 sm:col-span-2"><Label>Cabeçalho</Label><Input value={content.brandKit.letterheadHeader} onChange={(event) => setBrand({ letterheadHeader: event.target.value })} /></div>
                  <div className="space-y-1.5 sm:col-span-2"><Label>Rodapé</Label><Input value={content.brandKit.letterheadFooter} onChange={(event) => setBrand({ letterheadFooter: event.target.value })} /></div>
                  <div className="space-y-1.5"><Label>Assinatura</Label><Input value={content.brandKit.signatureName} onChange={(event) => setBrand({ signatureName: event.target.value })} /></div>
                  <div className="space-y-1.5"><Label>Cargo / identificação</Label><Input value={content.brandKit.signatureTitle} onChange={(event) => setBrand({ signatureTitle: event.target.value })} /></div>
                  <div className="space-y-1.5 sm:col-span-2"><Label>Linhas de contato</Label><Input value={csv(content.brandKit.contactLines)} onChange={(event) => setBrand({ contactLines: parseCsv(event.target.value) })} /></div>
                  <div className="space-y-1.5 sm:col-span-2"><Label>Nota padrão</Label><Textarea value={content.brandKit.disclaimer} onChange={(event) => setBrand({ disclaimer: event.target.value })} /></div>
                </div>
              </section>
            </div>
            <aside className="self-start border bg-white shadow-sm dark:bg-[#111318]" style={{ color: content.brandKit.textColor, fontFamily: content.brandKit.bodyFont }}>
              <div className="flex min-h-20 items-center justify-between gap-4 border-b px-6 py-4" style={{ borderTop: `5px solid ${content.brandKit.secondaryColor}` }}><div>{logoUrl ? <img src={logoUrl} alt={`Logo ${content.brandKit.brandName}`} className="max-h-11 max-w-40 object-contain" /> : <p className="text-[16px] font-semibold" style={{ fontFamily: content.brandKit.headingFont }}>{content.brandKit.brandName}</p>}<p className="mt-1 text-[8px] opacity-65">{content.brandKit.letterheadHeader}</p></div><div className="h-10 w-1" style={{ background: content.brandKit.accentColor }} /></div>
              <div className="min-h-[430px] px-8 py-9"><p className="text-[9px] font-semibold uppercase" style={{ color: content.brandKit.secondaryColor }}>Documento institucional</p><h4 className="mt-3 text-[24px] font-semibold leading-8" style={{ fontFamily: content.brandKit.headingFont }}>A clareza da marca acompanha cada caso.</h4><p className="mt-5 text-[11px] leading-5 opacity-80">Este é o timbrado-base usado pela biblioteca versionada. O conteúdo final combina dados confirmados do Card, documentos, conversa e aprovação humana aplicável.</p><div className="mt-9 h-px" style={{ background: content.brandKit.secondaryColor }} /><p className="mt-8 text-[10px] font-semibold">{content.brandKit.signatureName}</p><p className="text-[9px] opacity-65">{content.brandKit.signatureTitle}</p></div>
              <div className="border-t px-6 py-3 text-[8px] opacity-65"><p>{content.brandKit.letterheadFooter}</p><p className="mt-1">{content.brandKit.contactLines.join(' · ')}</p></div>
            </aside>
          </div>
        </TabsContent>

        <TabsContent value="documents" className="m-0 grid min-h-[580px] lg:grid-cols-[310px_minmax(0,1fr)]">
          <aside className="border-r bg-secondary/10 p-3"><div className="flex gap-2"><div className="relative min-w-0 flex-1"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input value={documentSearch} onChange={(event) => setDocumentSearch(event.target.value)} className="pl-9" placeholder="Buscar modelo" /></div><Button type="button" size="icon" aria-label="Adicionar modelo" onClick={addDocument}><Plus className="h-4 w-4" /></Button></div><ScrollArea className="mt-3 h-[510px]"><div className="space-y-1.5 pr-3">{documents.map((item) => <button key={item.id} type="button" onClick={() => { setSelectedDocumentId(item.id); setSelectedSectionId(item.sections[0]?.id ?? null); }} className={cn('w-full border px-3 py-2.5 text-left', selectedDocumentId === item.id ? 'border-primary bg-primary/10' : 'bg-background hover:border-primary/50')}><div className="flex items-center justify-between gap-2"><span className="truncate text-[11px] font-semibold">{item.name}</span><Badge variant={item.status === 'approved' ? 'default' : 'outline'}>{item.status}</Badge></div><p className="mt-1 text-[9px] text-muted-foreground">{item.documentType.replaceAll('_', ' ')} · {item.channel} · v{item.templateVersion}</p></button>)}</div></ScrollArea></aside>
          <div className="min-w-0 p-4 lg:p-5">{selectedDocument ? <div className="space-y-5"><div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3"><div><h3 className="text-[15px] font-semibold">Modelo versionado</h3><p className="text-[10px] text-muted-foreground">Estrutura, arquivo-fonte, canais, agentes e gates de aprovação.</p></div><div className="flex gap-2"><Button type="button" variant="outline" size="sm" onClick={duplicateDocument}><Copy className="mr-2 h-4 w-4" />Duplicar</Button><Button type="button" variant="outline" size="sm" onClick={() => replaceDocument({ ...selectedDocument, status: 'archived', active: false })}><Archive className="mr-2 h-4 w-4" />Arquivar</Button><Button type="button" size="icon" variant="ghost" aria-label="Excluir modelo do rascunho" onClick={() => onChange({ ...content, documentTemplates: content.documentTemplates.filter((item) => item.id !== selectedDocument.id) })}><Trash2 className="h-4 w-4 text-[#a53e3c]" /></Button></div></div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><div className="space-y-1.5 sm:col-span-2"><Label>Nome</Label><Input value={selectedDocument.name} onChange={(event) => replaceDocument({ ...selectedDocument, name: event.target.value })} /></div><div className="space-y-1.5"><Label>Tipo</Label><Select value={selectedDocument.documentType} onValueChange={(value: PlaybookDocumentTemplate['documentType']) => replaceDocument({ ...selectedDocument, documentType: value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{['proposal', 'contract', 'power_of_attorney', 'petition', 'client_update', 'meeting_brief', 'email', 'whatsapp_pdf', 'invoice', 'other'].map((value) => <SelectItem key={value} value={value}>{value.replaceAll('_', ' ')}</SelectItem>)}</SelectContent></Select></div><div className="space-y-1.5"><Label>Status</Label><Select value={selectedDocument.status} onValueChange={(value: PlaybookDocumentTemplate['status']) => replaceDocument({ ...selectedDocument, status: value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="draft">Rascunho</SelectItem><SelectItem value="approved">Aprovado</SelectItem><SelectItem value="archived">Arquivado</SelectItem></SelectContent></Select></div><div className="space-y-1.5"><Label>Canal</Label><Select value={selectedDocument.channel} onValueChange={(value: PlaybookDocumentTemplate['channel']) => replaceDocument({ ...selectedDocument, channel: value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{['document', 'whatsapp', 'email', 'internal'].map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></div><div className="space-y-1.5"><Label>Saída</Label><Select value={selectedDocument.outputFormat} onValueChange={(value: PlaybookDocumentTemplate['outputFormat']) => replaceDocument({ ...selectedDocument, outputFormat: value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{['pdf', 'docx', 'google_doc', 'html', 'message'].map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></div><div className="space-y-1.5"><Label>Layout</Label><Select value={selectedDocument.layout} onValueChange={(value: PlaybookDocumentTemplate['layout']) => replaceDocument({ ...selectedDocument, layout: value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{['slides_16_9', 'whatsapp_vertical', 'letterhead_a4', 'document_a4', 'message'].map((value) => <SelectItem key={value} value={value}>{value.replaceAll('_', ' ')}</SelectItem>)}</SelectContent></Select></div><div className="space-y-1.5"><Label>Versão</Label><Input type="number" min={1} value={selectedDocument.templateVersion} onChange={(event) => replaceDocument({ ...selectedDocument, templateVersion: Number(event.target.value) || 1 })} /></div></div>
            <div className="grid gap-3 sm:grid-cols-2"><div className="space-y-1.5"><Label>Etapas</Label><Input value={csv(selectedDocument.applicableStages)} onChange={(event) => replaceDocument({ ...selectedDocument, applicableStages: parseCsv(event.target.value) })} /></div><div className="space-y-1.5"><Label>Subtemas</Label><Input value={csv(selectedDocument.applicableSubthemes)} onChange={(event) => replaceDocument({ ...selectedDocument, applicableSubthemes: parseCsv(event.target.value) })} /></div><div className="space-y-1.5"><Label>Agentes autorizados</Label><Input value={csv(selectedDocument.agentKeys)} onChange={(event) => replaceDocument({ ...selectedDocument, agentKeys: parseCsv(event.target.value) })} /></div><div className="space-y-1.5"><Label>Variáveis obrigatórias</Label><Input value={csv(selectedDocument.requiredVariables)} onChange={(event) => replaceDocument({ ...selectedDocument, requiredVariables: parseCsv(event.target.value) })} /></div></div>
            <div className="flex flex-wrap items-center gap-3 border-y py-3"><input ref={sourceInputRef} type="file" accept="application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/html" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) sourceUpload.mutate(file); event.currentTarget.value = ''; }} /><Button type="button" variant="outline" size="sm" onClick={() => sourceInputRef.current?.click()} disabled={sourceUpload.isPending}>{sourceUpload.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}Arquivo-fonte</Button>{selectedDocument.sourceObjectId && selectedDocument.sourceObjectVersion ? <><span className="text-[10px] text-muted-foreground">{selectedDocument.sourceFilename} · v{selectedDocument.sourceObjectVersion}</span><Button type="button" size="sm" variant="ghost" onClick={() => void downloadLocalStorageObject(selectedDocument.sourceObjectId!, selectedDocument.sourceObjectVersion!).then((blob) => saveBlob(blob, selectedDocument.sourceFilename ?? `${selectedDocument.name}.bin`))}><Download className="mr-2 h-4 w-4" />Baixar</Button></> : <span className="text-[10px] text-muted-foreground">Estrutura nativa, sem arquivo externo.</span>}<span className="ml-auto flex items-center gap-2 text-[10px]"><Switch checked={selectedDocument.approvalRequired} onCheckedChange={(checked) => replaceDocument({ ...selectedDocument, approvalRequired: checked })} />Aprovação</span><span className="flex items-center gap-2 text-[10px]"><Switch checked={selectedDocument.active} onCheckedChange={(checked) => replaceDocument({ ...selectedDocument, active: checked })} />Ativo</span></div>
            <section><div className="flex items-center justify-between"><div><h4 className="text-[13px] font-semibold">Seções</h4><p className="text-[9px] text-muted-foreground">Ordem de geração e conteúdo com variáveis controladas.</p></div><Button type="button" variant="outline" size="sm" onClick={() => { const section = { id: identifier('section'), title: 'Nova seção', body: 'Conteúdo sujeito à revisão.', required: true }; replaceDocument({ ...selectedDocument, sections: [...selectedDocument.sections, section] }); setSelectedSectionId(section.id); }}><Plus className="mr-2 h-4 w-4" />Seção</Button></div><div className="mt-3 space-y-3">{selectedDocument.sections.map((section, index) => <div key={section.id} className={cn('border p-3', selectedSectionId === section.id && 'border-primary')} onClick={() => setSelectedSectionId(section.id)}><div className="flex gap-2"><Input value={section.title} onChange={(event) => replaceDocument({ ...selectedDocument, sections: selectedDocument.sections.map((item) => item.id === section.id ? { ...item, title: event.target.value } : item) })} /><Button type="button" size="icon" variant="ghost" aria-label="Mover seção para cima" disabled={index === 0} onClick={() => { const sections = [...selectedDocument.sections]; [sections[index - 1], sections[index]] = [sections[index], sections[index - 1]]; replaceDocument({ ...selectedDocument, sections }); }}><ArrowUp className="h-4 w-4" /></Button><Button type="button" size="icon" variant="ghost" aria-label="Mover seção para baixo" disabled={index === selectedDocument.sections.length - 1} onClick={() => { const sections = [...selectedDocument.sections]; [sections[index + 1], sections[index]] = [sections[index], sections[index + 1]]; replaceDocument({ ...selectedDocument, sections }); }}><ArrowDown className="h-4 w-4" /></Button><Button type="button" size="icon" variant="ghost" aria-label="Excluir seção" onClick={() => replaceDocument({ ...selectedDocument, sections: selectedDocument.sections.filter((item) => item.id !== section.id) })}><Trash2 className="h-4 w-4 text-[#a53e3c]" /></Button></div><Textarea className="mt-2 min-h-28" value={section.body} onChange={(event) => replaceDocument({ ...selectedDocument, sections: selectedDocument.sections.map((item) => item.id === section.id ? { ...item, body: event.target.value } : item) })} /><div className="mt-2 flex flex-wrap items-center gap-2"><Select onValueChange={(key) => replaceDocument({ ...selectedDocument, sections: selectedDocument.sections.map((item) => item.id === section.id ? { ...item, body: `${item.body}${item.body.endsWith(' ') ? '' : ' '}{{${key}}}` } : item) })}><SelectTrigger className="h-8 w-56"><SelectValue placeholder="Inserir variável" /></SelectTrigger><SelectContent>{content.documentVariables.map((variable) => <SelectItem key={variable.key} value={variable.key}>{variable.label} · {variable.key}</SelectItem>)}</SelectContent></Select><span className="text-[9px] text-muted-foreground">{variablesIn(section.body).length} variável(is) nesta seção</span><span className="ml-auto flex items-center gap-2 text-[9px]"><Switch checked={section.required} onCheckedChange={(checked) => replaceDocument({ ...selectedDocument, sections: selectedDocument.sections.map((item) => item.id === section.id ? { ...item, required: checked } : item) })} />Obrigatória</span></div></div>)}</div></section>
          </div> : <EmptySelection label="um modelo" />}</div>
        </TabsContent>

        <TabsContent value="faq" className="m-0 grid min-h-[580px] lg:grid-cols-[310px_minmax(0,1fr)]">
          <aside className="border-r bg-secondary/10 p-3"><div className="flex gap-2"><div className="relative min-w-0 flex-1"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input className="pl-9" value={faqSearch} onChange={(event) => setFaqSearch(event.target.value)} placeholder="Buscar pergunta" /></div><Button type="button" size="icon" aria-label="Adicionar FAQ" onClick={() => { const next: PlaybookFaqEntry = { id: identifier('faq'), category: 'Nova categoria', question: 'Nova pergunta', approvedAnswer: 'Resposta sujeita à revisão.', keywords: [], sourceRefs: [], allowedChannels: ['whatsapp'], applicableStages: [], agentKeys: [], confidenceThreshold: 0.9, requiresHumanReview: true, active: false }; onChange({ ...content, faqs: [...content.faqs, next] }); setSelectedFaqId(next.id); }}><Plus className="h-4 w-4" /></Button></div><ScrollArea className="mt-3 h-[510px]"><div className="space-y-1.5 pr-3">{faqs.map((item) => <button key={item.id} type="button" onClick={() => setSelectedFaqId(item.id)} className={cn('w-full border px-3 py-2.5 text-left', selectedFaqId === item.id ? 'border-primary bg-primary/10' : 'bg-background hover:border-primary/50')}><p className="text-[9px] font-semibold uppercase text-muted-foreground">{item.category}</p><p className="mt-1 line-clamp-2 text-[11px] font-medium">{item.question}</p><div className="mt-2 flex gap-1"><Badge variant={item.active ? 'default' : 'outline'}>{item.active ? 'ativa' : 'inativa'}</Badge>{item.requiresHumanReview && <Badge variant="secondary">revisão</Badge>}</div></button>)}</div></ScrollArea></aside>
          <div className="p-4 lg:p-5">{selectedFaq ? <div className="space-y-4"><div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3"><div><h3 className="text-[15px] font-semibold">Resposta aprovada</h3><p className="text-[10px] text-muted-foreground">O sandbox e os agentes usam texto, fonte, confiança, canal e etapa desta versão.</p></div><div className="flex gap-2"><Button type="button" variant="outline" size="sm" onClick={() => { const next = { ...structuredClone(selectedFaq), id: identifier('faq'), question: `${selectedFaq.question} · cópia`, active: false }; onChange({ ...content, faqs: [...content.faqs, next] }); setSelectedFaqId(next.id); }}><Copy className="mr-2 h-4 w-4" />Duplicar</Button><Button type="button" variant="ghost" size="icon" aria-label="Excluir FAQ do rascunho" onClick={() => onChange({ ...content, faqs: content.faqs.filter((item) => item.id !== selectedFaq.id) })}><Trash2 className="h-4 w-4 text-[#a53e3c]" /></Button></div></div><div className="grid gap-3 sm:grid-cols-2"><div className="space-y-1.5"><Label>Categoria</Label><Input value={selectedFaq.category} onChange={(event) => replaceFaq({ ...selectedFaq, category: event.target.value })} /></div><div className="space-y-1.5"><Label>Confiança mínima</Label><Input type="number" min={0.5} max={1} step={0.01} value={selectedFaq.confidenceThreshold} onChange={(event) => replaceFaq({ ...selectedFaq, confidenceThreshold: Number(event.target.value) || 0.9 })} /></div><div className="space-y-1.5 sm:col-span-2"><Label>Pergunta</Label><Input value={selectedFaq.question} onChange={(event) => replaceFaq({ ...selectedFaq, question: event.target.value })} /></div><div className="space-y-1.5 sm:col-span-2"><Label>Resposta aprovada</Label><Textarea className="min-h-40" value={selectedFaq.approvedAnswer} onChange={(event) => replaceFaq({ ...selectedFaq, approvedAnswer: event.target.value })} /></div><div className="space-y-1.5 sm:col-span-2"><Label>Palavras e intenções</Label><Input value={csv(selectedFaq.keywords)} onChange={(event) => replaceFaq({ ...selectedFaq, keywords: parseCsv(event.target.value) })} /></div><div className="space-y-1.5 sm:col-span-2"><Label>Fontes obrigatórias</Label><Input value={csv(selectedFaq.sourceRefs)} onChange={(event) => replaceFaq({ ...selectedFaq, sourceRefs: parseCsv(event.target.value) })} /></div><div className="space-y-1.5"><Label>Canais</Label><Input value={csv(selectedFaq.allowedChannels)} onChange={(event) => replaceFaq({ ...selectedFaq, allowedChannels: parseCsv(event.target.value) })} /></div><div className="space-y-1.5"><Label>Etapas</Label><Input value={csv(selectedFaq.applicableStages)} onChange={(event) => replaceFaq({ ...selectedFaq, applicableStages: parseCsv(event.target.value) })} /></div><div className="space-y-1.5 sm:col-span-2"><Label>Agentes</Label><Input value={csv(selectedFaq.agentKeys)} onChange={(event) => replaceFaq({ ...selectedFaq, agentKeys: parseCsv(event.target.value) })} /></div></div><div className="flex flex-wrap gap-5 border-t pt-4 text-[10px]"><span className="flex items-center gap-2"><Switch checked={selectedFaq.requiresHumanReview} onCheckedChange={(checked) => replaceFaq({ ...selectedFaq, requiresHumanReview: checked })} />Exigir revisão humana</span><span className="flex items-center gap-2"><Switch checked={selectedFaq.active} onCheckedChange={(checked) => replaceFaq({ ...selectedFaq, active: checked })} />Ativa no runtime publicado</span></div></div> : <EmptySelection label="uma FAQ" />}</div>
        </TabsContent>

        <TabsContent value="responses" className="m-0 grid min-h-[580px] lg:grid-cols-[310px_minmax(0,1fr)]">
          <aside className="border-r bg-secondary/10 p-3"><div className="flex gap-2"><div className="relative min-w-0 flex-1"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input className="pl-9" value={responseSearch} onChange={(event) => setResponseSearch(event.target.value)} placeholder="Buscar resposta" /></div><Button type="button" size="icon" aria-label="Adicionar resposta" onClick={() => { const next: PlaybookResponseTemplate = { id: identifier('reply'), name: 'Nova resposta', intent: 'custom', channel: 'whatsapp', stage: 'triagem', body: 'Resposta sujeita à revisão.', variables: [], agentKeys: [], approvalRequired: true, active: false }; onChange({ ...content, responseTemplates: [...content.responseTemplates, next] }); setSelectedResponseId(next.id); }}><Plus className="h-4 w-4" /></Button></div><ScrollArea className="mt-3 h-[510px]"><div className="space-y-1.5 pr-3">{responses.map((item) => <button key={item.id} type="button" onClick={() => setSelectedResponseId(item.id)} className={cn('w-full border px-3 py-2.5 text-left', selectedResponseId === item.id ? 'border-primary bg-primary/10' : 'bg-background hover:border-primary/50')}><div className="flex items-center justify-between gap-2"><span className="truncate text-[11px] font-semibold">{item.name}</span><Badge variant={item.active ? 'default' : 'outline'}>{item.channel}</Badge></div><p className="mt-1 line-clamp-2 text-[9px] text-muted-foreground">{item.body}</p></button>)}</div></ScrollArea></aside>
          <div className="p-4 lg:p-5">{selectedResponse ? <div className="space-y-4"><div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3"><div><h3 className="text-[15px] font-semibold">Mensagem reutilizável</h3><p className="text-[10px] text-muted-foreground">Atalho aprovado para humano, agente ou flow, sem perder variáveis e gates.</p></div><div className="flex gap-2"><Button type="button" variant="outline" size="sm" onClick={() => { const next = { ...structuredClone(selectedResponse), id: identifier('reply'), name: `${selectedResponse.name} · cópia`, active: false }; onChange({ ...content, responseTemplates: [...content.responseTemplates, next] }); setSelectedResponseId(next.id); }}><Copy className="mr-2 h-4 w-4" />Duplicar</Button><Button type="button" size="icon" variant="ghost" aria-label="Excluir resposta do rascunho" onClick={() => onChange({ ...content, responseTemplates: content.responseTemplates.filter((item) => item.id !== selectedResponse.id) })}><Trash2 className="h-4 w-4 text-[#a53e3c]" /></Button></div></div><div className="grid gap-3 sm:grid-cols-2"><div className="space-y-1.5"><Label>Nome</Label><Input value={selectedResponse.name} onChange={(event) => replaceResponse({ ...selectedResponse, name: event.target.value })} /></div><div className="space-y-1.5"><Label>Intenção</Label><Input value={selectedResponse.intent} onChange={(event) => replaceResponse({ ...selectedResponse, intent: event.target.value })} /></div><div className="space-y-1.5"><Label>Canal</Label><Select value={selectedResponse.channel} onValueChange={(value: PlaybookResponseTemplate['channel']) => replaceResponse({ ...selectedResponse, channel: value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{['whatsapp', 'instagram', 'messenger', 'email', 'internal'].map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></div><div className="space-y-1.5"><Label>Etapa</Label><Input value={selectedResponse.stage} onChange={(event) => replaceResponse({ ...selectedResponse, stage: event.target.value })} /></div><div className="space-y-1.5 sm:col-span-2"><Label>Texto</Label><Textarea className="min-h-40" value={selectedResponse.body} onChange={(event) => replaceResponse({ ...selectedResponse, body: event.target.value, variables: [...new Set([...selectedResponse.variables, ...variablesIn(event.target.value)])] })} /></div><div className="space-y-1.5"><Label>Variáveis</Label><Input value={csv(selectedResponse.variables)} onChange={(event) => replaceResponse({ ...selectedResponse, variables: parseCsv(event.target.value) })} /></div><div className="space-y-1.5"><Label>Agentes</Label><Input value={csv(selectedResponse.agentKeys)} onChange={(event) => replaceResponse({ ...selectedResponse, agentKeys: parseCsv(event.target.value) })} /></div></div><div className="flex flex-wrap items-center gap-3"><Select onValueChange={(key) => replaceResponse({ ...selectedResponse, body: `${selectedResponse.body}${selectedResponse.body.endsWith(' ') ? '' : ' '}{{${key}}}`, variables: [...new Set([...selectedResponse.variables, key])] })}><SelectTrigger className="w-64"><SelectValue placeholder="Inserir variável" /></SelectTrigger><SelectContent>{content.documentVariables.map((variable) => <SelectItem key={variable.key} value={variable.key}>{variable.label} · {variable.key}</SelectItem>)}</SelectContent></Select><span className="ml-auto flex items-center gap-2 text-[10px]"><Switch checked={selectedResponse.approvalRequired} onCheckedChange={(checked) => replaceResponse({ ...selectedResponse, approvalRequired: checked })} />Aprovação</span><span className="flex items-center gap-2 text-[10px]"><Switch checked={selectedResponse.active} onCheckedChange={(checked) => replaceResponse({ ...selectedResponse, active: checked })} />Ativa</span></div></div> : <EmptySelection label="uma resposta" />}</div>
        </TabsContent>

        <TabsContent value="variables" className="m-0 grid min-h-[580px] lg:grid-cols-[310px_minmax(0,1fr)]">
          <aside className="border-r bg-secondary/10 p-3"><div className="flex gap-2"><div className="relative min-w-0 flex-1"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input className="pl-9" value={variableSearch} onChange={(event) => setVariableSearch(event.target.value)} placeholder="Buscar variável" /></div><Button type="button" size="icon" aria-label="Adicionar variável" onClick={() => { const key = `manual.${crypto.randomUUID().slice(0, 8)}`; const next: PlaybookDocumentVariable = { key, label: 'Nova variável', source: 'manual', sourcePath: 'manual.value', required: false, sensitive: false, allowAgentWrite: false, fallbackPolicy: 'human_input', fallbackText: null }; onChange({ ...content, documentVariables: [...content.documentVariables, next] }); setSelectedVariableKey(key); }}><Plus className="h-4 w-4" /></Button></div><ScrollArea className="mt-3 h-[510px]"><div className="space-y-1.5 pr-3">{variables.map((item) => <button key={item.key} type="button" onClick={() => setSelectedVariableKey(item.key)} className={cn('w-full border px-3 py-2.5 text-left', selectedVariableKey === item.key ? 'border-primary bg-primary/10' : 'bg-background hover:border-primary/50')}><p className="truncate font-mono text-[10px] font-semibold">{`{{${item.key}}}`}</p><p className="mt-1 truncate text-[9px] text-muted-foreground">{item.label} · {item.source}</p></button>)}</div></ScrollArea></aside>
          <div className="p-4 lg:p-5">{selectedVariable ? <div className="space-y-4"><div className="flex items-center justify-between border-b pb-3"><div><h3 className="text-[15px] font-semibold">Contrato da variável</h3><p className="text-[10px] text-muted-foreground">Origem, sensibilidade, fallback e permissão de escrita do agente.</p></div><Button type="button" size="icon" variant="ghost" aria-label="Excluir variável do rascunho" onClick={() => onChange({ ...content, documentVariables: content.documentVariables.filter((item) => item.key !== selectedVariable.key) })}><Trash2 className="h-4 w-4 text-[#a53e3c]" /></Button></div><div className="grid gap-3 sm:grid-cols-2"><div className="space-y-1.5"><Label>Chave</Label><Input value={selectedVariable.key} onChange={(event) => replaceVariable(selectedVariable.key, { ...selectedVariable, key: event.target.value })} /></div><div className="space-y-1.5"><Label>Nome</Label><Input value={selectedVariable.label} onChange={(event) => replaceVariable(selectedVariable.key, { ...selectedVariable, label: event.target.value })} /></div><div className="space-y-1.5"><Label>Fonte</Label><Select value={selectedVariable.source} onValueChange={(value: PlaybookDocumentVariable['source']) => replaceVariable(selectedVariable.key, { ...selectedVariable, source: value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{['brand', 'client', 'case', 'conversation', 'document', 'financial', 'meeting', 'manual', 'system'].map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></div><div className="space-y-1.5"><Label>Caminho da fonte</Label><Input value={selectedVariable.sourcePath} onChange={(event) => replaceVariable(selectedVariable.key, { ...selectedVariable, sourcePath: event.target.value })} /></div><div className="space-y-1.5"><Label>Fallback</Label><Select value={selectedVariable.fallbackPolicy} onValueChange={(value: PlaybookDocumentVariable['fallbackPolicy']) => replaceVariable(selectedVariable.key, { ...selectedVariable, fallbackPolicy: value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="hard_gate">Bloquear execução</SelectItem><SelectItem value="human_input">Pedir ao humano</SelectItem><SelectItem value="omit">Omitir campo</SelectItem></SelectContent></Select></div><div className="space-y-1.5"><Label>Texto alternativo</Label><Input value={selectedVariable.fallbackText ?? ''} onChange={(event) => replaceVariable(selectedVariable.key, { ...selectedVariable, fallbackText: event.target.value || null })} /></div></div><div className="flex flex-wrap gap-5 border-t pt-4 text-[10px]"><span className="flex items-center gap-2"><Switch checked={selectedVariable.required} onCheckedChange={(checked) => replaceVariable(selectedVariable.key, { ...selectedVariable, required: checked })} />Obrigatória</span><span className="flex items-center gap-2"><Switch checked={selectedVariable.sensitive} onCheckedChange={(checked) => replaceVariable(selectedVariable.key, { ...selectedVariable, sensitive: checked })} />Dado sensível</span><span className="flex items-center gap-2"><Switch checked={selectedVariable.allowAgentWrite} onCheckedChange={(checked) => replaceVariable(selectedVariable.key, { ...selectedVariable, allowAgentWrite: checked })} />Agente pode escrever</span></div></div> : <EmptySelection label="uma variável" />}</div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
