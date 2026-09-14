# Plano — WhatsApp 2026: usernames e BSUID

Data: 27/07/2026
Fonte primária: e-book "Usernames e BSUID no WhatsApp em 2026" (Zenvia, 34 págs.)
Auditoria de código: feita neste repositório na mesma data.

---

## 1. O que muda na plataforma do WhatsApp

Durante anos o número de telefone foi a identidade dentro do WhatsApp. Em 2026
isso deixa de ser verdade, e a mudança acontece em duas camadas.

**Username** (`@nome`) é identificador opcional, visível e editável. Para o
usuário final, adotar um `@` **oculta o número de telefone**. Para a empresa, o
`@` convive com o número, que continua visível.

**BSUID** (*Business-Scoped User ID*) é a chave técnica. Gerado pela Meta,
identifica um usuário **dentro do escopo de um portfólio de negócios**. Não é
escolhido pelo usuário, não é público e não é contato — é a referência que
sustenta a continuidade quando o telefone não está disponível.

O que não muda: preço, aprovação de template, limite de envio e política de
qualidade. A mudança é inteira na camada de identificação, envio, recebimento e
processamento de eventos.

### Regras da plataforma que viram invariantes de código

1. **Username muda, BSUID permanece.** Nunca chavear nada por username.
2. **BSUID é escopado por portfólio.** O mesmo usuário tem BSUID diferente em
   portfólios distintos, e enviar para um BSUID a partir de número vinculado a
   outro portfólio **falha**. A chave é `(workspace, portfólio, bsuid)`, jamais
   `bsuid` sozinho.
3. **A empresa nunca inicia conversa pelo `@` do cliente.** O caminho do `@` só
   existe do cliente para a marca. Campo de "enviar para @" na interface é campo
   que nunca vai funcionar.
4. **Autenticação, verificação e validação de identidade continuam exigindo
   telefone.** Sem exceção.
5. `parent_business_scoped_user_id` pode aparecer em cenários específicos.
   Guardar quando vier, nunca tratar como regra.

### Cronograma declarado

| Período | Evento |
|---|---|
| Março-Abril/2026 | BSPs adequam suas APIs |
| Maio/2026 | API já recebe e identifica BSUID, username e telefone |
| Junho/2026 | Envio usando BSUID passa a ser permitido; testes de username em Colômbia, Peru e Rep. Dominicana |
| **Agosto/2026** | **Adoção de usernames no Brasil** e expansão global gradual |

Estamos em 27/07/2026. **O Brasil entra no mês que vem.**

### Onde mora o risco

Não é a virada, é a convivência. Por um período, alguns eventos trazem só
telefone, outros telefone e BSUID, outros só BSUID. Mudança gradual gera inércia
("ainda está funcionando") e a adaptação é adiada — até o dia em que o
atendimento perde histórico, o bot não reconhece o cliente e o CRM duplica
cadastro. A falha não aparece em abstrato; aparece na operação.

---

## 2. Diagnóstico do nosso código

### 2.1 O que já está certo (e é a parte difícil)

O e-book prescreve que a empresa tenha uma **identidade interna própria**
(`customer_id`) e pendure nela telefone, e-mail, BSUID e username — em vez de
trocar a dependência do telefone por dependência cega do BSUID.

`OmnichannelIdentity` (`app/models/omnichannel_v6.py`) já é exatamente isso:

- `client_id`, `display_name`
- `canonical_phone` **nullable**, com unique parcial (`WHERE ... IS NOT NULL`)
- `canonical_email` nullable, mesmo tratamento
- `identity_state` (`provisional` / verificado)
- `merge_version` — versionamento de fusão de identidade
- `identity_metadata` (JSON)

O `merge_version` cobre justamente o cenário que o e-book cita: troca de número
com regeneração de BSUID, em que o CRM precisa atualizar vínculos sem perder
histórico.

### 2.2 O que quebra, por gravidade

**P0 — Supressão e opt-in chaveados por telefone.**
`whatsapp_suppression_entries`: `phone` não-nulo, unique `(workspace_id, phone)`.
`whatsapp_opt_in_records`: unique `(workspace_id, phone, channel)`.
Consequência: usuário sem telefone visível pede descadastro e **não há onde
registrar**. Isso é falha de LGPD e de política da Meta ao mesmo tempo, não
inconveniência.

**P0 — `whatsapp_contacts.phone` é `nullable=False`.**
Unique `(workspace_id, phone, whatsapp_connection_id)`. Evento sem telefone não
consegue criar contato: a mensagem chega e não tem onde pousar.

**P1 — Campanha e audiência chaveadas por telefone.**
`uq_wa_campaign_recipients_campaign_phone`, `uq_wa_audience_member_list_phone`.
Contato sem telefone não é endereçável em campanha.

**P2 — Ponte do dono (Hermes) resolve por telefone.**
`klm_agent_service.py` filtra `WhatsAppContact.phone == owner_phone`.

**Já corrigido antes desta análise:** o `UNIQUE INDEX ix_whatsapp_contacts_phone`
global (migração `2bc77616f16c`) foi substituído por índice não-único em
`4d7c4c4b6d11`. Se ainda estivesse lá, dois workspaces não poderiam ter o mesmo
telefone — bug multi-tenant latente.

### 2.3 O que falta acrescentar

- Vínculo de identificador de canal → identidade, escopado por portfólio.
  Tipos: `phone`, `bsuid`, `username`, `parent_bsuid`.
- Captura e persistência do BSUID sempre que ele aparecer no evento.
- Username apenas como rótulo de exibição.

---

## 3. Plano de execução

### Onda 0 — Verificar antes de construir (sem código)

**Pergunta que decide o plano inteiro:** o BSUID chega na Cloud API direta ou só
via BSP? O e-book afirma "só via provedor que seja BSP Meta" — mas quem escreve
é a Zenvia, que vende exatamente esse intermediário. Nós usamos a Cloud API
oficial direto.

Se a afirmação proceder, muda custo, contrato e latência da operação inteira.
Verificação no changelog da própria Meta, não em material de fornecedor.

Em paralelo: capturar o payload real de um webhook hoje e registrar quais campos
já chegam. Programar contra evento real, não contra a descrição do PDF.

**Saída:** decisão registrada + amostra de payload real versionada.

### Onda 1 — Tolerância na borda (sem migração)

Roda em paralelo a qualquer outro trabalho no repositório, porque não toca
esquema.

**Cadeia de resolução na entrada**, nesta ordem:
1. BSUID presente → identificador de canal → identidade (mais estável)
2. Telefone presente → `canonical_phone` → identidade
3. Username → **nunca como chave**, só rótulo
4. Nada encontrado → identidade provisória com o que veio

**Evento em formato desconhecido → Central de Exceções**, que já existe, virando
tarefa para humano. Nunca descartar em silêncio: a diferença entre descobrir o
problema numa terça calma e descobrir pela reclamação do cliente.

**Guarda de envio:** nunca por `@`; escolhe telefone e cai para BSUID; sem
nenhum dos dois, a interface diz **"contato não endereçável"**. Falha silenciosa
aqui é perder cliente sem saber que perdeu.

**Prova:** testes com as três formas de evento (só telefone / telefone + BSUID /
só BSUID) afirmando zero cliente duplicado e zero mensagem perdida.

### Onda 2 — Esquema (exige janela coordenada)

- Tabela de identificadores de canal: `(workspace_id, portfolio_id, kind, value)`
  → `identity_id`, unique. Escopada por portfólio desde o primeiro dia.
- `whatsapp_contacts.phone` passa a aceitar nulo; unique existente vira parcial.
- **Supressão e opt-in re-chaveados para identidade**, com telefone rebaixado a
  atributo resolvido. Esta é a que não pode esperar, por ser jurídica.
- Campanha e audiência endereçam identidade, não telefone.

**Restrição operacional:** duas migrações simultâneas dão duas cabeças na cadeia
do Alembic e quebram `tests/migrations/test_single_alembic_head.py` para todos.
Esta onda precisa de janela combinada com quem mais estiver no repositório.

### Onda 3 — Fusão sem perder histórico

Quando o segundo identificador aparece depois, funde usando `merge_version`.
Troca de número com regeneração de BSUID não pode quebrar histórico nem duplicar
cadastro.

Cada fusão auditada e reversível. Fusão errada é pior que duplicata: junta dois
clientes diferentes num só, e isso em escritório de advocacia significa misturar
dois casos.

### Onda 4 — Superfície

- O `@` do escritório como ativo de entrada: site, anúncio, assinatura de
  e-mail. É porta de entrada nova e gratuita.
- Cartão do contato mostra quais identificadores são conhecidos e se o contato é
  endereçável — o advogado precisa ver isso **antes** de tentar iniciar conversa.

### Onda 5 — Prova integral

Bateria que simula a convivência inteira e afirma:
- nenhum cliente duplicado em nenhuma das três formas de evento;
- nenhuma mensagem perdida;
- **opt-out honrado sem telefone**;
- envio para BSUID de outro portfólio recusado antes da rede.

---

## 4. Por que isso é vantagem competitiva, não manutenção

Legal Mail, Legis e o resto do mercado jurídico batem na mesma parede em agosto,
e a maioria vai descobrir em produção. Nós já temos a identidade interna que o
e-book manda construir.

Chegar em agosto com contato sem telefone funcionando, enquanto o concorrente
duplica cadastro e perde histórico, é argumento de venda concreto e verificável.

---

## 5. Ressalva sobre a fonte

O e-book é material comercial de um BSP. O conteúdo técnico sobre BSUID,
escopo por portfólio e comportamento de username é consistente e verificável na
documentação da Meta, mas a afirmação de exclusividade via BSP e o cronograma
específico da Zenvia devem ser conferidos na fonte primária antes de virarem
decisão de arquitetura ou de contrato.
