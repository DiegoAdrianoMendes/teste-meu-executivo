"""
Biblioteca de Templates de Fluxo por Área do Direito
Cada template é um dict compatível com o model Flow.
"""
from typing import Any, Dict, List


def _node(id: str, type: str, label: str, x: float, y: float, **config) -> Dict[str, Any]:
    return {"id": id, "type": type, "label": label, "position": {"x": x, "y": y}, "config": config}


def _edge(id: str, source: str, target: str, label: str = "", condition_value: str = "") -> Dict[str, Any]:
    e = {"id": id, "source": source, "target": target}
    if label:
        e["label"] = label
    if condition_value:
        e["condition_value"] = condition_value
    return e


FLOW_TEMPLATES: List[Dict[str, Any]] = [

    # ─── 1. TRABALHISTA — SDR + Proposta Completo ───────────────────────
    {
        "name": "Trabalhista — SDR + Proposta Completo",
        "description": "Fluxo completo: qualificação via WhatsApp → análise jurídica → minuta de contrato → assinatura ZapSign",
        "practice_area": "trabalhista",
        "is_template": True,
        "is_active": True,
        "trigger": "new_lead",
        "trigger_config": {"column_id": "entrada"},
        "nodes": [
            _node("n1", "trigger", "Novo Lead", 100, 100, event="new_lead"),
            _node("n2", "agent", "SDR — Qualificação WhatsApp", 100, 250, agent="klm_sdr"),
            _node("n3", "condition", "Score ≥ 7?", 100, 400, field="sdr_score", operator="gt", value="6"),
            _node("n4", "wait", "Aguardar 24h (sem resposta)", 350, 400, delay_hours=24, condition="no_response"),
            _node("n5", "send_message", "Follow-up urgência", 350, 550,
                  message="Olá {nome}! Temos apenas 2 vagas disponíveis essa semana. Posso garantir a sua? 🔒"),
            _node("n6", "human_pause", "Revisar proposta", 100, 550, reason="Revisar qualificação SDR e preparar proposta comercial"),
            _node("n7", "agent", "Analyst — Análise Jurídica", 100, 700, agent="klm_analyst"),
            _node("n8", "agent", "Drafter — Minuta do Contrato", 100, 850, agent="klm_drafter"),
            _node("n9", "zapsign_request", "Assinatura — Procuração + Contrato", 100, 1000, doc_type="Procuração + Contrato de Honorários"),
            _node("n10", "send_message", "Link de assinatura enviado", 100, 1150,
                  message="✅ {nome}, preparamos seu contrato! Assine aqui em 2 minutos: {sign_url}"),
        ],
        "edges": [
            _edge("e1-2", "n1", "n2"),
            _edge("e2-3", "n2", "n3"),
            _edge("e3-6", "n3", "n6", "Sim ✓", "true"),
            _edge("e3-4", "n3", "n4", "Não — Follow-up", "false"),
            _edge("e4-5", "n4", "n5"),
            _edge("e5-6", "n5", "n6"),
            _edge("e6-7", "n6", "n7"),
            _edge("e7-8", "n7", "n8"),
            _edge("e8-9", "n8", "n9"),
            _edge("e9-10", "n9", "n10"),
        ],
    },

    # ─── 2. PREVIDENCIÁRIO — BPC/LOAS ──────────────────────────────────
    {
        "name": "Previdenciário — BPC/LOAS",
        "description": "Triagem BPC/LOAS → coleta de documentos → análise de requisitos → procuração INSS",
        "practice_area": "previdenciario",
        "is_template": True,
        "is_active": True,
        "trigger": "new_lead",
        "trigger_config": {"column_id": "entrada"},
        "nodes": [
            _node("n1", "trigger", "Novo Lead", 100, 100, event="new_lead"),
            _node("n2", "agent", "SDR — Triagem BPC/LOAS", 100, 250, agent="klm_sdr"),
            _node("n3", "send_message", "Lista de documentos", 100, 400,
                  message="Olá {nome}! Para dar andamento ao BPC/LOAS, preciso dos seguintes documentos:\n📄 CPF e RG\n📄 Comprovante de renda familiar\n📄 Laudo médico com CID (se deficiência)\n📄 Certidão de Nascimento\n\nPode me enviar por aqui?"),
            _node("n4", "wait", "Aguardar docs (48h)", 100, 550, delay_hours=48, condition="always"),
            _node("n5", "send_message", "Lembrete de documentos", 100, 700,
                  message="{nome}, ainda aguardo os documentos para analisar seu caso de BPC/LOAS. Está com dificuldade em algum deles?"),
            _node("n6", "wait", "Aguardar mais 48h", 100, 850, delay_hours=48, condition="always"),
            _node("n7", "human_pause", "Analisar documentos recebidos", 100, 1000,
                  reason="Verificar documentos enviados pelo cliente e validar requisitos de renda e deficiência"),
            _node("n8", "agent", "Analyst — Requisitos BPC", 100, 1150, agent="klm_analyst"),
            _node("n9", "zapsign_request", "Procuração INSS", 100, 1300, doc_type="Procuração INSS"),
            _node("n10", "send_message", "Próximos passos", 100, 1450,
                  message="✅ {nome}, protocolo junto ao INSS será realizado em até 5 dias úteis. Te manterei informado!"),
        ],
        "edges": [
            _edge("e1-2", "n1", "n2"), _edge("e2-3", "n2", "n3"), _edge("e3-4", "n3", "n4"),
            _edge("e4-5", "n4", "n5"), _edge("e5-6", "n5", "n6"), _edge("e6-7", "n6", "n7"),
            _edge("e7-8", "n7", "n8"), _edge("e8-9", "n8", "n9"), _edge("e9-10", "n9", "n10"),
        ],
    },

    # ─── 3. BANCÁRIO — RMC ─────────────────────────────────────────────
    {
        "name": "Bancário — RMC / Consignado",
        "description": "Triagem de crédito consignado → análise de abusividade → carta ou petição → assinatura",
        "practice_area": "bancario",
        "is_template": True,
        "is_active": True,
        "trigger": "new_lead",
        "trigger_config": {"column_id": "entrada"},
        "nodes": [
            _node("n1", "trigger", "Novo Lead", 100, 100, event="new_lead"),
            _node("n2", "agent", "SDR — Triagem Bancária", 100, 250, agent="klm_sdr"),
            _node("n3", "send_message", "Solicitar documentos", 100, 400,
                  message="Olá {nome}! Para analisar seu caso de crédito consignado, preciso:\n📄 Extrato dos últimos 12 meses\n📄 Contrato do crédito ou cartão\n📄 Holerites recentes\n\nPode enviar?"),
            _node("n4", "wait", "Aguardar 48h", 100, 550, delay_hours=48, condition="always"),
            _node("n5", "agent", "Analyst — Abusividade", 100, 700, agent="klm_analyst"),
            _node("n6", "human_pause", "Confirmar tese jurídica", 100, 850,
                  reason="Validar análise de abusividade de juros antes de prosseguir com a ação"),
            _node("n7", "agent", "Drafter — Carta/Petição", 100, 1000, agent="klm_drafter"),
            _node("n8", "zapsign_request", "Procuração", 100, 1150, doc_type="Procuração"),
        ],
        "edges": [
            _edge("e1-2", "n1", "n2"), _edge("e2-3", "n2", "n3"), _edge("e3-4", "n3", "n4"),
            _edge("e4-5", "n4", "n5"), _edge("e5-6", "n5", "n6"), _edge("e6-7", "n6", "n7"),
            _edge("e7-8", "n7", "n8"),
        ],
    },

    # ─── 4. PLANO DE SAÚDE — Negativa ──────────────────────────────────
    {
        "name": "Plano de Saúde — Negativa de Cobertura",
        "description": "Triagem → urgência vs normal → notificação extrajudicial → petição de tutela se urgência",
        "practice_area": "plano_saude",
        "is_template": True,
        "is_active": True,
        "trigger": "new_lead",
        "trigger_config": {"column_id": "entrada"},
        "nodes": [
            _node("n1", "trigger", "Novo Lead", 100, 100, event="new_lead"),
            _node("n2", "agent", "SDR — Triagem Plano", 100, 250, agent="klm_sdr"),
            _node("n3", "condition", "Urgência/Emergência?", 100, 400, field="urgencia", operator="eq", value="sim"),
            _node("n4", "send_message", "URGENTE — Acionamento", 400, 400,
                  message="⚠️ {nome}, caso URGENTE identificado! Já estamos preparando a notificação extrajudicial. Prazo: 24h para a operadora responder. Mantenha o número de protocolo e documentos médicos à mão."),
            _node("n5", "send_message", "Solicitar carta de negativa", 100, 550,
                  message="Olá {nome}! Preciso da carta de negativa da {operadora} e o número da sua apólice para analisar o caso."),
            _node("n6", "wait", "Aguardar 24h", 100, 700, delay_hours=24, condition="always"),
            _node("n7", "human_pause", "Analisar e decidir via extrajudicial/judicial", 100, 850,
                  reason="Verificar carta de negativa vs rol ANS e decidir estratégia: extrajudicial (3-15 dias) ou tutela de urgência"),
            _node("n8", "agent", "Analyst — Viabilidade ANS", 100, 1000, agent="klm_analyst"),
            _node("n9", "agent", "Drafter — Notificação/Tutela", 100, 1150, agent="klm_drafter"),
            _node("n10", "zapsign_request", "Procuração", 100, 1300, doc_type="Procuração"),
        ],
        "edges": [
            _edge("e1-2", "n1", "n2"), _edge("e2-3", "n2", "n3"),
            _edge("e3-4u", "n3", "n4", "Urgente ⚠️", "true"),
            _edge("e3-5", "n3", "n5", "Normal", "false"),
            _edge("e4-7", "n4", "n7"), _edge("e5-6", "n5", "n6"), _edge("e6-7", "n6", "n7"),
            _edge("e7-8", "n7", "n8"), _edge("e8-9", "n8", "n9"), _edge("e9-10", "n9", "n10"),
        ],
    },

    # ─── 5. ISENÇÃO DE IR ──────────────────────────────────────────────
    {
        "name": "Isenção de IR — Doença Grave",
        "description": "Triagem de doença grave → validação na lista da Receita → petição administrativa",
        "practice_area": "tributario",
        "is_template": True,
        "is_active": True,
        "trigger": "new_lead",
        "trigger_config": {"column_id": "entrada"},
        "nodes": [
            _node("n1", "trigger", "Novo Lead", 100, 100, event="new_lead"),
            _node("n2", "agent", "SDR — Triagem IR", 100, 250, agent="klm_sdr"),
            _node("n3", "send_message", "Solicitar documentos", 100, 400,
                  message="Olá {nome}! Para o pedido de isenção de IR, preciso:\n📄 Laudo médico com CID\n📄 Declarações de IR dos últimos 5 anos\n📄 Documentos pessoais\n\nPode enviar?"),
            _node("n4", "wait", "Aguardar 72h", 100, 550, delay_hours=72, condition="always"),
            _node("n5", "human_pause", "Analisar doença vs lista Receita", 100, 700,
                  reason="Verificar se CID consta nas 14 doenças isentas (art. 6° Lei 7713/88): neoplasia maligna, cardiopatia grave, TB ativa, alienação mental, esclerose múltipla, cegueira, hanseníase, paralisia irreversível, nefropatia grave, hepatopatia grave, espondiloartrose, paget grave, contaminação por radiação, fibrose cística"),
            _node("n6", "agent", "Analyst — Lei 7713/88", 100, 850, agent="klm_analyst"),
            _node("n7", "agent", "Drafter — Petição Adm/Judicial", 100, 1000, agent="klm_drafter"),
            _node("n8", "zapsign_request", "Procuração + Autorização", 100, 1150, doc_type="Procuração + Autorização de Representação"),
            _node("n9", "scheduled_message", "Follow-up D+30", 100, 1300,
                  message="Olá {nome}, atualização sobre seu processo de isenção de IR: ✅ Petição protocolada, aguardando resposta da Receita Federal (prazo: 30 dias).",
                  scheduled_at="09:00"),
        ],
        "edges": [
            _edge("e1-2", "n1", "n2"), _edge("e2-3", "n2", "n3"), _edge("e3-4", "n3", "n4"),
            _edge("e4-5", "n4", "n5"), _edge("e5-6", "n5", "n6"), _edge("e6-7", "n6", "n7"),
            _edge("e7-8", "n7", "n8"), _edge("e8-9", "n8", "n9"),
        ],
    },

    # ─── 6. SEGUROS ────────────────────────────────────────────────────
    {
        "name": "Seguros — Sinistro Negado",
        "description": "Triagem → análise excludentes SUSEP → reclamação ou petição",
        "practice_area": "seguros",
        "is_template": True,
        "is_active": True,
        "trigger": "new_lead",
        "trigger_config": {"column_id": "entrada"},
        "nodes": [
            _node("n1", "trigger", "Novo Lead", 100, 100, event="new_lead"),
            _node("n2", "agent", "SDR — Triagem Seguro", 100, 250, agent="klm_sdr"),
            _node("n3", "send_message", "Solicitar documentos", 100, 400,
                  message="Olá {nome}! Para analisar o sinistro negado, preciso:\n📄 Apólice do seguro\n📄 Carta de negativa da seguradora\n📄 Boletim de ocorrência (se houver)\n📄 Documentos do sinistro\n\nPode enviar?"),
            _node("n4", "wait", "Aguardar 48h", 100, 550, delay_hours=48, condition="always"),
            _node("n5", "agent", "Analyst — Excludentes SUSEP", 100, 700, agent="klm_analyst"),
            _node("n6", "human_pause", "Validar tese antes de acionar", 100, 850,
                  reason="Verificar excludentes contratuais vs cobertura obrigatória SUSEP. Decidir: reclamação SUSEP (15 dias) ou ação judicial"),
            _node("n7", "agent", "Drafter — Reclamação/Petição", 100, 1000, agent="klm_drafter"),
            _node("n8", "zapsign_request", "Procuração", 100, 1150, doc_type="Procuração"),
            _node("n9", "send_message", "Prazo para resposta", 100, 1300,
                  message="✅ {nome}, notificação enviada. A seguradora tem 15 dias úteis para responder. Te aviso assim que houver retorno!"),
        ],
        "edges": [
            _edge("e1-2", "n1", "n2"), _edge("e2-3", "n2", "n3"), _edge("e3-4", "n3", "n4"),
            _edge("e4-5", "n4", "n5"), _edge("e5-6", "n5", "n6"), _edge("e6-7", "n6", "n7"),
            _edge("e7-8", "n7", "n8"), _edge("e8-9", "n8", "n9"),
        ],
    },

    # ─── 7. UNIVERSAL — Follow-up 3 Etapas ────────────────────────────
    {
        "name": "Follow-up Automático — 3 Etapas",
        "description": "Sequência D+1, D+3, D+7. Funciona em qualquer área do direito.",
        "practice_area": "universal",
        "is_template": True,
        "is_active": True,
        "trigger": "column_moved",
        "trigger_config": {},
        "nodes": [
            _node("n1", "trigger", "Card movido de coluna", 100, 100, event="column_moved"),
            _node("n2", "wait", "Aguardar 24h", 100, 250, delay_hours=24, condition="always"),
            _node("n3", "send_message", "Follow-up D+1", 100, 400,
                  message="Olá {nome}, tudo bem? Queria saber se conseguiu reunir os documentos que conversamos. Posso te ajudar com alguma dúvida? 😊"),
            _node("n4", "wait", "Aguardar 48h", 100, 550, delay_hours=48, condition="always"),
            _node("n5", "send_message", "Follow-up D+3 — Urgência", 100, 700,
                  message="André, só posso pegar mais 2 novos casos essa semana. Como o seu tem potencial, se quiser garantir, me avisa até o fim do dia. Combinado? ⏰"),
            _node("n6", "wait", "Aguardar 96h", 100, 850, delay_hours=96, condition="always"),
            _node("n7", "send_message", "Follow-up D+7 — Prazo", 100, 1000,
                  message="Olá {nome}, o prazo para pedir sua indenização diminui a cada dia que passa. Posso fazer uma análise gratuita do seu caso hoje. Tem 5 minutos?"),
            _node("n8", "human_pause", "Lead frio — decidir ação", 100, 1150,
                  reason="Lead não respondeu em 7 dias. Decidir: arquivar caso, manter em espera, ou tentar contato por outro canal."),
        ],
        "edges": [
            _edge("e1-2", "n1", "n2"), _edge("e2-3", "n2", "n3"), _edge("e3-4", "n3", "n4"),
            _edge("e4-5", "n4", "n5"), _edge("e5-6", "n5", "n6"), _edge("e6-7", "n6", "n7"),
            _edge("e7-8", "n7", "n8"),
        ],
    },

    # ─── 8. UNIVERSAL — Coleta de Documentos ──────────────────────────
    {
        "name": "Coleta de Documentos — Universal",
        "description": "Solicita documentos via WhatsApp com 2 lembretes automáticos",
        "practice_area": "universal",
        "is_template": True,
        "is_active": True,
        "trigger": "manual",
        "trigger_config": {},
        "nodes": [
            _node("n1", "trigger", "Início manual", 100, 100, event="manual"),
            _node("n2", "send_message", "Solicitar documentos", 100, 250,
                  message="Olá {nome}! Para dar andamento ao seu caso, preciso dos seguintes documentos:\n\n{lista_documentos}\n\nPode me enviar aqui pelo WhatsApp? 📎"),
            _node("n3", "wait", "Aguardar 72h", 100, 400, delay_hours=72, condition="always"),
            _node("n4", "send_message", "Lembrete gentil", 100, 550,
                  message="Oi {nome}! Ainda estou aguardando os documentos para analisar seu caso. Está tendo dificuldade com algum deles? Posso ajudar! 😊"),
            _node("n5", "wait", "Aguardar mais 48h", 100, 700, delay_hours=48, condition="always"),
            _node("n6", "send_message", "Última tentativa", 100, 850,
                  message="{nome}, última mensagem antes de suspender a análise do seu caso. Ainda tem interesse? Se sim, me envie os documentos hoje."),
            _node("n7", "human_pause", "Decidir próximo passo", 100, 1000,
                  reason="Cliente não enviou documentos. Decidir: arquivar temporariamente, ligar diretamente, ou encerrar o caso."),
        ],
        "edges": [
            _edge("e1-2", "n1", "n2"), _edge("e2-3", "n2", "n3"), _edge("e3-4", "n3", "n4"),
            _edge("e4-5", "n4", "n5"), _edge("e5-6", "n5", "n6"), _edge("e6-7", "n6", "n7"),
        ],
    },
]


async def seed_templates(db) -> int:
    """
    Insere templates na tabela flows se ainda não existirem.
    Chamado no startup da aplicação ou via script.
    Retorna número de templates inseridos.
    """
    from sqlalchemy import select
    from app.models.flow import Flow

    count = 0
    for template_data in FLOW_TEMPLATES:
        # Verificar se já existe
        existing = await db.execute(
            select(Flow).where(
                Flow.name == template_data["name"],
                Flow.is_template == True  # noqa
            )
        )
        if existing.scalar_one_or_none():
            continue

        flow = Flow(
            workspace_id=None,
            name=template_data["name"],
            description=template_data.get("description"),
            practice_area=template_data["practice_area"],
            is_template=True,
            is_active=True,
            trigger=template_data["trigger"],
            trigger_config=template_data.get("trigger_config"),
            nodes=template_data["nodes"],
            edges=template_data["edges"],
        )
        db.add(flow)
        count += 1

    if count > 0:
        await db.commit()

    return count
