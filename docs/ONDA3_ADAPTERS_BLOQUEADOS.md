# ONDA 3 — ADAPTERS DEDICADOS BLOQUEADOS (Astrea / ADVBOX / DataJuri)

> Plano Mestre §12 determina construir só a metade **agnóstica** do
> Importador Universal de Migração nesta onda. Os três adapters dedicados
> abaixo ficam **BLOQUEADOS** — não por dificuldade técnica, mas porque a
> regra do plano exige um arquivo real de export de cada concorrente antes
> de escrever o parser específico (nunca inventar o formato de memória).

## O que já está pronto e não depende de nenhum adapter

- Modelos `MigrationJob` / `MigrationStagingRecord` / `MigrationDivergence`
  (`app/models/migration_job.py`), com RLS por workspace.
- `MigrationSourceSystem` já reserva os 3 valores (`astrea`, `advbox`,
  `datajuri`) — quando o arquivo de export chegar, o trabalho é só escrever
  um parser que produza `ParsedRow` (mesmo formato usado pelo CSV/XLSX/ZIP/
  JSON universais, ver `app/services/migration_parsers.py`) e apontar
  `source_system` para o valor certo. **Nenhuma migração de banco nova é
  necessária.**
- Todo o pipeline (staging, normalização de CPF/CNPJ/CNJ/datas, dedupe,
  prévia, importação em lote idempotente, reconciliação, semáforo,
  divergências, rollback) já funciona ponta a ponta e é 100% reutilizável
  pelos três adapters — eles só entram na etapa "detectar formato +
  parsear linhas", tudo depois disso (normalização, dedupe, importação,
  semáforo) é o mesmo código.

## O que falta para destravar cada adapter

Pedir ao dono da conta (ou ao próprio concorrente, via suporte) o
seguinte, em cada caso:

### Astrea

- Um export real de **clientes**, **casos/processos** e **financeiro**
  (o Astrea costuma oferecer exportação em CSV/XLSX pelo painel
  administrativo — "Configurações → Exportar dados" ou equivalente).
- Se o Astrea expuser API/webhook de exportação, o token de acesso de uma
  conta de teste (não produção) basta para inspecionar o schema JSON.
- Sem o arquivo real, qualquer suposição de coluna/formato seria
  fabricada — a regra do plano proíbe isso explicitamente.

### ADVBOX

- Mesma coisa: export real de clientes/processos/financeiro pela conta do
  cliente que está migrando (ADVBOX tem exportação CSV nativa em
  "Relatórios" e uma API REST documentada — um token de sandbox/teste real
  destrava o parser via API em vez de só CSV).

### DataJuri

- Export real de clientes/processos/financeiro. DataJuri historicamente
  oferece exportação via planilha e, em alguns planos, integração via
  webservice SOAP/REST — precisa do arquivo OU da documentação de conexão
  de uma conta real para não adivinhar o contrato.

## Como destravar (passo a passo, quando o arquivo chegar)

1. Salvar o arquivo de export real (anonimizado se necessário) em
   `tests/fixtures/migration/<sistema>/`.
2. Escrever `parse_<sistema>(content) -> ParseResult` em
   `app/services/migration_parsers.py`, reaproveitando `ParsedRow` (mesmo
   contrato dos parsers universais).
3. Adicionar o branch em `parse_content()`/`detect_format()` só quando
   `source_system` for explicitamente `astrea`/`advbox`/`datajuri` (nunca
   por heurística de conteúdo — o usuário já disse a origem no upload).
4. Escrever o teste de peso equivalente ao de
   `tests/services/test_migration_service.py`, mas com o arquivo real
   como fixture (não sintético) para o adapter em questão.
5. Nenhuma mudança em `app/models/migration_job.py` ou na migração
   `a04b6e6f22b4_add_migration_jobs.py` deveria ser necessária — se for,
   é sinal de que o adapter precisa de um domínio/campo que a versão
   agnóstica ainda não previu, e aí sim vira uma migração nova.
