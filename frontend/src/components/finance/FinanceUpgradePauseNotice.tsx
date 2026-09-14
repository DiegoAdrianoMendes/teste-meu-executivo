import { Landmark, Network, ShieldCheck, Workflow } from 'lucide-react';

import { Badge } from '@/components/ui/badge';

export const FINANCIAL_UPGRADE_STATUS = 'AGUARDANDO ATIVAÇÃO' as const;
export const FINANCIAL_UPGRADE_UNLOCK_TRIGGER = 'destravar upgrade de funções financeiras';

const launchPaths = [
  {
    key: 'asaas',
    label: 'Asaas direto',
    icon: Landmark,
    status: 'Implementado',
    description: 'Adapter dedicado do Meu Executivo para cobranças e eventos financeiros do Asaas.',
    steps: [
      'Contrato, cobranças, parcelas e recorrências prontos e testados localmente',
      'Webhooks com deduplicação e baixa automática cobertos por testes',
      'Falta apenas: credenciais do sandbox/produção após o desbloqueio',
    ],
  },
  {
    key: 'pluga',
    label: 'Pluga',
    icon: Network,
    status: 'Planejado',
    description: 'Automação intermediada para reduzir o trabalho do primeiro lançamento.',
    steps: [
      'Contrato de webhook definido no Meu Executivo',
      'Campos e chaves idempotentes mapeados',
      'Automação ativada somente após o desbloqueio',
    ],
  },
  {
    key: 'zapier',
    label: 'Zapier',
    icon: Workflow,
    status: 'Planejado',
    description: 'Rota alternativa por webhook para fluxos que já operam no ecossistema Zapier.',
    steps: [
      'Payload e eventos versionados localmente',
      'Deduplicação e replay cobertos por testes',
      'Zap publicado somente após o desbloqueio',
    ],
  },
] as const;

export function FinanceUpgradePauseNotice({ showLaunchPaths = false }: { showLaunchPaths?: boolean }) {
  return (
    <section
      data-testid="finance-upgrade-pause"
      className="overflow-hidden border border-emerald-300 bg-emerald-50/70 dark:border-emerald-900 dark:bg-emerald-950/20"
    >
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-200">
            <ShieldCheck className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-medium">Upgrade financeiro nativo e avançado</h2>
              <Badge variant="secondary" className="text-xs">Implementado</Badge>
            </div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              Cobranças, parcelamento, recorrências, régua de cobrança, conciliação bancária,
              honorários, relatórios (KPIs, DRE, projeção de caixa) e indicações já estão
              construídos e testados. Tudo roda em modo local seguro até a ativação.
            </p>
          </div>
        </div>
        <Badge variant="outline" className="w-fit border-emerald-500 text-emerald-900 dark:text-emerald-100">
          {FINANCIAL_UPGRADE_STATUS}
        </Badge>
      </div>

      <div className="flex items-start gap-2 border-t border-emerald-200 px-4 py-3 text-xs leading-5 text-emerald-950 dark:border-emerald-900 dark:text-emerald-100">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
        <p>
          <strong>Modo local seguro.</strong> Enquanto a ativação não é liberada, esta etapa não pede
          credenciais, não testa contas e não chama Asaas, Pluga, Zapier ou qualquer outro provedor —
          as cobranças ficam registradas como locais e sincronizam quando o gateway for ligado.
        </p>
      </div>

      {showLaunchPaths && (
        <div className="border-t border-emerald-200 dark:border-emerald-900">
          <div className="px-4 pt-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-emerald-900 dark:text-emerald-100">
              Caminhos preparados para o lançamento
            </p>
          </div>
          <div className="mt-3 grid divide-y border-t border-emerald-200 dark:border-emerald-900 md:grid-cols-3 md:divide-x md:divide-y-0">
            {launchPaths.map((path) => {
              const Icon = path.icon;
              return (
                <article key={path.key} className="min-w-0 bg-background/60 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      <Icon className="h-4 w-4 shrink-0 text-emerald-800 dark:text-emerald-200" />
                      <h3 className="text-sm font-semibold truncate">{path.label}</h3>
                    </div>
                    <Badge variant="outline" className="shrink-0 text-xs">{path.status}</Badge>
                  </div>
                  <p className="mt-2 text-xs leading-4 text-muted-foreground">{path.description}</p>
                  <ol className="mt-3 space-y-1.5">
                    {path.steps.map((step, index) => (
                      <li key={step} className="grid grid-cols-[18px_1fr] gap-1.5 text-xs leading-4">
                        <span className="font-semibold text-emerald-900 dark:text-emerald-100">{index + 1}.</span>
                        <span>{step}</span>
                      </li>
                    ))}
                  </ol>
                </article>
              );
            })}
          </div>
          <p className="border-t border-emerald-200 px-4 py-3 text-xs leading-4 text-muted-foreground dark:border-emerald-900">
            A escolha de uma rota documenta o lançamento futuro; não representa conexão, homologação
            ou movimentação financeira real até a ativação das credenciais.
          </p>
        </div>
      )}
    </section>
  );
}
