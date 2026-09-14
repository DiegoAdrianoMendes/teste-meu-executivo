# Onda 6 — Spike Word round-trip: contrato provado por teste (§9)

**Status:** spike concluído, contrato provado por 6 testes verdes
(`tests/services/test_legal_docx_service.py`). Este documento é a
lista HONESTA do que o contrato preserva e do que não preserva —
pré-requisito explícito antes de construir o editor web por cima
(Onda 6, restante) e a pasta de protocolo (Onda 7).

## Por que este spike existe

O dono já apanhou de round-trip `.docx` quebrado no editor do
USEIUS/Lastro. Este spike prova, por teste, o contrato de
interoperabilidade Word ANTES de qualquer UI ser construída sobre ele —
se o contrato falhar aqui, falha rápido e sem UI para culpar.

## Estado real do venv (verificado, não assumido)

```
$ .venv/bin/python -c "import docx"       # ModuleNotFoundError
$ .venv/bin/python -c "import openpyxl"   # ModuleNotFoundError
```

Nem `python-docx` nem `openpyxl` estão instalados neste venv, e **não
foram adicionados só para este spike** — por instrução explícita do
enunciado ("implementar o máximo possível do contrato com o que
houver"), o contrato foi implementado com o que já existe: a stdlib
(`zipfile` + `xml.etree.ElementTree`). Um `.docx` é, por especificação
(ECMA-376/OOXML), um pacote zip de partes XML — construí-lo e lê-lo
diretamente é um caminho válido, só menos tolerante a `.docx` arbitrário
vindo de qualquer versão do Word do que uma biblioteca madura seria.

Se `python-docx` for adicionado depois (decisão consciente, não deste
spike), o parser de `word/document.xml` pode ser substituído por
`Document(BytesIO(...))` mantendo a mesma interface pública
(`export_docx`/`reimport_docx`) — o contrato dos testes não muda.

## O que este contrato PRESERVA

| Elemento | Como |
|---|---|
| Texto de parágrafo | `<w:t>` dentro de `<w:p>`, lido/escrito 1:1 |
| Títulos de seção | Parágrafo com `pStyle="Heading1"` = novo `{"title", "body"}` em `content.sections` |
| Quebra de parágrafo dentro do corpo | Body é `"\n".join(linhas)`; cada linha = um `<w:p>` |
| Comentários do Word (`word/comments.xml`) | Extraídos na reimportação para `content.comments` (autor, data, texto) |
| Identidade + integridade da versão | Custom properties `klmArtifactVersionRowId` + `klmArtifactEvidenceSha256`, comparadas contra `LegalArtifactVersion.evidence_sha256` (já travado por `before_insert`) |

## O que este contrato NÃO preserva (sinalizado, nunca descartado em silêncio)

`manifest.not_preserved` lista cada ocorrência encontrada; a reimportação
**continua** (não bloqueia), mas o elemento não é recriado no
`content` estruturado:

- **Imagens** (`word/media/*`) — sinalizado como `image`.
- **Tabelas** (`<w:tbl>`) — sinalizado como `table`; texto de dentro da
  tabela não é extraído nesta versão do parser.
- **Objetos incorporados / desenhos / caixas de texto** (`<w:drawing>`,
  `<w:pict>`, `<w:object>`) — sinalizado como `embedded_object_or_drawing`.
- **Controle de alterações do Word** (`<w:ins>`/`<w:del>`) — sinalizado
  como `tracked_change`; o texto extraído é o texto "achatado" do XML,
  não o antes/depois da revisão.
- **`mc:AlternateContent`** (fallback de compatibilidade, comum em
  desenhos/formas modernas) — sinalizado como `alternate_content`.
- **Cabeçalhos e rodapés** (`word/header*.xml`, `word/footer*.xml`) —
  sinalizado como `header_or_footer`; conteúdo timbrado do Playbook
  (`office_templates`) usado como base de exportação NÃO é o foco deste
  spike de conteúdo estruturado — isso é trabalho do editor real
  (Onda 6, restante), que precisa decidir se o cabeçalho/rodapé do
  modelo timbrado é fixo (nunca editável pelo Word) ou reimportável.
- **Notas de rodapé/fim** (`word/footnotes.xml`, `word/endnotes.xml`) —
  sinalizado, texto não incorporado ao `content`.

**Lição portada do editor do USEIUS/Lastro:** a falha lá foi silenciosa
— elemento sumia sem aviso e ninguém sabia até o cliente reclamar. Aqui
qualquer um desses casos aparece em `manifest.not_preserved`, testado
explicitamente (`test_unpreservable_elements_are_flagged_not_silently_dropped`).

## Limitação conhecida e documentada: comentários são assimétricos

A reimportação **lê** `word/comments.xml` e preserva os comentários como
dado (`content.comments`), mas a exportação **não os re-emite** no
próximo `.docx` gerado — `export_docx` sempre parte de `content.sections`
e ignora `content.comments` ao montar o pacote. Ou seja: comentário
entra, mas não volta a aparecer ancorado no mesmo lugar num export
subsequente. Um contrato bidirecional completo de comentários (ancorar
`commentRangeStart`/`commentRangeEnd` a um trecho específico do texto
através de edições) é trabalho de editor, não deste spike — fica listado
aqui para não ser "descoberto" como bug depois.

## Como a identidade de versão é protegida (sem segredo novo)

Nenhum HMAC ou segredo novo foi inventado. O selo de integridade reusa
`LegalArtifactVersion.evidence_sha256`, que já é calculado e travado por
`before_insert` (`app/models/klm_operations.py`) a partir do payload
canônico completo da versão (workspace, matter, artifact_key, version,
kind, title, content, source_snapshot, created_by). `export_docx` embute
`(id da linha, evidence_sha256)` como custom properties OOXML;
`reimport_docx` busca a linha por id no workspace correto e recalcula a
comparação — se o hash embutido não bater com o hash real armazenado no
banco, é rejeitado como adulterado, sem tentar adivinhar a origem.

Cobertura: `test_reimport_rejects_missing_or_tampered_version_id`
(ausência da custom property E adulteração do hash, dois casos no mesmo
teste).

## Concorrência: nunca merge automático

Se a versão de origem do `.docx` não é mais a versão mais recente do
`artifact_key` no banco (alguém avançou o documento nesse intervalo),
`reimport_docx` levanta `DocxMergeConflictError` e não tenta nenhuma
mesclagem — o usuário precisa exportar de novo a partir da versão atual.
Coberto por `test_reimport_of_stale_export_is_rejected_never_auto_merged`.

## Aprovação e "manifesto" invalidados por reimportação

Não existe ainda uma tabela de manifesto de protocolo (isso é Onda 7 —
DMS jurídico + pasta de protocolo). Para este spike, "manifesto" é o
`DocxManifest` retornado por cada chamada de `export_docx`/`reimport_docx`
— não é persistido como registro à parte. Quando uma versão **aprovada**
(`LegalArtifactState.status == "approved"`) é reimportada com conteúdo
alterado:

1. Uma versão nova é criada (nunca sobrescreve a aprovada — ela é
   imutável por `register_append_only_model`).
2. O estado da versão superada passa para `"changes_requested"` — o
   MESMO status que `legal_controladoria_panel_service.py` já interpreta
   como "minuta bloqueada, revisor precisa agir" (nenhum status novo
   foi criado).
3. `manifest.approval_valid` vira `False` com
   `invalidated_reason="approved_version_content_changed_on_docx_reimport"`.
4. Um evento de auditoria (`legal_artifact_approval_invalidated_by_docx_reimport`)
   é registrado via `KlmOperationsService.record_audit` (infra já
   existente, reusada).

Quando a Onda 7 construir o manifesto de protocolo real (hash do PDF
final, handoff humano), este mesmo evento de auditoria é o gancho para
invalidar aquele manifesto também — não precisa reinventar a detecção de
"conteúdo mudou depois de aprovado", só reagir a este evento.

Coberto por
`test_reimport_of_approved_version_with_changes_invalidates_approval_and_manifest`.

## Nenhuma migração de banco foi necessária

Todo o contrato usa modelos e status já existentes
(`LegalArtifactVersion`, `LegalArtifactState`, `"changes_requested"`,
`evidence_sha256`) e reusa `KlmOperationsService.create_legal_artifact_version`
para a criação de versão nova — a mesma trava de concorrência
(`expected_version`) e o mesmo snapshot de origem que a API
`/api/v1/klm/legal-artifacts` já usa. Não havia necessidade de tabela
nova nem de coluna nova para provar este contrato; se o editor real
precisar persistir o manifesto como registro de protocolo formal, isso
pertence à Onda 7 (que já está no roadmap para "manifesto, hash,
handoff humano").

## Arquivos

- `app/services/legal_docx_service.py` — `export_docx`, `reimport_docx`,
  `DocxManifest`, `UnpreservedElement`, exceções (`DocxVersionRejectedError`,
  `DocxMergeConflictError`, `DocxArtifactNotFoundError`).
- `tests/services/test_legal_docx_service.py` — os 6 testes-prova.
