import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LoaderCircle, RefreshCw, ShieldOff } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { klmApi, type KlmSdrQualification, type KlmSdrStep } from '@/klm/api';
import { KlmPill } from '@/klm/components/Primitives';
import { formatBRL } from '@/lib/formatters';

/**
 * Painel de leitura da qualificação estruturada produzida pelo agente de SDR
 * (`Matter.sdr_qualification`), exposta por `GET /api/v1/sdr/matters/{id}`.
 *
 * Duas regras de honestidade guiam a renderização:
 *
 * 1. Sem qualificação persistida o painel diz explicitamente que o lead ainda
 *    não foi qualificado — nunca renderiza campos vazios como se fossem
 *    resposta do agente.
 * 2. Sem a permissão `lead_data.view` o backend aplica `mask_sdr_status`
 *    (app/core/lead_privacy.py), substituindo nome por rótulo genérico,
 *    zerando telefone/empregador e trocando textos por "[conteúdo protegido]".
 *    Nesse caso o painel avisa que a leitura está mascarada por permissão, em
 *    vez de apresentar o recorte como se fosse a qualificação completa.
 */

const stepLabels: Record<KlmSdrStep, string> = {
  inicio: 'Início',
  coletando_nome: 'Coletando nome',
  coletando_demanda: 'Coletando demanda',
  coletando_empregador: 'Coletando empregador',
  coletando_tempo_empresa: 'Coletando tempo de empresa',
  coletando_situacao: 'Coletando situação',
  coletando_documentos: 'Coletando documentos',
  qualificando: 'Qualificando',
  qualificado: 'Qualificado',
  handoff_closer: 'Handoff para o closer',
  handoff_assistente: 'Handoff para o assistente',
  encerrado: 'Encerrado',
};

const handoffLabels: Record<KlmSdrQualification['handoff_target'], string> = {
  closer_juridico: 'Closer jurídico',
  assistente_juridico: 'Assistente jurídico',
  nenhum: 'Nenhum',
};

function stepLabel(step: KlmSdrStep): string {
  return stepLabels[step] ?? String(step).replaceAll('_', ' ');
}

function booleanLabel(value: boolean | null): string | null {
  if (value === null || value === undefined) return null;
  return value ? 'Sim' : 'Não';
}

type FieldRow = { label: string; value: string };

function qualificationRows(qualification: KlmSdrQualification): FieldRow[] {
  const rows: Array<[string, string | null]> = [
    ['Nome do cliente', qualification.client_name],
    ['Telefone', qualification.phone],
    ['Tipo de demanda', qualification.demand_type],
    ['Empregador', qualification.employer],
    ['Tempo de empresa', qualification.time_at_company],
    ['Situação empregatícia', qualification.employment_situation],
    ['Possui CTPS', booleanLabel(qualification.has_ctps)],
    ['Possui documentos', booleanLabel(qualification.has_documents)],
    ['Score de qualificação', qualification.qualification_score == null
      ? null
      : `${qualification.qualification_score}/10`],
    ['Classificação', qualification.classification],
    ['Valor estimado da causa', qualification.estimated_claim_value == null
      ? null
      : formatBRL(qualification.estimated_claim_value)],
    ['Destino do handoff', handoffLabels[qualification.handoff_target]
      ?? String(qualification.handoff_target)],
  ];
  return rows
    .filter((entry): entry is [string, string] => Boolean(entry[1] && entry[1].trim()))
    .map(([label, value]) => ({ label, value }));
}

function textRows(qualification: KlmSdrQualification): FieldRow[] {
  const rows: Array<[string, string | null]> = [
    ['Notas de viabilidade', qualification.viability_notes],
    ['Notas do handoff', qualification.handoff_notes],
    ['Resumo da conversa', qualification.conversation_summary],
  ];
  return rows
    .filter((entry): entry is [string, string] => Boolean(entry[1] && entry[1].trim()))
    .map(([label, value]) => ({ label, value }));
}

export type SdrQualificationPanelProps = {
  matterId: string;
  /**
   * Espelha `can_view_sensitive_lead_data` no backend, que hoje resolve para a
   * permissão `lead_data.view`. Quando falso, o payload chega mascarado.
   */
  canViewSensitiveLeadData: boolean;
  /** Espelha `require_permission("kanban", "edit")` exigida pelo reprocess. */
  canReprocess: boolean;
};

export function SdrQualificationPanel({
  matterId,
  canViewSensitiveLeadData,
  canReprocess,
}: SdrQualificationPanelProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const queryKey = ['klm', 'sdr-status', matterId] as const;
  const statusQuery = useQuery({
    queryKey,
    queryFn: () => klmApi.sdrStatus(matterId),
    enabled: Boolean(matterId),
  });

  const reprocessMutation = useMutation({
    mutationFn: () => klmApi.reprocessSdr(matterId, 'Requalificação solicitada no Caso 360.'),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey });
      toast({ title: 'Requalificação enfileirada', description: result.message });
    },
    onError: (error: unknown) => toast({
      variant: 'destructive',
      title: 'Não foi possível requalificar',
      description: error instanceof Error ? error.message : 'Tente novamente.',
    }),
  });

  const reprocessButton = (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="h-7 text-2xs"
      disabled={!canReprocess || reprocessMutation.isPending || statusQuery.isPending}
      title={canReprocess ? undefined : 'Permissão kanban.edit necessária'}
      onClick={() => reprocessMutation.mutate()}
    >
      {reprocessMutation.isPending
        ? <LoaderCircle className="mr-1.5 h-3 w-3 animate-spin" />
        : <RefreshCw className="mr-1.5 h-3 w-3" />}
      Requalificar
    </Button>
  );

  if (statusQuery.isPending) {
    return (
      <div className="flex items-center gap-2 text-2xs text-muted-foreground">
        <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
        Carregando a qualificação do SDR
      </div>
    );
  }

  if (statusQuery.isError || !statusQuery.data) {
    return (
      <div className="space-y-2">
        <p className="text-2xs leading-4 text-muted-foreground">
          Não foi possível carregar a qualificação do SDR deste caso.
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 text-2xs"
          onClick={() => void statusQuery.refetch()}
        >
          Tentar novamente
        </Button>
      </div>
    );
  }

  const status = statusQuery.data;
  const qualification = status.qualification;
  const rows = qualification ? qualificationRows(qualification) : [];
  const notes = qualification ? textRows(qualification) : [];
  const documentsRequested = qualification?.documents_requested ?? [];
  const documentsReceived = qualification?.documents_received ?? [];

  return (
    <div className="space-y-3" data-testid="sdr-qualification-panel">
      <div className="flex flex-wrap items-center gap-1.5">
        <KlmPill tone={status.sdr_completed ? 'green' : 'gold'}>
          Passo: {stepLabel(status.sdr_step)}
        </KlmPill>
        <KlmPill tone={status.sdr_completed ? 'green' : undefined}>
          {status.sdr_completed ? 'Qualificação concluída' : 'Qualificação em andamento'}
        </KlmPill>
        <span className="ml-auto">{reprocessButton}</span>
      </div>

      {!canViewSensitiveLeadData && (
        <p
          className="flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-2xs leading-4"
          role="note"
        >
          <ShieldOff className="mt-0.5 h-3 w-3 shrink-0" />
          <span>
            Leitura mascarada por permissão: sem <code>lead_data.view</code> o servidor oculta
            nome, telefone, empregador e os textos livres desta qualificação. O que aparece abaixo
            é um recorte, não a qualificação completa.
          </span>
        </p>
      )}

      {!qualification ? (
        <p className="text-2xs leading-4 text-muted-foreground">
          Este lead ainda não foi qualificado pelo SDR. Nenhum campo estruturado foi persistido
          neste caso.
        </p>
      ) : (
        <>
          {rows.length ? (
            <dl className="grid grid-cols-2 gap-2 text-2xs">
              {rows.map((row) => (
                <div key={row.label} className="min-w-0">
                  <dt className="text-muted-foreground">{row.label}</dt>
                  <dd className="mt-0.5 break-words font-semibold">{row.value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-2xs leading-4 text-muted-foreground">
              O SDR abriu a qualificação, mas ainda não preencheu nenhum campo estruturado.
            </p>
          )}

          {documentsRequested.length ? (
            <div className="text-2xs">
              <p className="text-muted-foreground">Documentos solicitados</p>
              <div className="mt-1 flex flex-wrap gap-1">
                {documentsRequested.map((document) => (
                  <KlmPill key={`requested-${document}`} tone="gold">{document}</KlmPill>
                ))}
              </div>
            </div>
          ) : null}

          {documentsReceived.length ? (
            <div className="text-2xs">
              <p className="text-muted-foreground">Documentos recebidos</p>
              <div className="mt-1 flex flex-wrap gap-1">
                {documentsReceived.map((document) => (
                  <KlmPill key={`received-${document}`} tone="green">{document}</KlmPill>
                ))}
              </div>
            </div>
          ) : null}

          {notes.map((note) => (
            <div key={note.label} className="text-2xs">
              <p className="text-muted-foreground">{note.label}</p>
              <p className="mt-0.5 break-words leading-4">{note.value}</p>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

export default SdrQualificationPanel;
