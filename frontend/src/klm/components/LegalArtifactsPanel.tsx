import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, FileClock, FilePlus2, Loader2, RotateCw, Send, ShieldCheck } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { klmApi, type KlmLegalArtifactKind, type KlmLegalArtifactVersion } from '@/klm/api';

const kindLabels: Record<KlmLegalArtifactKind, string> = {
  proposal: 'Proposta',
  contract: 'Contrato',
  power_of_attorney: 'Procuração',
  petition: 'Petição',
  client_update: 'Atualização ao cliente',
};

const statusLabels: Record<KlmLegalArtifactVersion['status'], string> = {
  draft: 'Rascunho',
  review_pending: 'Em aprovação',
  approved: 'Aprovado',
  rejected: 'Rejeitado',
  changes_requested: 'Ajustes solicitados',
  archived: 'Arquivado',
};

const toneForStatus = (status: KlmLegalArtifactVersion['status']): 'green' | 'gold' | 'red' => {
  if (status === 'approved') return 'green';
  if (status === 'rejected') return 'red';
  return 'gold';
};

type DraftState = {
  matterId: string;
  kind: KlmLegalArtifactKind;
  title: string;
  body: string;
  artifactKey?: string;
  expectedVersion?: number;
};

export function LegalArtifactsPanel({
  kinds,
  matterId,
  title,
  onNavigate,
}: {
  kinds: KlmLegalArtifactKind[];
  matterId?: string | null;
  title: string;
  onNavigate?: (path: string) => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [editorOpen, setEditorOpen] = useState(false);
  const [selected, setSelected] = useState<KlmLegalArtifactVersion | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftState>({
    matterId: matterId ?? '',
    kind: kinds[0],
    title: '',
    body: '',
  });

  useEffect(() => {
    if (matterId) setDraft((current) => ({ ...current, matterId }));
  }, [matterId]);

  const artifactsQuery = useQuery({
    queryKey: ['klm', 'legal-artifacts', matterId ?? 'all', kinds.join(',')],
    queryFn: async () => {
      const batches = await Promise.all(kinds.map((kind) => klmApi.legalArtifacts({ matterId: matterId ?? undefined, kind })));
      return batches.flat().sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt));
    },
  });
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel });
  const cardsById = useMemo(
    () => new Map((funnelQuery.data?.cards ?? []).map((card) => [card.id, card])),
    [funnelQuery.data?.cards],
  );
  const latestArtifacts = useMemo(() => {
    const latest = new Map<string, KlmLegalArtifactVersion>();
    for (const artifact of artifactsQuery.data ?? []) {
      const current = latest.get(artifact.artifactKey);
      if (!current || artifact.version > current.version) latest.set(artifact.artifactKey, artifact);
    }
    return [...latest.values()].sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt));
  }, [artifactsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: () => klmApi.createLegalArtifact({
      matterId: draft.matterId,
      kind: draft.kind,
      title: draft.title.trim(),
      content: {
        body: draft.body.trim(),
        editor: 'klm_native',
        externalEffectsExecuted: 0,
      },
      artifactKey: draft.artifactKey,
      expectedVersion: draft.expectedVersion,
    }),
    onSuccess: (artifact) => {
      setEditorOpen(false);
      setSelected(artifact);
      setNotice(`${kindLabels[artifact.kind]} v${artifact.version} registrada no banco local.`);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'legal-artifacts'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
    },
    onError: () => setNotice('A versão não foi registrada. Os dados anteriores foram preservados.'),
  });
  const submitMutation = useMutation({
    mutationFn: (artifactId: string) => klmApi.submitLegalArtifact(artifactId),
    onSuccess: ({ artifact }) => {
      setSelected(artifact);
      setNotice(`Versão v${artifact.version} enviada para aprovação humana.`);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'legal-artifacts'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'approvals'] });
    },
    onError: () => setNotice('Não foi possível criar a solicitação de aprovação.'),
  });

  const openNew = () => {
    setDraft({ matterId: matterId ?? '', kind: kinds[0], title: '', body: '' });
    setEditorOpen(true);
  };
  const openVersion = (artifact: KlmLegalArtifactVersion) => {
    setDraft({
      matterId: artifact.matterId,
      kind: artifact.kind,
      title: artifact.title,
      body: typeof artifact.content.body === 'string' ? artifact.content.body : JSON.stringify(artifact.content, null, 2),
      artifactKey: artifact.artifactKey,
      expectedVersion: artifact.version,
    });
    setEditorOpen(true);
  };
  const canSave = Boolean(draft.matterId && draft.title.trim().length >= 3 && draft.body.trim());

  return (
    <KlmPanel className="p-4 sm:p-5" data-testid="legal-artifacts-panel">
      <KlmSectionTitle
        title={title}
        description="Conteúdo versionado, vinculado ao caso e submetido à fila humana antes de qualquer entrega externa."
        action={<Button size="sm" className="h-8 bg-[#031121] text-2xs text-white hover:bg-[#031121]" onClick={openNew}><FilePlus2 className="mr-1.5 h-3.5 w-3.5" />Nova minuta</Button>}
      />
      {notice ? <div className="mt-3 flex items-center justify-between gap-3 border-l-2 border-[#b98224] bg-[#b98224]/8 px-3 py-2 text-2xs"><span>{notice}</span><Button variant="ghost" size="sm" className="h-6 px-2 text-2xs" onClick={() => setNotice(null)}>Fechar</Button></div> : null}

      {artifactsQuery.isPending ? (
        <div className="mt-4 flex min-h-32 items-center justify-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Carregando versões persistidas...</div>
      ) : artifactsQuery.isError ? (
        <div className="mt-4 flex items-center justify-between border p-4 text-xs"><span>As versões não puderam ser consultadas.</span><Button variant="outline" size="sm" onClick={() => void artifactsQuery.refetch()}><RotateCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente</Button></div>
      ) : latestArtifacts.length === 0 ? (
        <div className="mt-4 border border-dashed p-6 text-center text-xs text-muted-foreground">Nenhuma minuta deste tipo foi registrada. O botão acima cria a primeira versão ligada a um card.</div>
      ) : (
        <div className="mt-4 divide-y border">
          {latestArtifacts.map((artifact) => {
            const card = cardsById.get(artifact.matterId);
            return (
              <div key={artifact.id} className="grid gap-3 p-4 md:grid-cols-[1fr_auto] md:items-center">
                <button className="min-w-0 text-left" onClick={() => setSelected(artifact)}>
                  <div className="flex flex-wrap items-center gap-2"><span className="text-xs font-semibold">{artifact.title}</span><KlmPill tone={toneForStatus(artifact.status)}>{statusLabels[artifact.status]}</KlmPill></div>
                  <p className="mt-1 truncate text-2xs text-muted-foreground">{kindLabels[artifact.kind]} · v{artifact.version} · {card?.name ?? `caso ${artifact.matterId.slice(0, 8)}`} · {new Date(artifact.createdAt).toLocaleString('pt-BR')}</p>
                </button>
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" className="h-7 text-2xs" onClick={() => openVersion(artifact)}><FileClock className="mr-1.5 h-3 w-3" />Nova versão</Button>
                  <Button size="sm" className="h-7 bg-[#031121] text-2xs text-white" disabled={artifact.status !== 'draft' || submitMutation.isPending} onClick={() => submitMutation.mutate(artifact.id)}><Send className="mr-1.5 h-3 w-3" />Aprovação</Button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Dialog open={editorOpen} onOpenChange={setEditorOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{draft.artifactKey ? 'Criar nova versão' : 'Nova minuta jurídica'}</DialogTitle><DialogDescription>O conteúdo será salvo como uma versão local. Salvar não envia, assina nem protocola nada.</DialogDescription></DialogHeader>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="space-y-1 text-2xs font-medium">Caso
              <Select value={draft.matterId} onValueChange={(value) => setDraft((current) => ({ ...current, matterId: value }))} disabled={Boolean(matterId || draft.artifactKey)}>
                <SelectTrigger className="mt-1 h-9 text-xs"><SelectValue placeholder="Selecione um card" /></SelectTrigger>
                <SelectContent>{(funnelQuery.data?.cards ?? []).map((card) => <SelectItem key={card.id} value={card.id}>{card.name} · {card.object}</SelectItem>)}</SelectContent>
              </Select>
            </label>
            <label className="space-y-1 text-2xs font-medium">Tipo
              <Select value={draft.kind} onValueChange={(value) => setDraft((current) => ({ ...current, kind: value as KlmLegalArtifactKind }))} disabled={Boolean(draft.artifactKey)}>
                <SelectTrigger className="mt-1 h-9 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>{kinds.map((kind) => <SelectItem key={kind} value={kind}>{kindLabels[kind]}</SelectItem>)}</SelectContent>
              </Select>
            </label>
          </div>
          <label className="space-y-1 text-2xs font-medium">Título<Input className="mt-1" value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} /></label>
          <label className="space-y-1 text-2xs font-medium">Conteúdo<Textarea className="mt-1 min-h-60 resize-y text-xs leading-5" value={draft.body} onChange={(event) => setDraft((current) => ({ ...current, body: event.target.value }))} /></label>
          <div className="flex items-start gap-2 border-l-2 border-emerald-700 bg-emerald-50 px-3 py-2 text-2xs text-emerald-900 dark:bg-emerald-950/25 dark:text-emerald-200"><ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />A origem do card e das premissas financeiras será capturada no servidor junto com esta versão.</div>
          <DialogFooter><Button variant="outline" onClick={() => setEditorOpen(false)}>Cancelar</Button><Button className="bg-[#031121] text-white" disabled={!canSave || saveMutation.isPending} onClick={() => saveMutation.mutate()}>{saveMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}Salvar versão</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(selected)} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{selected?.title}</DialogTitle><DialogDescription>{selected ? `${kindLabels[selected.kind]} · versão ${selected.version} · ${statusLabels[selected.status]}` : ''}</DialogDescription></DialogHeader>
          <div className="max-h-[52vh] overflow-y-auto whitespace-pre-wrap border bg-secondary p-4 text-xs leading-5">{selected ? (typeof selected.content.body === 'string' ? selected.content.body : JSON.stringify(selected.content, null, 2)) : ''}</div>
          <DialogFooter><Button variant="outline" onClick={() => { const path = `/app/leads?matter=${selected?.matterId ?? ''}`; if (onNavigate) onNavigate(path); else navigate(path); }}>Abrir caso</Button>{selected?.status === 'draft' ? <Button className="bg-[#031121] text-white" onClick={() => selected && submitMutation.mutate(selected.id)}>Enviar para aprovação</Button> : null}</DialogFooter>
        </DialogContent>
      </Dialog>
    </KlmPanel>
  );
}
