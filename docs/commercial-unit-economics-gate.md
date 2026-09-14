# Gate de unit economics das ofertas com IA

## Objetivo

Uma oferta com franquia paga de IA só pode ser marcada como pública quando o
backend consegue demonstrar, a partir de recibos reais e append-only de uso,
que existe amostra mínima de consumo em todas as rotas ativas e que o custo
projetado pela tabela interna versionada cabe no teto
interno da mensalidade. Limite configurado, credencial disponível, fixture,
credencial, fixture ou teste local não constituem prova de uso pelo provider.

Ofertas internas continuam editáveis, versionáveis e contratáveis pelo fluxo
administrativo autorizado. O bloqueio incide sobre a publicação comercial ao
mercado, não sobre o trabalho de preparação do Super Admin.

## Fonte e versão

- fonte declarada na API: `provider_confirmed_usage_plus_internal_rate_card`;
- recibos de uso: `platform_model_usage_ledger`;
- versão do diagnóstico: `ai-active-route-rate-card-v3`;
- janela móvel: 90 dias;
- somente recibos com `provider_request_id`, versões **iguais às versões
  atualmente ativas** dos catálogos de preço e rota e tokens positivos;
- cada rota primária e cada fallback ativos precisa de ao menos um recibo;
- `cost_usd` histórico não é usado como tarifa de projeção;
- nenhum prompt, resposta ou conteúdo do cliente entra no diagnóstico.

## Amostra mínima

O gate exige simultaneamente:

- 30 chamadas com uso confirmado pelo provider;
- sete dias UTC distintos;
- 50.000 tokens observados.

Esses mínimos não são apresentados como pesquisa de mercado. São um controle
operacional conservador e versionado para impedir a publicação baseada em
zeros, fixtures ou estimativas sem lastro.

## Teste de custo

O gate enumera todas as rotas primárias e fallback do catálogo atualmente
ativo. Para cada `provider:model`, ele exige uso confirmado na mesma versão de
catálogo e busca a tarifa correspondente na tabela interna ativa.

Input, output e cached input são precificados separadamente. Como a franquia
finita pode ser consumida em combinações diferentes, a projeção usa a maior
tarifa direcional entre todas as rotas ativas. Assim, uma amostra com muito
input barato não oculta output caro, cache sem tarifa não vira gratuito e uma
rota barata não subsidia um fallback caro.

Esse custo unitário conservador é projetado sobre o teto mensal de tokens da
oferta, com:

- câmbio interno de referência de R$ 5,50/US$;
- fator de segurança de 1,25;
- teto de custo de IA equivalente a 35% da mensalidade.

O câmbio, o fator, a tabela de preços e o teto são hipóteses internas
versionadas. A combinação “uso confirmado pelo provider + tabela interna” não
é fatura do provider, cotação cambial em tempo real, pesquisa de mercado, preço
recomendado nem prova de disposição a pagar.

Execuções, conversas e gerações são tratadas como tokenizadas apenas enquanto
continuarem tecnicamente subordinadas ao teto finito de tokens. `audio_minutes`
não é tokenizado: sua publicação permanece bloqueada até existir um ledger
append-only com minutos e custo de transcrição observados. Evidência de tokens
nunca é usada como substituto. Toda nova dimensão paga sem modelo de custo e
fonte observada próprios também deve falhar fechada.

Os valores e margens calculados não são devolvidos a perfis comuns. A prévia
expõe apenas status, cobertura da amostra e motivos do bloqueio. O backend é a
autoridade final e repete a avaliação dentro do comando de publicação ou
restauração.

## Estados

- `not_required`: oferta interna ou oferta pública sem franquia paga de IA;
- `insufficient_data`: amostra, preço ou teto de tokens insuficiente;
- `unprofitable`: amostra suficiente, mas custo projetado acima do teto;
- `ready`: amostra suficiente e teste interno aprovado.

## Comportamento fail-closed

1. O rascunho pode ser salvo, inclusive com `isPublic=true`.
2. A prévia devolve `unitEconomics`, `blockers`, diagnóstico e
   `eligible=false` quando faltar evidência.
3. O botão de publicação permanece bloqueado.
4. Chamada direta ao endpoint de publicação falha no backend.
5. Restauração de revisão pública repete o mesmo gate.
6. A consulta `publicOnly=true` suprime revisões antigas que tenham sido
   marcadas públicas antes da existência do controle.
7. O Super Admin pode tornar a oferta interna e publicar a revisão interna,
   com motivo e auditoria, sem inventar evidência.

Alterar qualquer constante exige nova versão do diagnóstico, testes de
regressão e registro do motivo. Dados reais nunca serão fabricados para liberar
o gate.
