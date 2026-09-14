# QA do KLM Hub SaaS Local V5

**Data:** 20 de julho de 2026  
**Branch:** `codex/saas-foundation`  
**Estado:** `CORE_VERIFIED_P5_ENVIRONMENT_BLOCKED`  
**Decisão:** ainda não declarar `LOCAL_SAAS_APPROVAL_READY_V5`.

## 1. Escopo e regra de verdade

Este laudo cobre somente execução local. APIs de modelos, WhatsApp, e-mail,
agenda, assinatura, pagamento, áudio, anúncios e AWS permaneceram desligadas.
Nenhum resultado local é apresentado como verificação de provider ou produção.

Há dois escopos de digest complementares:

- `1afaa16193523b38d4543bbb4d1fc54e2f24a51917a01c4bf33fafabd10a3e11`:
  snapshot de 1.184 arquivos que formam o contrato de release OSS nativo;
- o baseline amplo inclui a documentação e tem seu valor final registrado em
  `.local/qa-evidence/saas-v5-p5/baseline-after.json`. O digest não é repetido
  dentro deste laudo porque o próprio arquivo participa desse inventário.

Artefatos gerados são excluídos dos digests que precisam ser renovados por eles
mesmos. O lock de upstreams permanece incluído e os cinco checkouts pinados
coincidem com commit e licença registrados.

## 2. Evidências aprovadas no snapshot atual

| Gate | Resultado | Evidência |
|---|---|---|
| Backend completo | 2.119 passed, 0 skipped, 0 failures, 0 errors, 10 warnings | `app/data/oss_native_proofs/backend.json` e artefatos gzip em `app/data/oss_native_proofs/raw/` |
| Frontend unitário | 321 passed em 48 arquivos | `app/data/oss_native_proofs/frontend-unit.json` |
| TypeScript | passou | prova `frontend-unit` |
| ESLint | 0 erros, 12 warnings históricos | prova `frontend-unit` |
| Build Vite | passou com Node 20.19.4 | prova `frontend-unit` |
| Censo OSS | 3.708 unidades; 3.631 verificadas; 77 bloqueadas por licença | `docs/oss-function-units.lock.json` |
| Contratos FunctionUnit | 3.631 contratos, 135 capacidades e 511 ações | gerado por `scripts/verify_oss_function_unit_release.py --check` em `.local/qa-evidence/oss/oss_function_unit_verification.json` |
| Convergência derivada | censo, política e manifesto passam em `--check`; 18 testes específicos | logs desta execução e artefatos versionados |
| P4 IA/RAG | sete gates passaram | `.local/qa-evidence/saas-v5-p5/p4-verification-current.json` |
| Eficiência de contexto | redução média 50,97%; limite inferior 50,32%; cobertura factual 1,0; qualidade 1,0 | `.local/qa-evidence/saas-v5-p4/benchmark.json` |
| Simulação | 2.800 conversas sintéticas; zero efeito externo | `.local/qa-evidence/saas-v5-p5/conversation-simulation-current.json` |
| Segredos | zero achado de alta confiança; upstreams íntegros | `.local/qa-evidence/saas-v5-p5/baseline-current.json` |
| Ruff | zero violação nova | `.local/qa-evidence/saas-v5-p5/ruff-current.json` |
| SBOM | 691 componentes; zero acesso de rede | `.local/qa-evidence/saas-v5-p5/sbom-current.json` |
| Diff | `git diff --check` passou | execução local |
| P5 rápido não privilegiado | 14 de 14 gates selecionados; fonte estável; resultado `partial_passed` | `.local/qa-evidence/saas-v5-p5/p5-quick-verification.json` |

O ratchet de Ruff não significa código integralmente limpo: existem **5.144
achados históricos** no baseline. O gate impede aumento e permite redução
progressiva. O SBOM também não transforma metadados de licença do ecossistema em
parecer jurídico: 687 componentes não possuem evidência de licença reforçada pelo
próprio gerador, e o EmbeddedChat permanece bloqueado.

## 3. Defeito encontrado e corrigido nesta rodada

`scripts/capture_klm_baseline.py` aceitava `--output` absoluto fora do repositório,
gravava o JSON e quebrava ao formatar o caminho para stdout. O formatador agora
aceita caminhos internos e externos; a regressão foi adicionada a
`tests/test_capture_klm_baseline.py`. A suíte focada passou com 14 testes e o
comando real em `/private/tmp` passou.

Também foi reconciliado um SHA antigo no censo: política, lock e manifesto foram
regerados em ordem determinística. A primeira suíte completa expôs somente essa
divergência; após a correção, a repetição passou com 2.089 testes backend.

O coletor visual também passou a capturar em staging e publicar o diretório de
evidência por troca atômica. Uma falha forçada na abertura do Chromium preservou
o sentinel do pacote anterior e removeu o staging incompleto. Assim, uma tentativa
ruim não destrói mais a última evidência válida.

Os 27 testes PostgreSQL antes condicionados deixaram de usar `skip` ou `skipif`.
O runner agora cria e migra um PostgreSQL efêmero, inclui a suíte de identidade,
gera JUnit e falha se houver qualquer teste omitido. A suíte backend, o P5 e o
manifesto nativo recusam estruturalmente `skipped > 0`. A repetição integral
passou com 2.119 testes e zero skips. Durante o endurecimento também foi corrigido
um trap de limpeza que poderia mascarar um erro do runner com código de saída zero.

## 4. Gates bloqueados pelo ambiente da sessão

| Gate pendente | Causa observada | Por que não foi promovido |
|---|---|---|
| captura visual endurecida | Chromium encerra em `MachPortRendezvous ... Permission denied (1100)` | o manifesto exige 16 screenshots, 2 vídeos, zero loading e zero falha local |
| Playwright E2E do snapshot | mesma permissão de processo do Chromium | resultados de snapshot anterior não substituem nova execução |
| upstream runtime preflight | acesso ao socket Docker negado | pins/licenças estáticos passaram, mas o runtime dos sidecars não foi reprovado no digest atual |
| runtime HTTP local | processos em namespace externo não são alcançáveis pelo executor restrito | a prova exige API e sidecars no mesmo digest |
| stack Docker atual | daemon Docker não acessível | imagem antiga em execução não prova o código atual |
| benchmark p95 da stack | proxy local `:18080` inacessível | o SLO de 300 ms precisa ser medido no build atual |
| manifesto OSS nativo final | requer backend, frontend, E2E, runtime e upstream no mesmo digest | somente backend e frontend-unit estão atuais |
| P5 final | depende de todos os itens acima | o arquivo `app/data/saas_v5/p5-verification.json` continua sendo uma execução antiga falha e não deve ser usado como aprovação |

O coletor visual agora grava em staging e só publica o pacote completo por troca
atômica. Uma falha forçada na abertura do Chromium preservou o pacote anterior e
removeu o staging incompleto. O bloqueio é de permissão do executor, mas o
comportamento visual permanece não comprovado no snapshot atual até a execução
privilegiada ocorrer.

Os dois revisores independentes solicitados pelo contrato de trabalho também não
iniciaram: a plataforma os encerrou por limite de uso. A segunda passagem foi
executada localmente e encontrou a autorreferência de digest deste laudo, que foi
removida antes da repetição do manifesto P5 `quick`. Isso não substitui a revisão
independente exigida para o fechamento final.

## 5. Warnings e riscos residuais

1. SQLAlchemy ainda emite oito avisos ligados a `datetime.utcnow()` em defaults
   históricos; não há falha funcional, mas a migração para UTC timezone-aware
   deve entrar no backlog.
2. Duas migrações em SQLite emitem aviso de reflection de índice baseado em
   expressão; PostgreSQL continua sendo o banco de prova de tenancy real.
3. ESLint mantém 12 warnings de Fast Refresh e uma dependência de hook
   desnecessária. O gate não aceita erros, mas essa dívida deve ser reduzida.
4. As 5.144 ocorrências históricas de Ruff são dívida aceita por ratchet, não
   qualidade final ideal.
5. Nenhuma integração externa, custo de modelo pago, uptime, backup/restore ou
   capacidade de produção foi homologada nesta fase.
6. A revisão competitiva identifica gaps em conflitos, trust accounting, prazos
   processuais amplos e billing jurídico profundo.

## 6. Comandos de fechamento ainda necessários

Quando o executor tiver acesso a Chromium, loopback e Docker, executar na ordem:

```bash
env GIT_NO_LAZY_FETCH=1 ./.venv/bin/python scripts/verify_oss_native_release.py --suite upstream-locks
env GIT_NO_LAZY_FETCH=1 ./.venv/bin/python scripts/verify_oss_native_release.py --suite local-runtime
env PATH="$PWD/.local/toolchains/node-v20.19.4-darwin-arm64/bin:$PATH" NPM_CONFIG_OFFLINE=true ./.venv/bin/python scripts/verify_oss_native_release.py --suite frontend-e2e
./.venv/bin/python scripts/verify_oss_native_release.py --finalize-only
env GIT_NO_LAZY_FETCH=1 ./.venv/bin/python scripts/census_oss_function_units.py --check
./.venv/bin/python scripts/build_oss_function_unit_dispositions.py --check
./.venv/bin/python scripts/verify_oss_function_unit_release.py --check
env GIT_NO_LAZY_FETCH=1 ./.venv/bin/python scripts/verify_saas_v5_p5.py --mode final --stack-env-file /private/tmp/klm-local-stack.env
```

Antes do P5, as imagens da API e frontend precisam ser reconstruídas a partir do
snapshot atual e a stack recriada localmente. O verificador P5 não possui permissão
para mutar a stack por desenho; ele apenas mede e inspeciona a stack preparada.

## 7. Critério de aprovação

O núcleo local e a incorporação nativa estão maduros o suficiente para a última
rodada de infraestrutura. A aprovação do proprietário deve ocorrer somente quando
o P5 final reportar `passed`, `localP5Verified=true`, os cinco proofs nativos
compartilharem o digest atual e o benchmark de endpoint apresentar p95 agregado e
individual abaixo de 300 ms. Até lá, APIs e AWS continuam bloqueadas.
