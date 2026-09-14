# ADR: cadeia de custodia de mensagens e midias

- Status: implementado localmente, sujeito aos gates de producao abaixo
- Data: 2026-07-21
- Escopo: conversas omnichannel, anexos, edicoes, exclusoes e recibos de provedor
- Fora de escopo nesta fase: AWS, carimbo do tempo externo, ICP-Brasil e ativacao de APIs externas

## Decisao

O KLM preserva cada mensagem como uma projecao consultavel e, separadamente, como uma cadeia append-only de eventos probatorios. A projecao pode indicar que o conteudo foi editado ou apagado; o original e cada revisao continuam imutaveis na cadeia.

A implementacao usa quatro camadas:

1. Envelope bruto do webhook: bytes exatos recebidos, hash SHA-256, cabecalho de assinatura, hash da assinatura, esquema, resultado da verificacao e instante de recebimento.
2. Evento probatorio por tenant: captura, edicao, exclusao, recibo, midia capturada, midia indisponivel ou limitacao do provedor.
3. Encadeamento criptografico: cada evento inclui o hash do anterior e produz um novo hash canonico da cadeia.
4. Projecao operacional: a mensagem atual mostra estado, total de eventos, total de revisoes e hash da cabeca da cadeia, sem substituir os registros anteriores.

## Invariantes

- Nenhum update ou delete ordinario e permitido nas tabelas de evidencia.
- O workspace nao pode ser excluido em cascata enquanto houver evidencia vinculada.
- Referencias entre evidencia, mensagem, thread, conexao e objeto de armazenamento incluem o `workspace_id`.
- Uma chave de idempotencia nao pode ser reutilizada com conteudo diferente.
- A cadeia precisa ter revisoes contiguas, ponteiro para o evento anterior e hash anterior correspondente.
- O hash e calculado sobre JSON canonico, incluindo identidade do evento, revisao, snapshots, hashes de origem e instantes.
- Segredos conhecidos sao removidos dos metadados antes da persistencia tenant-facing.
- O envelope bruto global nunca e exposto pela API comum do workspace.
- Midia probatoria referencia uma versao exata do objeto, com hash, tamanho, MIME, nome e identificador do provedor quando existentes.
- Objetos ligados a evidencia nao podem ser apagados pelo fluxo comum de armazenamento.

## Semantica dos eventos

| Evento | Significado |
|---|---|
| `captured` | Estado observado pelo KLM quando a mensagem foi materializada. |
| `edited` | Nova revisao entregue pelo provedor; a revisao anterior permanece. |
| `deleted` | Sinal de exclusao entregue pelo provedor; o ultimo conteudo conhecido permanece na cadeia. |
| `provider_receipt` | Recibo ou mudanca de entrega sem substituir o conteudo. |
| `media_captured` | Bytes da midia foram preservados e conferidos por hash. |
| `media_unavailable` | O provedor sinalizou midia, mas os bytes nao puderam ser obtidos. |
| `provider_unsupported` | O provedor nao oferece informacao suficiente para reconstruir o evento. |

`observation_state` separa fato observado (`observed`), sinal do provedor (`provider_signal`), inferencia explicita (`inferred`) e indisponibilidade (`unavailable`). Inferencia nunca e apresentada como fato recebido.

## Historico anterior a migracao

Mensagens ja existentes recebem um evento `captured` com `captureBasis=canonical_projection`. Esse evento prova o estado observado pelo KLM no momento do backfill. Ele nao afirma, retroativamente, que esse era o primeiro estado existente no provedor.

O backfill e idempotente: duas execucoes consecutivas nao duplicam eventos. Na copia local de QA usada em 2026-07-21, 236 mensagens produziram 236 cadeias na primeira execucao e zero novas cadeias na segunda.

## Isolamento e exportacao

- Leitura e exportacao exigem `workspace_id`, `thread_id` e `message_id` coincidentes.
- O manifesto de exportacao inclui snapshots, revisoes, hashes, estado de integridade e um hash final do proprio manifesto.
- Usuarios sem permissao para dados sensiveis recebem os hashes e a estrutura da cadeia, sem corpo, conteudo estruturado ou identificador interno do objeto.
- O envelope bruto pode conter varios tenants no mesmo lote de provedor; por isso fica fora da API de tenant e exige procedimento administrativo privilegiado.

## Garantias e limites

O KLM oferece evidencia tecnica de integridade, sequencia, origem observada e custodia interna. A cadeia detecta alteracao posterior do que foi persistido, desde que o atacante nao consiga substituir simultaneamente banco, chaves, backups e trilhas externas.

O KLM nao garante, isoladamente:

- que um provedor entregara todos os eventos de edicao ou exclusao;
- que um snapshot historico anterior ao primeiro recebimento possa ser reconstruido;
- autoria humana fora das informacoes verificadas pelo provedor e pela autenticacao KLM;
- admissibilidade automatica, autenticidade incontroversa ou valor juridico predeterminado;
- carimbo do tempo de terceira parte enquanto o gate correspondente nao estiver ativo.

A valoração final da prova continua dependente do caso concreto e da autoridade competente. A arquitetura foi desenhada para apoiar demonstracao de autenticidade e preservacao de documento eletronico, em linha com o [CPC](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105compilada.htm), a [Lei 11.419/2006](https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11419.htm) e a [Lei 14.063/2020](https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2020/lei/l14063.htm), sem declarar certificacao ou presuncao legal automatica.

## Acesso e retencao

- O fluxo comum nunca apaga conversas, leads ou evidencias.
- Pedido de eliminacao por titular exige fluxo administrativo privilegiado, motivo, dupla confirmacao, escopo calculado e trilha de decisao.
- Exclusao legalmente exigida deve produzir registro minimo nao reversivel da operacao, sem conservar o conteudo que deveria ser eliminado.
- Exportacao e eliminacao de conta sao exclusivas do administrador master do SaaS ou de TI com delegacao expressa e auditada.
- Backups devem preservar a mesma separacao entre dados do tenant e envelope global.

## Gates obrigatorios antes de producao

1. Migrar PostgreSQL em copia restaurada e executar o backfill idempotente.
2. Validar RLS com papeis de aplicacao, worker, migracao e suporte emergencial.
3. Assinar cabecas de cadeia com chave assimetrica em KMS ou HSM, com rotacao e trilha de uso.
4. Adicionar carimbo do tempo confiavel para exportacoes de alta criticidade.
5. Armazenar envelopes e versoes de midia em camada WORM/Object Lock com politica de retencao revisada.
6. Testar restauracao de backup e verificacao integral das cadeias apos restore.
7. Registrar sincronizacao de relogio, versao do verificador e artefato de build.
8. Submeter o desenho a revisao juridica e de seguranca antes de alegacoes publicas sobre valor probatorio.

## Verificacao local

- Migracao testada em SQLite com triggers append-only e FKs ativas.
- Update e delete de eventos de evidencia sao rejeitados.
- Exclusao do workspace com evidencia e rejeitada.
- Referencia cross-tenant e rejeitada.
- Cadeia adulterada falha na verificacao.
- Exportacao sem permissao mascara conteudo sensivel.
- Midia ligada a evidencia nao pode ser apagada pelo caminho comum.

## Arquivos de referencia

- `app/models/omnichannel_v6.py`
- `app/models/klm_operations.py`
- `app/services/message_evidence_service.py`
- `app/services/local_object_storage_service.py`
- `app/api/v1/endpoints/klm_operations.py`
- `alembic/versions/b4d6f8a0c201_add_message_evidence_chain.py`
- `tests/services/test_message_evidence_service.py`
- `tests/migrations/test_message_evidence_chain_migration.py`
