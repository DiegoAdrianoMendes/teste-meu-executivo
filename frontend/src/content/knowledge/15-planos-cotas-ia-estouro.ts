import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'planos-cotas-ia-estouro',
  titulo: 'Planos, cotas de IA e o que acontece ao estourar',
  resumo:
    'Onde ver os limites do plano do workspace (usuários, IA, WhatsApps conectados, armazenamento), como funciona o limite mensal de tokens de IA e o que acontece quando ele é atingido.',
  categoria: 'gestao',
  papeis: ['gestor'],
  paginasRelacionadas: [{ label: 'Configurações — IA e tokens', rota: '/app/configuracoes/avancadas?tab=ia' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Explica onde acompanhar os limites do plano contratado pelo escritório e como funciona o controle de consumo de IA, nas abas "Plano do workspace" e "IA e tokens" de [[rota:/app/configuracoes/avancadas?tab=ia|Configurações]].

## O painel "Plano do workspace"

No topo das Configurações, um painel mostra, para o plano atual, quanto já foi usado e quanto ainda resta em cada frente:

- **Usuários**: quantas pessoas da equipe já têm conta, sobre o limite de assentos do plano.
- **Agentes IA**: quantos agentes de IA estão ativos.
- **WhatsApps**: quantos números do WhatsApp estão conectados.
- **Conversas IA**: quantas conversas com IA já ocorreram neste mês.
- **Áudio IA**: quantos minutos de processamento de áudio já foram usados neste mês.
- **Armazenamento**: quanto espaço em disco já está ocupado.
- **Arquivos**: quantos arquivos já foram enviados.
- **Automações**: quantas automações (flows) já estão configuradas.
- **Alertas extras**: quantos destinatários adicionais de alerta operacional já estão cadastrados.

Quando um item não tem teto no plano contratado, a tela mostra "Ilimitado no plano atual" em vez de uma fração. Abaixo dos números, um conjunto de selos mostra quais recursos adicionais o plano inclui (assinatura digital, agenda, e-mail, webhooks, auditoria avançada, gerente de sucesso).

> DICA: se algum item estiver perto do limite, é possível ver exatamente quanto resta em cada frente sem precisar entrar em contato com o suporte para perguntar — a tela é sempre a fonte mais atual.

## O limite mensal de tokens de IA

A aba "IA e tokens" detalha o consumo de tokens (a unidade que mede o uso da inteligência artificial) do mês corrente:

1. **Saldo de tokens**: quanto ainda resta dentro do limite mensal.
2. **Uso do limite mensal**: a porcentagem já consumida, com uma barra de progresso.
3. **Limite mensal de tokens**: o plano já define um teto contratual, mas o escritório pode configurar um limite local próprio, mais conservador que o do plano, se quiser.

## O que significa "bloqueio rígido"

Ao configurar o limite mensal, existe uma opção chamada **bloqueio rígido**:

- **Com bloqueio rígido ativado**: quando o limite é atingido, o sistema impede novas reservas de tokens — ou seja, a IA para de processar novas solicitações até o próximo ciclo mensal ou até o limite ser aumentado.
- **Sem bloqueio rígido (padrão)**: atingir o limite gera apenas um alerta, sem travar o uso da IA.

> ATENÇÃO: o bloqueio rígido é uma proteção contra gasto inesperado, mas também pode interromper o atendimento automatizado no meio do mês se o limite for definido baixo demais. Avalie o consumo médio dos últimos meses antes de ativar essa opção.

## Alerta de consumo

É possível configurar em que porcentagem do limite o sistema deve emitir um alerta (por exemplo, avisar ao atingir 80% do consumo mensal), para dar tempo de agir antes de um eventual bloqueio.

## O que fazer ao se aproximar do limite

1. Reveja se o limite local configurado está compatível com o plano contratado — às vezes o teto mais apertado é uma configuração interna, não do plano em si.
2. Se o consumo estiver consistentemente alto, considere revisar o plano do workspace para um com mais franquia de conversas de IA.
3. Se o bloqueio rígido estiver ativo e a IA parar de responder no meio do mês, esse é o primeiro lugar a checar antes de suspeitar de outro problema técnico.
`,
};

export default article;
