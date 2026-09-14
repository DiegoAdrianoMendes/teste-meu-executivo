import { useMemo, useState } from 'react';
import { useAppStore } from '@/store/app-context';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { PriorityBadge } from '@/components/common/PriorityBadge';
import { DocScoreBar } from '@/components/common/DocScoreBar';
import { ClassificationChip } from '@/components/common/ClassificationChip';
import { Textarea } from '@/components/ui/textarea';
import { Check, X, RotateCcw, ArrowLeft, AlertTriangle, ExternalLink, FileText, MessagesSquare } from 'lucide-react';
import type { ApprovalItem } from '@/types';
import { fetchDocumentDownloadUrl } from '@/services/matters';
import { toast } from '@/components/ui/use-toast';
import { formatBRL } from '@/lib/formatters';

interface Props {
  item: ApprovalItem;
  onBack: () => void;
}

export function ApprovalReviewPanel({ item, onBack }: Props) {
  const { state, resolveApproval, can } = useAppStore();
  const card = state.cards.find(c => c.id === item.cardId);
  const [notes, setNotes] = useState(item.resolutionReason ?? '');
  const [loading, setLoading] = useState(false);
  const [openingDocumentId, setOpeningDocumentId] = useState<string | null>(null);

  const contextualDocuments = useMemo(() => {
    if (!card) return [];
    return card.documents.slice(0, 5);
  }, [card]);

  if (!card) return null;

  const handleAction = async (action: 'approved' | 'rejected' | 'changes_requested') => {
    setLoading(true);
    try {
      await resolveApproval(item.id, action, notes.trim() || undefined);
      onBack();
    } finally {
      setLoading(false);
    }
  };

  const handleOpenDocument = async (documentId: string) => {
    setOpeningDocumentId(documentId);
    try {
      const downloadUrl = await fetchDocumentDownloadUrl(documentId);
      window.open(downloadUrl, '_blank', 'noopener,noreferrer');
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Documento indisponível',
        description: error instanceof Error ? error.message : 'Não foi possível emitir o link temporário.',
      });
    } finally {
      setOpeningDocumentId(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={onBack} className="gap-1 text-xs shrink-0">
            <ArrowLeft className="h-3.5 w-3.5" /> Voltar
          </Button>
          <div className="min-w-0">
            <h3 className="text-lg font-semibold truncate">{item.title}</h3>
            <p className="text-xs text-muted-foreground">
              Solicitado por {item.requestedBy || 'Equipe'} em {new Date(item.requestedAt).toLocaleDateString('pt-BR')}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 pl-10 sm:pl-0">
          <PriorityBadge priority={item.priority} />
          <Badge variant="outline" className="text-xs">{item.type}</Badge>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-2 space-y-3">
          <Card style={{ boxShadow: 'var(--shadow-card)' }}>
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-medium">Contexto do Card</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground shrink-0">Cliente</span>
                <span className="min-w-0 text-sm font-semibold truncate ml-2">{card.client.name}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Classificação</span>
                <ClassificationChip classification={card.classification} />
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-muted-foreground shrink-0">Demanda</span>
                <span className="min-w-0 text-sm truncate">{card.demandType}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Valor</span>
                <span className="text-sm font-semibold tabular-nums">{card.claimValue ? formatBRL(card.claimValue) : '—'}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Score documental</span>
                <DocScoreBar score={card.docScore} compact />
              </div>
            </CardContent>
          </Card>

          {card.conversationInsight && (
            <Card style={{ boxShadow: 'var(--shadow-card)' }}>
              <CardHeader className="pb-2">
                <CardTitle className="text-base font-medium flex items-center gap-1.5">
                  <MessagesSquare className="h-3.5 w-3.5 shrink-0" /> Sinal da Conversa
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-xs text-muted-foreground">
                <p>{card.conversationInsight.summary}</p>
                <p>Ação sugerida: {card.conversationInsight.recommendedAction || '—'}</p>
                <p>Prontidão documental: {card.conversationInsight.documentReadiness || '—'}</p>
              </CardContent>
            </Card>
          )}

          {card.risks.length > 0 && (
            <Card style={{ boxShadow: 'var(--shadow-card)' }}>
              <CardHeader className="pb-2">
                <CardTitle className="text-base font-medium flex items-center gap-1.5">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" /> Riscos
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-1">
                  {card.risks.map((risk, index) => (
                    <li key={index} className="text-xs text-muted-foreground flex items-start gap-1.5">
                      <span className="text-amber-500 mt-0.5">•</span> {risk}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          <Card style={{ boxShadow: 'var(--shadow-card)' }}>
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-medium">Feedback da revisão</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="Explique o motivo da rejeição ou o ajuste solicitado."
                className="min-h-[120px] text-sm"
              />
              <div className="flex items-center gap-2 pt-2">
                <Button size="sm" className="flex-1 h-9 text-xs gap-1.5 bg-[hsl(var(--success))] hover:bg-[hsl(var(--success))]/90 text-white" disabled={loading} onClick={() => handleAction('approved')}>
                  <Check className="h-3.5 w-3.5" /> Aprovar
                </Button>
                <Button size="sm" variant="outline" className="flex-1 h-9 text-xs gap-1.5" disabled={loading} onClick={() => handleAction('changes_requested')}>
                  <RotateCcw className="h-3.5 w-3.5" /> Solicitar ajuste
                </Button>
                <Button size="sm" variant="outline" className="flex-1 h-9 text-xs gap-1.5 text-destructive border-destructive/30 hover:bg-destructive/10" disabled={loading} onClick={() => handleAction('rejected')}>
                  <X className="h-3.5 w-3.5" /> Rejeitar
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>

        <div className="lg:col-span-3 space-y-4">
          <Card style={{ boxShadow: 'var(--shadow-card)' }}>
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-medium flex items-center gap-1.5">
                <FileText className="h-3.5 w-3.5 shrink-0" /> Conteúdo submetido à aprovação
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-xs text-muted-foreground">
              <p>{item.description || card.executiveSummary || 'Sem descrição detalhada.'}</p>
              {item.documentVersionUrl && (
                <a href={item.documentVersionUrl} target="_blank" rel="noreferrer" className="text-xs text-primary underline">
                  Abrir versão do documento enviada para revisão
                </a>
              )}
            </CardContent>
          </Card>

          <Card style={{ boxShadow: 'var(--shadow-card)' }}>
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-medium">Documentos mais recentes</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {contextualDocuments.length === 0 ? (
                <p className="text-xs text-muted-foreground">Nenhum documento vinculado ao card.</p>
              ) : contextualDocuments.map((document) => (
                <div key={document.id} className="flex items-center justify-between gap-3 rounded-md border p-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{document.name}</p>
                    <p className="text-xs text-muted-foreground">{document.status}</p>
                  </div>
                  {can('lead_data', 'view') ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 shrink-0 gap-1 px-2 text-xs"
                      disabled={openingDocumentId === document.id}
                      onClick={() => void handleOpenDocument(document.id)}
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      {openingDocumentId === document.id ? 'Emitindo' : 'Abrir'}
                    </Button>
                  ) : null}
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
