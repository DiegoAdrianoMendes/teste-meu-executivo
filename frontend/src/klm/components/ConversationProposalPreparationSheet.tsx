import { FileSignature, LoaderCircle, RefreshCw, ShieldCheck } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import type { KlmMatterDetail } from '@/klm/api';
import { KlmPill } from '@/klm/components/Primitives';

type ConversationProposalPreparationSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  contactName: string;
  matter?: KlmMatterDetail;
  loading?: boolean;
  failed?: boolean;
  submitting?: boolean;
  onRetry: () => void;
  onOpenCase: () => void;
  onConfirm: () => void;
};

const currency = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', minimumFractionDigits: 2, maximumFractionDigits: 2 });

function money(value: number | null | undefined) {
  return value === null || value === undefined ? 'Não definido' : currency.format(value);
}

export function ConversationProposalPreparationSheet({
  open,
  onOpenChange,
  contactName,
  matter,
  loading = false,
  failed = false,
  submitting = false,
  onRetry,
  onOpenCase,
  onConfirm,
}: ConversationProposalPreparationSheetProps) {
  const premises = matter?.revenueProjection?.projectionPremises;
  const initialFee = premises?.fixedFee ?? matter?.honorariumEstimate?.fixedFee;
  const projectedRevenue = matter?.revenueProjection?.projectedRevenue ?? matter?.honorariumEstimate?.totalEstimatedValue;
  const reviewStatus = premises?.reviewStatus ?? 'rascunho';

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-[96vw] flex-col overflow-hidden p-0 sm:max-w-[620px]">
        <SheetHeader className="border-b border-border px-5 py-5 pr-12 text-left">
          <div className="mb-2 flex h-9 w-9 items-center justify-center rounded-md bg-[#031121] text-primary dark:bg-white dark:text-[#0057d8]"><FileSignature className="h-4 w-4" /></div>
          <SheetTitle>Preparar proposta</SheetTitle>
          <SheetDescription>Revise o contexto que será versionado antes de gerar a proposta de {contactName || 'este caso'}. A conversa permanece aberta ao fundo.</SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5 scrollbar-thin">
          {loading ? <div className="flex min-h-52 items-center justify-center gap-2 text-2xs text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />Conferindo Caso 360, honorários e versões</div> : null}
          {failed ? <div className="flex min-h-52 flex-col items-center justify-center rounded-md border border-red-200 bg-red-50 p-6 text-center dark:border-red-900/70 dark:bg-red-950/30"><p className="text-2xs font-semibold">Não foi possível carregar os dados atuais do caso</p><p className="mt-1 max-w-sm text-2xs leading-4 text-muted-foreground">A proposta não será criada com contexto incompleto.</p><Button variant="outline" size="sm" className="mt-3 h-8 text-2xs" onClick={onRetry}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />Conferir novamente</Button></div> : null}
          {!loading && !failed && matter ? <>
            <section className="rounded-md border border-border bg-card p-4">
              <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-2xs font-semibold uppercase text-muted-foreground">Cliente e demanda</p><h3 className="mt-1 text-sm font-semibold">{matter.client.name}</h3><p className="mt-1 text-2xs text-muted-foreground">{matter.demandType} · {matter.healthObjectName || 'Objeto não informado'}</p></div><KlmPill tone={reviewStatus === 'aprovado' ? 'green' : 'gold'}>{reviewStatus}</KlmPill></div>
              <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-border pt-4 sm:grid-cols-4">
                <div><span className="block text-2xs text-muted-foreground">Entrada</span><strong className="mt-1 block text-2xs">{money(initialFee)}</strong></div>
                <div><span className="block text-2xs text-muted-foreground">Projeção</span><strong className="mt-1 block text-2xs">{money(projectedRevenue)}</strong></div>
                <div><span className="block text-2xs text-muted-foreground">Score do lead</span><strong className="mt-1 block text-2xs">{matter.leadScore}%</strong></div>
                <div><span className="block text-2xs text-muted-foreground">Documentos</span><strong className="mt-1 block text-2xs">{matter.docScore}%</strong></div>
              </div>
            </section>

            <section className="space-y-2">
              <h3 className="text-sm font-semibold">O que será feito</h3>
              {[
                'Conferir a versão atual do funil e mover o caso para Proposta com controle de concorrência.',
                'Criar uma versão persistida da proposta usando os dados jurídicos e financeiros atuais.',
                'Abrir a prévia vertical dentro desta conversa e inserir o link somente no rascunho.',
                'Registrar zero envios externos até a confirmação humana no composer.',
              ].map((item) => <div key={item} className="flex items-start gap-2 rounded-md border border-border px-3 py-2.5 text-2xs leading-4"><ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0b7435] dark:text-emerald-400" /><span>{item}</span></div>)}
            </section>

            {reviewStatus !== 'aprovado' ? <div className="border-l-2 border-primary bg-primary/5 px-3 py-2.5 text-2xs leading-4"><strong>Revisão financeira pendente.</strong> A versão será criada como rascunho e continuará sujeita à aprovação humana.</div> : null}
          </> : null}
        </div>

        <SheetFooter className="gap-2 border-t border-border bg-card px-5 py-4">
          <Button type="button" variant="outline" onClick={onOpenCase}>Revisar Caso 360</Button>
          <Button type="button" disabled={!matter || loading || failed || submitting} onClick={onConfirm}>{submitting ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <FileSignature className="mr-2 h-4 w-4" />}{submitting ? 'Gerando versão' : 'Gerar proposta e abrir prévia'}</Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
