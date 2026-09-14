"""Canonical, versioned catalog for plug-and-play legal practice areas.

The catalog is deliberately provider-agnostic. It describes the domain contract used
by the inbox, CRM, playbooks, contracts, flows and agent router without enabling any
external side effect.
"""
from __future__ import annotations

import unicodedata
from copy import deepcopy
from typing import Any, Iterable


LEGAL_AREA_CATALOG_VERSION = "2026.07.25.2"


def _product(
    key: str,
    label: str,
    description: str,
    *,
    keywords: Iterable[str],
    documents: Iterable[str],
    specialist: str,
    closer: str,
    audience: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "description": description,
        "keywords": list(keywords),
        "requiredDocuments": list(documents),
        "specialistAgentKey": specialist,
        "closerAgentKey": closer,
        "contractTemplateKey": f"contrato_{key}",
        "playbookScope": f"produto:{key}",
        "audience": audience,
    }


def _agent(
    key: str,
    name: str,
    role: str,
    *,
    parent: str,
    description: str,
    task_class: str,
    tools: Iterable[str],
    gate: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "role": role,
        "parentAgentKey": parent,
        "description": description,
        "taskClass": task_class,
        "tools": list(tools),
        "gate": gate,
    }


SHARED_FUNNEL = [
    "novo_lead",
    "triagem_sdr",
    "documentos",
    "reuniao",
    "proposta",
    "contrato",
    "pagamento",
    "ganho",
    "perdido",
]

SHARED_AGENTS = [
    "orquestrador",
    "hermes",
    "crm",
    "secretaria",
    "paralegal",
    "peticao",
    "revisor_juridico",
    "andamento",
]

SHARED_GATES = {
    "humanRequestAgent": "orquestrador",
    "humanHandoffCoordinator": "orquestrador",
    "petitionAlwaysRequiresApproval": True,
    "externalActionsDisabled": True,
    "audioRequiresRepeatedRequest": True,
}


def _area(
    key: str,
    name: str,
    description: str,
    *,
    products: list[dict[str, Any]],
    documents: Iterable[str],
    required_fields: Iterable[str],
    audiences: Iterable[str],
    sdr: str,
    specialist: str,
    closer: str,
    default_active: bool = False,
    legacy_keys: Iterable[str] = (),
) -> dict[str, Any]:
    product_agents = [product["specialistAgentKey"] for product in products]
    agent_team = list(dict.fromkeys([*SHARED_AGENTS[:2], sdr, specialist, closer, *product_agents, *SHARED_AGENTS[2:]]))
    subagents = [
        _agent(
            sdr,
            f"SDR {name}",
            "Triagem especializada",
            parent="orquestrador",
            description=f"Qualifica a demanda de {name}, identifica o produto e coleta o conjunto mínimo de evidências.",
            task_class="simple",
            tools=("conversation_rag", "acquisition_context", "checklist", "crm_read", "handoff"),
            gate="Não emite conclusão jurídica nem promete resultado.",
        ),
        _agent(
            specialist,
            f"Jurídico {name}",
            "Especialista da área",
            parent="orquestrador",
            description=f"Analisa fatos, documentos e riscos de {name} e prepara a orientação inicial para revisão humana.",
            task_class="complex",
            tools=("conversation_rag", "document_rag", "playbook", "legal_sources", "internal_note"),
            gate="Conclusões decisivas, estratégia e atos externos exigem revisão humana.",
        ),
        _agent(
            closer,
            f"Closer {name}",
            "Fechamento técnico da área",
            parent="orquestrador",
            description=f"Conduz objeções e proposta de {name} usando somente política comercial e parecer interno vigentes.",
            task_class="medium",
            tools=("conversation_rag", "objection_playbook", "proposal_draft", "booking", "contract_draft"),
            gate="Não altera honorários, descontos, autonomia ou contrato sem alçada aprovada.",
        ),
    ]
    for product in products:
        product_agent = product["specialistAgentKey"]
        if product_agent == specialist:
            continue
        product_subagent = _agent(
            product_agent,
            f"Especialista em {product['label']}",
            "Subagente por produto jurídico",
            parent=specialist,
            description=f"Aprofunda a triagem e os riscos de {product['label']} sem sair do contexto do mesmo cliente e caso.",
            task_class="complex",
            tools=("conversation_rag", "document_rag", "product_playbook", "legal_sources", "risk_checklist"),
            gate="Opera apenas no produto vinculado e sempre preserva a revisão humana dos atos jurídicos.",
        )
        product_subagent["productKey"] = product["key"]
        subagents.append(product_subagent)

    return {
        "areaKey": key,
        "name": name,
        "description": description,
        "catalogVersion": LEGAL_AREA_CATALOG_VERSION,
        "catalogManaged": True,
        "defaultActive": default_active,
        "legacyKeys": list(legacy_keys),
        "taxonomy": {
            "subthemes": [product["key"] for product in products],
            "requiredFields": list(required_fields),
        },
        "products": products,
        "documents": list(dict.fromkeys([*documents, *(doc for product in products for doc in product["requiredDocuments"])])),
        "funnel": SHARED_FUNNEL,
        "agentTeam": agent_team,
        "subagents": subagents,
        "routing": {
            "coordinationAgentKey": "orquestrador",
            "ownerBridgeAgentKey": "hermes",
            "ownerBridgeDelegatorKey": "secretaria",
            "sdrAgentKey": sdr,
            "specialistAgentKey": specialist,
            "closerAgentKey": closer,
            "fallbackAgentKey": specialist,
            "manualOverrideWins": True,
            "productKeywordRouting": True,
        },
        "agentGovernance": {
            "policyVersion": "agent-governance-v1.4.0",
            "maxDelegationDepth": 4,
            "enforceParentChain": True,
            "manualOverrideWins": True,
            "hermesCannotOrchestrate": True,
            "crossAreaAccessDisabled": True,
            "crossProductAccessDisabled": True,
            "crossWorkspaceAccessDenied": True,
            "crossClientAccessDenied": True,
            "leastPrivilegeTools": True,
            "independentReviewRequired": True,
            "externalActionsDisabled": True,
        },
        "contracts": [
            {
                "key": product["contractTemplateKey"],
                "label": f"Contrato - {product['label']}",
                "productKey": product["key"],
                "playbookScope": product["playbookScope"],
            }
            for product in products
        ],
        "playbook": {
            "onboardingRequired": True,
            "sections": [
                "posicionamento",
                "qualificacao",
                "documentos",
                "objecoes",
                "honorarios",
                "contratos",
                "handoffs",
                "faq",
            ],
        },
        "communicationProfile": {
            "tone": "balanced",
            "formality": "formal",
            "neutralLanguage": True,
            "plainLanguage": True,
            "signatureStyle": "office",
            "preferredTerms": [],
            "avoidedTerms": [],
            "customInstructions": None,
        },
        "audiences": list(audiences),
        "gates": deepcopy(SHARED_GATES),
    }


LEGAL_AREA_CATALOG: tuple[dict[str, Any], ...] = (
    _area(
        "saude_suplementar",
        "Negativas de plano de saúde",
        "Atendimento de urgência e contencioso para negativas de cobertura, tratamentos e procedimentos.",
        products=[
            _product("medicamento_alto_custo", "Medicamento de alto custo", "Cobertura de fármacos prescritos e ciclos de tratamento.", keywords=("medicamento", "remedio", "quimioterapia", "imunoterapia"), documents=("prescricao_medica", "relatorio_medico", "negativa_plano"), specialist="saude_medicamentos", closer="closer_saude", audience="Pacientes e responsáveis"),
            _product("tratamento", "Tratamento", "Cobertura de tratamento continuado, terapias e protocolos.", keywords=("tratamento", "terapia", "sessao"), documents=("prescricao_medica", "plano_terapeutico", "negativa_plano"), specialist="juridico_saude", closer="closer_saude", audience="Pacientes e responsáveis"),
            _product("home_care", "Home care", "Internação e assistência domiciliar prescritas.", keywords=("home care", "internacao domiciliar", "cuidador"), documents=("prescricao_home_care", "relatorio_medico", "negativa_plano"), specialist="saude_home_care", closer="closer_saude", audience="Pacientes e familiares"),
            _product("cirurgia", "Cirurgia", "Autorização de cirurgia, materiais e equipe assistencial.", keywords=("cirurgia", "procedimento cirurgico"), documents=("pedido_cirurgico", "relatorio_medico", "negativa_plano"), specialist="saude_cirurgia", closer="closer_saude", audience="Pacientes e responsáveis"),
            _product("exame", "Exame", "Autorização de exames diagnósticos e de acompanhamento.", keywords=("exame", "pet-ct", "ressonancia", "tomografia"), documents=("pedido_exame", "relatorio_medico", "negativa_plano"), specialist="juridico_saude", closer="closer_saude", audience="Pacientes e responsáveis"),
            _product("opme", "OPME", "Órteses, próteses e materiais especiais indicados.", keywords=("opme", "protese", "ortese", "material cirurgico"), documents=("pedido_opme", "orcamentos", "negativa_plano"), specialist="saude_opme", closer="closer_saude", audience="Pacientes e responsáveis"),
            _product("reajuste_abusivo", "Reajuste abusivo", "Contestação de reajuste anual, por faixa etária ou por sinistralidade.", keywords=("reajuste", "aumento abusivo", "faixa etaria", "sinistralidade"), documents=("boletos_historicos", "comunicado_reajuste", "contrato_plano"), specialist="saude_reajustes", closer="closer_saude", audience="Beneficiários e empresas contratantes"),
            _product("cancelamento_plano", "Cancelamento do plano", "Restabelecimento de plano cancelado ou rescindido indevidamente.", keywords=("cancelamento", "plano cancelado", "rescisao unilateral", "exclusao beneficiario"), documents=("comunicado_cancelamento", "boletos_comprovantes", "contrato_plano"), specialist="saude_cancelamento", closer="closer_saude", audience="Beneficiários e empresas contratantes"),
        ],
        documents=("documento_identificacao", "carteirinha_plano", "contrato_plano"),
        required_fields=("productKey", "objectName", "healthInsurer", "healthDenialReason", "medicalUrgencyHours", "economicValueAnnual"),
        audiences=("Paciente", "Responsável legal", "Familiar"),
        sdr="sdr_saude",
        specialist="juridico_saude",
        closer="closer_saude",
        default_active=False,
        legacy_keys=("plano_saude",),
    ),
    _area(
        "erro_medico",
        "Erro médico e responsabilidade em saúde",
        "Análise de eventos adversos, falhas assistenciais, diagnóstico, cirurgia e responsabilidade de hospitais e profissionais.",
        products=[
            _product("erro_diagnostico", "Erro ou atraso de diagnóstico", "Falha, atraso ou perda de oportunidade diagnóstica.", keywords=("erro diagnostico", "diagnostico tardio", "exame nao solicitado"), documents=("prontuario_medico", "exames", "laudo_especialista"), specialist="erro_medico_diagnostico", closer="closer_erro_medico", audience="Pacientes e familiares"),
            _product("erro_cirurgico", "Erro cirúrgico", "Dano relacionado a cirurgia, técnica, material ou acompanhamento.", keywords=("erro cirurgia", "complicacao cirurgica", "material esquecido"), documents=("prontuario_medico", "relatorio_cirurgico", "exames_pos_operatorios"), specialist="erro_medico_cirurgico", closer="closer_erro_medico", audience="Pacientes e familiares"),
            _product("infeccao_hospitalar", "Infecção e segurança hospitalar", "Apuração de infecção, protocolos e falhas de segurança assistencial.", keywords=("infeccao hospitalar", "sepse", "contaminacao"), documents=("prontuario_medico", "culturas_exames", "relatorios_internacao"), specialist="erro_medico_hospitalar", closer="closer_erro_medico", audience="Pacientes e familiares"),
            _product("erro_obstetrico", "Erro obstétrico", "Danos maternos ou neonatais no pré-natal, parto ou pós-parto.", keywords=("erro obstetrico", "parto", "violencia obstetrica", "dano neonatal"), documents=("cartao_pre_natal", "prontuario_materno", "prontuario_neonatal"), specialist="erro_medico_obstetrico", closer="closer_erro_medico", audience="Gestantes, mães e famílias"),
            _product("falha_medicacao", "Falha de medicação ou tratamento", "Prescrição, dispensação, dose ou acompanhamento inadequado.", keywords=("medicacao errada", "dose errada", "reacao medicamento", "tratamento inadequado"), documents=("prescricoes", "prontuario_medico", "relatorio_evento_adverso"), specialist="erro_medico_medicacao", closer="closer_erro_medico", audience="Pacientes e familiares"),
        ],
        documents=("documento_identificacao", "autorizacao_prontuario", "cronologia_atendimento"),
        required_fields=("productKey", "providerName", "healthFacility", "eventDate", "injurySummary", "currentCondition", "evidenceStatus"),
        audiences=("Paciente", "Responsável legal", "Familiar"),
        sdr="sdr_erro_medico",
        specialist="juridico_erro_medico",
        closer="closer_erro_medico",
    ),
    _area(
        "bancario",
        "Bancário",
        "Fraudes, crédito, descontos, bloqueios e conflitos com instituições financeiras.",
        products=[
            _product("fraude_pix", "Fraude e golpe bancário", "PIX, engenharia social, conta invadida e transações não reconhecidas.", keywords=("pix", "golpe", "fraude", "transacao nao reconhecida"), documents=("extrato_bancario", "boletim_ocorrencia", "protocolos_banco"), specialist="bancario_fraudes", closer="closer_bancario", audience="Consumidores bancários"),
            _product("consignado_rmc", "Consignado, RMC e RCC", "Contratações e descontos consignados contestados.", keywords=("consignado", "rmc", "rcc", "cartao consignado"), documents=("extrato_beneficio", "contrato_bancario", "extrato_bancario"), specialist="bancario_consignado", closer="closer_bancario", audience="Aposentados, pensionistas e servidores"),
            _product("juros_abusivos", "Juros e revisão contratual", "Revisão de crédito, financiamento e renegociação abusiva.", keywords=("juros", "financiamento", "revisional", "parcela"), documents=("contrato_bancario", "planilha_parcelas", "comprovantes_pagamento"), specialist="bancario_contratos", closer="closer_bancario", audience="Pessoas e empresas"),
            _product("bloqueio_conta", "Bloqueio ou encerramento de conta", "Conta, saldo ou meio de pagamento bloqueado sem solução adequada.", keywords=("conta bloqueada", "saldo bloqueado", "conta encerrada"), documents=("extrato_bancario", "comunicacao_banco", "protocolos_banco"), specialist="bancario_bloqueios", closer="closer_bancario", audience="Pessoas e empresas"),
            _product("superendividamento", "Superendividamento", "Repactuação e revisão de dívidas em bloco conforme o Estatuto do Superendividado.", keywords=("superendividamento", "repactuacao de dividas", "minimo existencial", "conciliacao de dividas"), documents=("relacao_dividas", "comprovantes_renda", "extratos_bancarios"), specialist="juridico_bancario", closer="closer_bancario", audience="Consumidores superendividados"),
            _product("busca_apreensao_veiculo", "Busca e apreensão de veículo", "Defesa em ação de busca e apreensão por alienação fiduciária, purgação de mora e revisão de saldo.", keywords=("busca e apreensao", "alienacao fiduciaria", "veiculo apreendido", "purga da mora"), documents=("contrato_financiamento", "citacao_busca_apreensao", "comprovantes_pagamento"), specialist="juridico_bancario", closer="closer_bancario", audience="Financiados em veículo"),
            _product("revisao_financiamento", "Revisão de financiamento", "Revisão de cláusulas, tarifas e encargos em financiamento de veículo ou bem.", keywords=("revisao financiamento", "tac tarifa cadastro", "juros financiamento veiculo", "encargos abusivos"), documents=("contrato_financiamento", "planilha_parcelas", "comprovantes_pagamento"), specialist="juridico_bancario", closer="closer_bancario", audience="Consumidores financiados"),
            _product("credito_rural", "Crédito rural", "Renegociação, revisão e defesa em operações de crédito rural (CCB, cédula rural, Pronaf).", keywords=("credito rural", "cedula de credito rural", "pronaf", "divida rural"), documents=("contrato_credito_rural", "extrato_operacao", "comprovantes_pagamento"), specialist="juridico_bancario", closer="closer_bancario", audience="Produtores rurais"),
        ],
        documents=("documento_identificacao", "comprovante_endereco"),
        required_fields=("productKey", "financialInstitution", "transactionDates", "estimatedLoss", "protocols"),
        audiences=("Consumidor", "Aposentado ou pensionista", "Empresa"),
        sdr="sdr_bancario",
        specialist="juridico_bancario",
        closer="closer_bancario",
        legacy_keys=("fraude_bancaria",),
    ),
    _area(
        "trabalhista",
        "Trabalhista",
        "Reclamações, defesas, rescisões, acidentes e relações de trabalho.",
        products=[
            _product("reclamacao_empregado", "Reclamação do trabalhador", "Verbas, jornada, vínculo e descumprimentos contratuais.", keywords=("demissao", "hora extra", "fgts", "verbas", "registro"), documents=("ctps", "contracheques", "termo_rescisao"), specialist="trabalhista_reclamante", closer="closer_trabalhista", audience="Trabalhadores"),
            _product("defesa_empregador", "Defesa do empregador", "Resposta preventiva ou contenciosa da empresa.", keywords=("reclamacao trabalhista", "empresa", "audiencia", "defesa"), documents=("peticao_inicial", "contrato_trabalho", "folha_ponto"), specialist="trabalhista_empresarial", closer="closer_trabalhista", audience="Empresas e empregadores"),
            _product("acidente_doenca", "Acidente e doença do trabalho", "Estabilidade, afastamento e reparação por dano ocupacional.", keywords=("acidente", "doenca ocupacional", "cat", "afastamento"), documents=("cat", "laudos_medicos", "documentos_inss"), specialist="trabalhista_acidente", closer="closer_trabalhista", audience="Trabalhadores e empresas"),
            _product("assedio_discriminacao", "Assédio e discriminação", "Provas e providências sobre assédio moral, sexual ou discriminação.", keywords=("assedio", "discriminacao", "humilhacao"), documents=("mensagens", "testemunhas", "comunicacoes_internas"), specialist="trabalhista_assedio", closer="closer_trabalhista", audience="Trabalhadores e empresas"),
            _product("reconhecimento_vinculo", "Reconhecimento de vínculo empregatício", "Reconhecimento de relação de emprego mascarada por PJ, autônomo ou informalidade.", keywords=("reconhecimento de vinculo", "pejotizacao", "vinculo empregaticio", "carteira nao assinada"), documents=("comprovantes_pagamento", "trocas_mensagens", "comprovantes_subordinacao"), specialist="juridico_trabalhista", closer="closer_trabalhista", audience="Trabalhadores"),
            _product("rescisao_indireta", "Rescisão indireta", "Rompimento do contrato por falta grave do empregador, com verbas de dispensa sem justa causa.", keywords=("rescisao indireta", "falta grave empregador", "justa causa do empregador"), documents=("contracheques", "comunicacoes_internas", "provas_falta_grave"), specialist="juridico_trabalhista", closer="closer_trabalhista", audience="Trabalhadores"),
            _product("estabilidade_gestante", "Estabilidade da gestante", "Reintegração ou indenização por dispensa de empregada gestante em período de estabilidade.", keywords=("estabilidade gestante", "demissao gravida", "reintegracao gestante"), documents=("exame_gravidez", "termo_rescisao", "ctps"), specialist="juridico_trabalhista", closer="closer_trabalhista", audience="Trabalhadoras gestantes"),
            _product("fgts_saque_revisao", "FGTS: saque e revisão de depósitos", "Liberação de saque, revisão de depósitos não recolhidos e diferenças de FGTS.", keywords=("fgts", "saque fgts", "fgts nao recolhido", "diferenca de fgts"), documents=("extrato_fgts", "ctps", "contracheques"), specialist="juridico_trabalhista", closer="closer_trabalhista", audience="Trabalhadores"),
        ],
        documents=("documento_identificacao", "contrato_trabalho"),
        required_fields=("productKey", "employmentStart", "employmentEnd", "role", "salary", "factsSummary"),
        audiences=("Trabalhador", "Empregador", "Departamento pessoal"),
        sdr="sdr_trabalhista",
        specialist="juridico_trabalhista",
        closer="closer_trabalhista",
    ),
    _area(
        "previdenciario",
        "Previdenciário",
        "Benefícios do INSS, BPC/LOAS, aposentadorias, incapacidade e revisões.",
        products=[
            _product("bpc_loas", "BPC/LOAS", "Benefício assistencial à pessoa idosa ou com deficiência.", keywords=("bpc", "loas", "beneficio assistencial"), documents=("cadunico", "laudos_medicos", "renda_familiar"), specialist="previdenciario_bpc", closer="closer_previdenciario", audience="Pessoa idosa, pessoa com deficiência e família"),
            _product("beneficio_incapacidade", "Benefício por incapacidade", "Auxílio por incapacidade temporária e aposentadoria por incapacidade permanente.", keywords=("auxilio doenca", "incapacidade", "pericia", "inss"), documents=("laudos_medicos", "cnis", "processo_administrativo"), specialist="previdenciario_incapacidade", closer="closer_previdenciario", audience="Segurados do INSS"),
            _product("aposentadoria", "Aposentadoria", "Planejamento, requerimento e correção de aposentadoria.", keywords=("aposentadoria", "tempo de contribuicao", "idade"), documents=("cnis", "ctps", "ppp"), specialist="previdenciario_aposentadoria", closer="closer_previdenciario", audience="Segurados do INSS"),
            _product("pensao_morte", "Pensão por morte", "Dependência, qualidade de segurado e requerimento de pensão.", keywords=("pensao por morte", "falecimento", "dependente"), documents=("certidao_obito", "documentos_dependencia", "cnis"), specialist="previdenciario_pensao", closer="closer_previdenciario", audience="Dependentes"),
            _product("revisao_beneficio", "Revisão de benefício", "Revisão de cálculo, vínculos, salários e períodos reconhecidos.", keywords=("revisao", "beneficio baixo", "calculo inss"), documents=("carta_concessao", "processo_administrativo", "cnis"), specialist="previdenciario_revisao", closer="closer_previdenciario", audience="Beneficiários do INSS"),
            _product("salario_maternidade", "Salário-maternidade", "Benefício previdenciário à segurada gestante, adotante ou por natimorto/aborto não criminoso.", keywords=("salario maternidade", "licenca maternidade inss", "beneficio gestante", "auxilio maternidade"), documents=("certidao_nascimento", "cnis", "documentos_gestacao"), specialist="juridico_previdenciario", closer="closer_previdenciario", audience="Gestantes, adotantes e seguradas"),
            _product("auxilio_acidente", "Auxílio-acidente", "Indenização por sequela permanente que reduz a capacidade de trabalho após acidente.", keywords=("auxilio acidente", "sequela permanente", "reducao capacidade laboral", "acidente trabalho inss"), documents=("laudos_medicos", "cnis", "cat"), specialist="juridico_previdenciario", closer="closer_previdenciario", audience="Segurados do INSS"),
            _product("aposentadoria_rural", "Aposentadoria rural", "Aposentadoria por idade ou tempo de atividade rural, com comprovação de trabalho no campo.", keywords=("aposentadoria rural", "trabalhador rural", "segurado especial", "autodeclaracao rural"), documents=("autodeclaracao_rural", "documentos_terra", "prova_testemunhal"), specialist="juridico_previdenciario", closer="closer_previdenciario", audience="Trabalhadores rurais e segurados especiais"),
            _product("aposentadoria_pcd", "Aposentadoria da pessoa com deficiência", "Regra diferenciada de aposentadoria por tempo de contribuição ou idade para pessoa com deficiência.", keywords=("aposentadoria pcd", "aposentadoria deficiencia", "lc 142", "pessoa com deficiencia inss"), documents=("laudos_medicos", "avaliacao_biopsicossocial", "cnis"), specialist="juridico_previdenciario", closer="closer_previdenciario", audience="Pessoas com deficiência seguradas do INSS"),
            _product("planejamento_previdenciario", "Planejamento previdenciário", "Simulação e estratégia para melhor data e regra de aposentadoria.", keywords=("planejamento previdenciario", "simulacao aposentadoria", "melhor regra", "estrategia aposentadoria"), documents=("cnis", "carteira_trabalho", "historico_contributivo"), specialist="juridico_previdenciario", closer="closer_previdenciario", audience="Segurados que ainda não requereram aposentadoria"),
        ],
        documents=("documento_identificacao", "comprovante_endereco"),
        required_fields=("productKey", "benefitNumber", "administrativeStatus", "contributionHistory", "factsSummary"),
        audiences=("Segurado", "Beneficiário", "Dependente", "Família"),
        sdr="sdr_previdenciario",
        specialist="juridico_previdenciario",
        closer="closer_previdenciario",
        legacy_keys=("bpc",),
    ),
    _area(
        "servidor_publico",
        "Servidor público (RPPS)",
        "Benefícios, incorporações, revisão funcional e defesa disciplinar de servidores públicos no regime próprio de previdência.",
        products=[
            _product("beneficios_servidor", "Benefícios e incorporações do RPPS", "Concessão de aposentadoria, pensão e incorporação de vantagens no regime próprio de previdência do servidor.", keywords=("aposentadoria servidor", "rpps", "incorporacao de vantagem", "tempo de contribuicao servidor", "pensao servidor"), documents=("ficha_funcional", "certidao_tempo_contribuicao", "processo_administrativo"), specialist="servidor_publico_beneficios", closer="closer_servidor_publico", audience="Servidores públicos e aposentados de RPPS"),
            _product("revisao_servidor", "Revisão e reenquadramento funcional", "Revisão de proventos, reenquadramento de carreira, progressão e paridade negada.", keywords=("revisao de proventos", "reenquadramento", "progressao funcional", "paridade servidor", "isonomia servidor"), documents=("ficha_funcional", "contracheques", "ato_administrativo"), specialist="juridico_servidor_publico", closer="closer_servidor_publico", audience="Servidores públicos ativos e aposentados"),
            _product("disciplina_processo_adm", "PAD e defesa disciplinar", "Defesa técnica em processo administrativo disciplinar, sindicância e responsabilização funcional.", keywords=("pad", "processo administrativo disciplinar", "sindicancia", "demissao servidor", "defesa disciplinar"), documents=("portaria_instauracao", "notificacao_pad", "processo_administrativo"), specialist="servidor_publico_disciplinar", closer="closer_servidor_publico", audience="Servidores públicos investigados"),
        ],
        documents=("documento_identificacao", "comprovante_vinculo_funcional"),
        required_fields=("productKey", "publicEntity", "career", "administrativeStatus", "factsSummary"),
        audiences=("Servidor público ativo", "Servidor público aposentado", "Pensionista de RPPS", "Família"),
        sdr="sdr_servidor_publico",
        specialist="juridico_servidor_publico",
        closer="closer_servidor_publico",
    ),
    _area(
        "tributario",
        "Tributário",
        "Consultivo, recuperação, isenções, passivos e contencioso fiscal para pessoas e empresas.",
        products=[
            _product("isencao_ir_doenca_grave", "Isenção de IR por doença grave", "Reconhecimento, repetição e cessação de retenções indevidas.", keywords=("isencao ir", "doenca grave", "cancer", "molestia grave"), documents=("laudos_medicos", "informes_rendimentos", "declaracoes_ir"), specialist="tributario_isencao", closer="closer_tributario", audience="Aposentados, pensionistas e militares reformados"),
            _product("recuperacao_tributaria", "Recuperação tributária", "Mapeamento e recuperação de créditos fiscais.", keywords=("credito tributario", "recuperacao", "compensacao"), documents=("sped", "declaracoes_fiscais", "guias_recolhimento"), specialist="tributario_recuperacao", closer="closer_tributario", audience="Empresas e entidades"),
            _product("execucao_fiscal", "Execução e passivo fiscal", "Defesa, garantia, prescrição e estratégia de regularização.", keywords=("execucao fiscal", "divida ativa", "penhora", "cda"), documents=("citacao", "cda", "processo_fiscal"), specialist="tributario_execucao_fiscal", closer="closer_tributario", audience="Pessoas e empresas executadas"),
            _product("transacao_parcelamento", "Transação e parcelamento", "Negociação e regularização de débitos fiscais.", keywords=("transacao tributaria", "parcelamento", "regularize"), documents=("relatorio_fiscal", "inscricoes_divida_ativa", "dados_financeiros"), specialist="tributario_negociacao", closer="closer_tributario", audience="Empresas e contribuintes"),
            _product("planejamento_tributario", "Planejamento tributário", "Diagnóstico preventivo e desenho de alternativas lícitas.", keywords=("planejamento tributario", "regime tributario", "economia fiscal"), documents=("balancetes", "contrato_social", "declaracoes_fiscais"), specialist="tributario_planejamento", closer="closer_tributario", audience="Empresas, sócios e investidores"),
            _product("itbi_restituicao", "Restituição de ITBI", "Restituição de ITBI pago a maior sobre valor venal de referência (Tema 796/STF).", keywords=("itbi", "restituicao de itbi", "itbi pago a maior", "valor venal de referencia"), documents=("guia_itbi_paga", "escritura_imovel", "matricula_imovel"), specialist="juridico_tributario", closer="closer_tributario", audience="Compradores de imóveis"),
        ],
        documents=("documento_identificacao", "procuracao_fiscal"),
        required_fields=("productKey", "taxpayerType", "taxAuthority", "taxType", "period", "estimatedValue"),
        audiences=("Pessoa física", "Empresa", "Contabilidade", "Investidor"),
        sdr="sdr_tributario",
        specialist="juridico_tributario",
        closer="closer_tributario",
    ),
    _area(
        "consumidor",
        "Consumidor",
        "Conflitos de consumo com produtos especializados por jornada e fornecedor.",
        products=[
            _product("transporte_aereo", "Voos e companhias aéreas", "Cancelamento, atraso, bagagem, reacomodação e assistência.", keywords=("voo", "aviao", "companhia aerea", "bagagem", "cancelamento"), documents=("bilhete_aereo", "cartao_embarque", "comprovantes_despesas"), specialist="consumidor_aereo", closer="closer_consumidor", audience="Passageiros"),
            _product("negativacao_indevida", "Negativação indevida", "Inscrição ou manutenção indevida em cadastro restritivo.", keywords=("serasa", "spc", "negativacao", "nome sujo"), documents=("consulta_restricao", "comprovante_inexistencia_divida", "protocolos"), specialist="consumidor_negativacao", closer="closer_consumidor", audience="Consumidores e empresas"),
            _product("cobranca_indevida", "Cobrança indevida", "Cobrança, débito ou renovação não contratada.", keywords=("cobranca indevida", "debito", "assinatura nao reconhecida"), documents=("fatura", "comprovantes_pagamento", "protocolos"), specialist="consumidor_cobrancas", closer="closer_consumidor", audience="Consumidores"),
            _product("produto_defeituoso", "Produto com vício ou defeito", "Troca, reparação e danos decorrentes de produto.", keywords=("produto defeituoso", "garantia", "troca", "vicio"), documents=("nota_fiscal", "fotos_videos", "ordens_servico"), specialist="consumidor_produtos", closer="closer_consumidor", audience="Consumidores"),
            _product("servico_nao_prestado", "Serviço não prestado ou inadequado", "Falha, atraso, cancelamento ou descumprimento de oferta.", keywords=("servico nao prestado", "descumprimento", "cancelamento"), documents=("contrato_consumo", "comprovantes_pagamento", "protocolos"), specialist="consumidor_servicos", closer="closer_consumidor", audience="Consumidores"),
            _product("bloqueio_conta_app", "Bloqueio de conta em aplicativo (entregador/motorista)", "Bloqueio, desativação ou suspensão de conta em aplicativos de entrega ou transporte (iFood, Uber, 99).", keywords=("ifood bloqueado", "uber bloqueado", "99 bloqueado", "conta de entregador", "desativacao de motorista"), documents=("prints_bloqueio", "identificacao_conta", "protocolos_aplicativo"), specialist="juridico_consumidor", closer="closer_consumidor", audience="Entregadores e motoristas de aplicativo"),
            _product("direito_transito", "Direito de trânsito (multas e CNH)", "Contestação de multas, pontuação, suspensão e cassação de CNH.", keywords=("multa transito", "recurso multa", "suspensao cnh", "cassacao cnh", "pontuacao carteira"), documents=("notificacao_infracao", "cnh", "auto_infracao"), specialist="juridico_consumidor", closer="closer_consumidor", audience="Condutores"),
        ],
        documents=("documento_identificacao", "comprovante_endereco"),
        required_fields=("productKey", "supplier", "purchaseDate", "estimatedLoss", "factsSummary"),
        audiences=("Consumidor pessoa física", "Consumidor por equiparação", "Microempresa elegível"),
        sdr="sdr_consumidor",
        specialist="juridico_consumidor",
        closer="closer_consumidor",
    ),
    _area(
        "digital",
        "Digital e plataformas",
        "Bloqueios, suspensões e conflitos em redes sociais, marketplaces e plataformas digitais.",
        products=[
            _product("bloqueio_instagram", "Instagram bloqueado", "Recuperação de conta, perfil ou ativo comercial no Instagram.", keywords=("instagram bloqueado", "conta instagram", "perfil suspenso"), documents=("prints_bloqueio", "identificacao_conta", "protocolos_meta"), specialist="digital_instagram", closer="closer_digital", audience="Criadores, profissionais e empresas"),
            _product("bloqueio_facebook", "Facebook bloqueado", "Conta, página, Business Manager ou anúncio bloqueado.", keywords=("facebook bloqueado", "business manager", "pagina suspensa"), documents=("prints_bloqueio", "identificacao_conta", "protocolos_meta"), specialist="digital_facebook", closer="closer_digital", audience="Pessoas, criadores e empresas"),
            _product("bloqueio_mercado_livre", "Mercado Livre bloqueado", "Suspensão de conta, anúncios, saldo ou reputação no marketplace.", keywords=("mercado livre", "mercado pago", "conta suspensa", "saldo retido"), documents=("prints_plataforma", "dados_conta", "historico_vendas"), specialist="digital_mercado_livre", closer="closer_digital", audience="Vendedores e empresas"),
            _product("bloqueio_amazon", "Amazon bloqueada", "Suspensão de seller, catálogo, pagamentos ou estoque.", keywords=("amazon seller", "amazon bloqueada", "conta suspensa"), documents=("prints_plataforma", "dados_seller", "historico_vendas"), specialist="digital_amazon", closer="closer_digital", audience="Vendedores e empresas"),
            _product("plataforma_digital", "Outras plataformas digitais", "Bloqueio, desmonetização, remoção ou retenção em plataforma digital.", keywords=("conta bloqueada", "plataforma", "desmonetizacao", "saldo retido"), documents=("prints_plataforma", "termos_plataforma", "protocolos"), specialist="juridico_digital", closer="closer_digital", audience="Usuários, criadores e empresas"),
        ],
        documents=("documento_identificacao", "comprovacao_titularidade"),
        required_fields=("productKey", "platform", "accountIdentifier", "blockDate", "businessImpact", "protocols"),
        audiences=("Criador de conteúdo", "Infoprodutor", "Seller", "Empresa", "Usuário"),
        sdr="sdr_digital",
        specialist="juridico_digital",
        closer="closer_digital",
    ),
    _area(
        "propriedade_intelectual",
        "Propriedade intelectual",
        "Marcas, patentes, direitos autorais, licenciamento e proteção de ativos intangíveis.",
        products=[
            _product("registro_marca", "Registro de marca", "Busca, estratégia de classes, depósito e acompanhamento de marca.", keywords=("registrar marca", "inpi", "marca"), documents=("dados_titular", "elementos_marca", "lista_produtos_servicos"), specialist="pi_marcas", closer="closer_pi", audience="Empreendedores, empresas e criadores"),
            _product("oposicao_marca", "Oposição, nulidade e conflito de marcas", "Defesa administrativa e estratégia em conflito marcário.", keywords=("oposicao", "nulidade", "marca parecida", "indeferimento"), documents=("processo_inpi", "provas_uso", "elementos_marca"), specialist="pi_contencioso_marcas", closer="closer_pi", audience="Titulares e requerentes de marca"),
            _product("patentes", "Patentes e invenções", "Proteção, busca, depósito e estratégia de patente ou modelo de utilidade.", keywords=("patente", "invencao", "modelo de utilidade"), documents=("descricao_invento", "desenhos_tecnicos", "dados_inventores"), specialist="pi_patentes", closer="closer_pi", audience="Inventores, universidades e empresas"),
            _product("direito_autoral", "Direito autoral", "Titularidade, uso não autorizado, registro e reparação.", keywords=("direito autoral", "plagio", "obra", "conteudo copiado"), documents=("obra_original", "provas_autoria", "provas_infracao"), specialist="pi_direito_autoral", closer="closer_pi", audience="Autores, criadores e empresas"),
            _product("licenciamento_pi", "Licenciamento e contratos de PI", "Cessão, licença, franquia e exploração de ativos intangíveis.", keywords=("licenciamento", "cessao", "royalties", "franquia"), documents=("ativos_pi", "minuta_contrato", "condicoes_comerciais"), specialist="pi_contratos", closer="closer_pi", audience="Titulares, licenciados e investidores"),
        ],
        documents=("documento_identificacao", "contrato_social"),
        required_fields=("productKey", "assetName", "owner", "territory", "status", "commercialUse"),
        audiences=("Empreendedor", "Empresa", "Criador", "Inventor", "Investidor"),
        sdr="sdr_pi",
        specialist="juridico_pi",
        closer="closer_pi",
    ),
    _area(
        "seguros",
        "Negativas de seguros",
        "Regulação e negativa de sinistros em seguros pessoais, patrimoniais, empresariais e proteção veicular.",
        products=[
            _product("seguro_vida", "Seguro de vida", "Negativa, cobertura, beneficiários e indenização securitária.", keywords=("seguro de vida", "beneficiario", "sinistro", "falecimento"), documents=("apolice", "comunicacao_sinistro", "negativa_seguradora"), specialist="seguros_vida", closer="closer_seguros", audience="Segurados e beneficiários"),
            _product("seguro_empresarial", "Seguro empresarial e de loja", "Danos, lucros cessantes, roubo, incêndio e cobertura do estabelecimento.", keywords=("seguro loja", "seguro empresarial", "incendio", "roubo", "lucros cessantes"), documents=("apolice", "comunicacao_sinistro", "laudos_prejuizos"), specialist="seguros_empresarial", closer="closer_seguros", audience="Empresas e lojistas"),
            _product("seguro_residencial", "Seguro residencial", "Cobertura de imóvel e conteúdo por dano, furto, incêndio ou evento climático.", keywords=("seguro residencial", "casa", "enchente", "incendio", "furto"), documents=("apolice", "comunicacao_sinistro", "fotos_laudos"), specialist="seguros_residencial", closer="closer_seguros", audience="Proprietários e moradores"),
            _product("seguro_automotivo", "Seguro automotivo", "Negativa de indenização, perda total, reparo, roubo e terceiros.", keywords=("seguro auto", "seguro automotivo", "perda total", "batida", "roubo veiculo"), documents=("apolice", "boletim_ocorrencia", "laudo_sinistro"), specialist="seguros_automotivo", closer="closer_seguros", audience="Proprietários e condutores"),
            _product("protecao_veicular_associacao", "Associação e proteção veicular", "Recusa de benefício por associação ou programa de proteção veicular.", keywords=("protecao veicular", "associacao", "rateio", "beneficio negado"), documents=("regulamento_associacao", "termo_adesao", "negativa_associacao"), specialist="seguros_protecao_veicular", closer="closer_seguros", audience="Associados e proprietários de veículos"),
        ],
        documents=("documento_identificacao", "comprovantes_pagamento_premio"),
        required_fields=("productKey", "insurer", "policyNumber", "claimNumber", "lossDate", "denialReason", "estimatedLoss"),
        audiences=("Segurado", "Beneficiário", "Empresa", "Associado"),
        sdr="sdr_seguros",
        specialist="juridico_seguros",
        closer="closer_seguros",
    ),
    _area(
        "familia",
        "Família e sucessões",
        "Divórcio, guarda, alimentos, inventário e planejamento sucessório.",
        products=[
            _product("divorcio", "Divórcio", "Divórcio consensual ou litigioso e partilha.", keywords=("divorcio", "separacao", "partilha"), documents=("certidao_casamento", "documentos_bens", "dados_filhos"), specialist="familia_divorcio", closer="closer_familia", audience="Cônjuges"),
            _product("guarda_alimentos", "Guarda e alimentos", "Guarda, convivência, pensão e revisões.", keywords=("guarda", "pensao alimenticia", "visitas"), documents=("certidao_nascimento", "comprovantes_despesas", "comprovantes_renda"), specialist="familia_guarda", closer="closer_familia", audience="Pais e responsáveis"),
            _product("inventario", "Inventário e sucessões", "Inventário judicial ou extrajudicial e organização sucessória.", keywords=("inventario", "heranca", "falecimento"), documents=("certidao_obito", "documentos_herdeiros", "documentos_bens"), specialist="familia_sucessoes", closer="closer_familia", audience="Herdeiros e famílias"),
            _product("adocao", "Adoção", "Habilitação, processo de adoção e adoção intuitu personae.", keywords=("adocao", "habilitacao adocao", "adocao intuitu personae", "guarda para adocao"), documents=("documento_identificacao", "comprovante_renda", "laudo_psicossocial"), specialist="juridico_familia", closer="closer_familia", audience="Pretendentes à adoção e famílias"),
            _product("uniao_estavel", "União estável (em vida)", "Reconhecimento, formalização ou dissolução de união estável entre pessoas vivas.", keywords=("uniao estavel", "reconhecimento de uniao estavel", "contrato de convivencia", "dissolucao de uniao estavel"), documents=("documento_identificacao", "provas_convivencia", "comprovante_endereco"), specialist="juridico_familia", closer="closer_familia", audience="Companheiros"),
            _product("violencia_domestica", "Violência doméstica (medida protetiva)", "Medida protetiva de urgência e acompanhamento de vítima em situação de violência doméstica.", keywords=("medida protetiva", "lei maria da penha", "violencia domestica", "urgencia protetiva"), documents=("boletim_ocorrencia", "laudo_medico", "documento_identificacao"), specialist="juridico_familia", closer="closer_familia", audience="Vítimas de violência doméstica"),
        ],
        documents=("documento_identificacao", "comprovante_endereco"),
        required_fields=("productKey", "parties", "relationship", "children", "assets", "urgency"),
        audiences=("Famílias", "Cônjuges", "Pais e responsáveis", "Herdeiros"),
        sdr="sdr_familia",
        specialist="juridico_familia",
        closer="closer_familia",
    ),
    _area(
        "empresarial",
        "Empresarial",
        "Contratos, societário, prevenção e contencioso estratégico para empresas.",
        products=[
            _product("contratos_empresariais", "Contratos empresariais", "Elaboração, revisão e negociação de contratos.", keywords=("contrato", "fornecedor", "cliente", "parceria"), documents=("minuta_contrato", "condicoes_comerciais", "contrato_social"), specialist="empresarial_contratos", closer="closer_empresarial", audience="Empresas e gestores"),
            _product("societario", "Societário", "Acordos de sócios, reorganização e conflitos societários.", keywords=("socios", "acordo de socios", "quotas", "societario"), documents=("contrato_social", "acordo_socios", "demonstracoes_financeiras"), specialist="empresarial_societario", closer="closer_empresarial", audience="Sócios, empresas e investidores"),
            _product("contencioso_empresarial", "Contencioso empresarial", "Cobranças, inadimplemento e disputas estratégicas.", keywords=("processo empresarial", "inadimplemento", "cobranca", "indenizacao"), documents=("contrato", "notificacoes", "provas_inadimplemento"), specialist="empresarial_contencioso", closer="closer_empresarial", audience="Empresas"),
        ],
        documents=("contrato_social", "documentos_representante"),
        required_fields=("productKey", "company", "counterparty", "estimatedValue", "factsSummary"),
        audiences=("Empresa", "Sócio", "Gestor", "Investidor"),
        sdr="sdr_empresarial",
        specialist="juridico_empresarial",
        closer="closer_empresarial",
    ),
    _area(
        "imobiliario",
        "Imobiliário",
        "Contratos, locação, regularização, condomínio e conflitos imobiliários.",
        products=[
            _product("locacao", "Locação", "Contrato, despejo, cobrança e revisão locatícia.", keywords=("aluguel", "locacao", "despejo", "fiador"), documents=("contrato_locacao", "comprovantes_pagamento", "notificacoes"), specialist="imobiliario_locacao", closer="closer_imobiliario", audience="Locadores e locatários"),
            _product("compra_venda", "Compra e venda de imóvel", "Análise contratual, atraso, vícios e distrato.", keywords=("compra de imovel", "distrato", "construtora", "atraso obra"), documents=("contrato_compra_venda", "matricula_imovel", "comprovantes_pagamento"), specialist="imobiliario_transacoes", closer="closer_imobiliario", audience="Compradores, vendedores e investidores"),
            _product("regularizacao", "Regularização imobiliária", "Usucapião, registro, retificação e regularização documental.", keywords=("usucapiao", "regularizacao", "matricula", "registro imovel"), documents=("matricula_imovel", "planta_memorial", "provas_posse"), specialist="imobiliario_regularizacao", closer="closer_imobiliario", audience="Proprietários e possuidores"),
        ],
        documents=("documento_identificacao", "comprovante_endereco"),
        required_fields=("productKey", "propertyAddress", "propertyRegistry", "parties", "estimatedValue"),
        audiences=("Proprietário", "Possuidor", "Locador", "Locatário", "Investidor"),
        sdr="sdr_imobiliario",
        specialist="juridico_imobiliario",
        closer="closer_imobiliario",
    ),
    _area(
        "criminal",
        "Criminal",
        "Flagrante, liberdade, habeas corpus, defesa criminal e execução penal.",
        products=[
            _product("flagrante_custodia", "Flagrante e custódia", "Atuação imediata em prisão em flagrante, audiência de custódia e liberdade provisória.", keywords=("flagrante", "prisao em flagrante", "audiencia de custodia", "custodia"), documents=("apf", "boletim_ocorrencia", "termo_flagrante"), specialist="criminal_flagrante", closer="closer_criminal", audience="Investigados e famílias"),
            _product("habeas_corpus", "Habeas corpus", "Impetração de habeas corpus contra prisão ilegal, constrangimento ou excesso de prazo.", keywords=("habeas corpus", "hc", "prisao ilegal", "constrangimento ilegal"), documents=("decisao_judicial", "auto_prisao", "certidao_processual"), specialist="criminal_habeas_corpus", closer="closer_criminal", audience="Investigados, réus e família"),
            _product("defesa_criminal", "Defesa criminal", "Resposta à acusação, instrução processual e defesa técnica ao longo do processo penal.", keywords=("resposta a acusacao", "denuncia", "instrucao criminal", "defesa criminal"), documents=("denuncia", "inquerito_policial", "provas_processuais"), specialist="juridico_criminal", closer="closer_criminal", audience="Réus"),
            _product("execucao_penal", "Execução penal", "Progressão de regime, livramento condicional e incidentes da execução da pena.", keywords=("execucao penal", "progressao de regime", "livramento condicional", "regime prisional"), documents=("guia_execucao", "atestado_conduta_carcerario", "calculo_penal"), specialist="criminal_execucao_penal", closer="closer_criminal", audience="Sentenciados e famílias"),
        ],
        documents=("documento_identificacao", "procuracao"),
        required_fields=("productKey", "defendantName", "caseNumber", "custodyStatus", "courtVenue", "factsSummary"),
        audiences=("Investigado", "Réu", "Sentenciado", "Família"),
        sdr="sdr_criminal",
        specialist="juridico_criminal",
        closer="closer_criminal",
    ),
    _area(
        "sucessoes",
        "Sucessões",
        "Inventário, partilha, testamento, planejamento sucessório e reconhecimento post mortem.",
        products=[
            _product("inventario_sucessorio", "Inventário", "Inventário extrajudicial em cartório ou judicial, com levantamento de bens e herdeiros.", keywords=("inventario", "inventario extrajudicial", "inventario judicial", "espolio", "heranca"), documents=("certidao_obito", "documentos_herdeiros", "documentos_bens"), specialist="sucessoes_inventario", closer="closer_sucessoes", audience="Herdeiros e famílias"),
            _product("partilha_sobrepartilha", "Partilha e sobrepartilha", "Partilha de bens do espólio e sobrepartilha de bens sonegados ou não incluídos.", keywords=("partilha", "sobrepartilha", "bem sonegado", "divisao de heranca"), documents=("formal_partilha", "documentos_bens", "documentos_herdeiros"), specialist="sucessoes_partilha", closer="closer_sucessoes", audience="Herdeiros"),
            _product("testamento_planejamento", "Testamento e planejamento sucessório", "Elaboração de testamento, doações e estruturação preventiva da sucessão.", keywords=("testamento", "planejamento sucessorio", "doacao em vida", "holding familiar"), documents=("minuta_testamento", "documentos_bens", "documentos_familiares"), specialist="juridico_sucessoes", closer="closer_sucessoes", audience="Testador e família"),
            _product("reconhecimento_post_mortem", "Reconhecimento post mortem", "Reconhecimento de união estável ou parentesco socioafetivo após o falecimento para fins sucessórios.", keywords=("uniao estavel post mortem", "socioafetivo", "reconhecimento post mortem", "parentesco socioafetivo"), documents=("provas_convivencia", "provas_socioafetivas", "certidao_obito"), specialist="sucessoes_reconhecimento_post_mortem", closer="closer_sucessoes", audience="Companheiro sobrevivente e filhos socioafetivos"),
        ],
        documents=("documento_identificacao", "comprovante_endereco"),
        required_fields=("productKey", "deceasedName", "heirs", "estateAssets", "willExists", "processStage"),
        audiences=("Herdeiros", "Cônjuge ou companheiro sobrevivente", "Testador", "Família"),
        sdr="sdr_sucessoes",
        specialist="juridico_sucessoes",
        closer="closer_sucessoes",
    ),
    _area(
        "societario",
        "Societário",
        "Conflitos entre sócios, acordos de sócios, responsabilidade de administradores e operações societárias.",
        products=[
            _product("conflito_socios", "Conflito entre sócios", "Exclusão de sócio, dissolução parcial de sociedade e apuração de haveres.", keywords=("exclusao de socio", "dissolucao parcial", "apuracao de haveres", "conflito societario"), documents=("contrato_social", "ata_reuniao", "balanco_patrimonial"), specialist="societario_conflito_socios", closer="closer_societario", audience="Sócios e empresas"),
            _product("acordo_socios", "Acordo de sócios", "Elaboração, revisão e execução de acordo de sócios e regras de governança.", keywords=("acordo de socios", "shareholders agreement", "governanca societaria", "quotas"), documents=("contrato_social", "acordo_socios", "minuta_acordo"), specialist="societario_acordo_socios", closer="closer_societario", audience="Sócios e investidores"),
            _product("responsabilidade_administrador", "Responsabilidade do administrador", "Apuração de responsabilidade civil do administrador, desconsideração da personalidade jurídica e deveres fiduciários.", keywords=("responsabilidade do administrador", "desconsideracao da personalidade juridica", "dever fiduciario", "gestao temeraria"), documents=("contrato_social", "ata_reuniao", "demonstracoes_financeiras"), specialist="societario_responsabilidade_administrador", closer="closer_societario", audience="Sócios, administradores e empresas"),
            _product("operacoes_societarias", "Operações societárias (M&A)", "Fusões, aquisições, reorganizações societárias e due diligence.", keywords=("fusao", "aquisicao", "m&a", "due diligence", "reorganizacao societaria"), documents=("contrato_social", "due_diligence_report", "minuta_contrato"), specialist="juridico_societario", closer="closer_societario", audience="Empresas, sócios e investidores"),
        ],
        documents=("contrato_social", "documentos_representante"),
        required_fields=("productKey", "company", "parties", "equityStake", "estimatedValue", "factsSummary"),
        audiences=("Sócio", "Administrador", "Empresa", "Investidor"),
        sdr="sdr_societario",
        specialist="juridico_societario",
        closer="closer_societario",
    ),
    _area(
        "saude_publica_sus",
        "Saúde pública (SUS)",
        "Medicamento, tratamento, cirurgia e insumos de home care pela rede pública de saúde.",
        products=[
            _product("medicamento_sus", "Medicamento pelo SUS", "Fornecimento de medicamento não padronizado ou em falta pela rede pública de saúde.", keywords=("medicamento sus", "farmacia de alto custo", "medicamento nao padronizado", "fornecimento de medicamento"), documents=("prescricao_medica", "relatorio_medico", "cartao_sus"), specialist="saude_publica_sus_medicamento", closer="closer_saude_publica_sus", audience="Pacientes e responsáveis"),
            _product("tratamento_cirurgia_sus", "Tratamento e cirurgia pelo SUS", "Acesso a tratamento, cirurgia ou procedimento negado ou represado na rede pública.", keywords=("cirurgia sus", "tratamento sus", "fila de espera", "procedimento sus"), documents=("prescricao_medica", "relatorio_medico", "protocolo_atendimento"), specialist="juridico_saude_publica_sus", closer="closer_saude_publica_sus", audience="Pacientes e responsáveis"),
            _product("insumos_home_care_sus", "Insumos e home care pelo SUS", "Fornecimento de insumos, fraldas, sondas, oxigênio e assistência domiciliar pela rede pública.", keywords=("home care sus", "insumos sus", "fraldas", "sonda", "oxigenio domiciliar"), documents=("prescricao_medica", "relatorio_medico", "laudo_assistencia_domiciliar"), specialist="saude_publica_sus_home_care", closer="closer_saude_publica_sus", audience="Pacientes e famílias"),
        ],
        documents=("documento_identificacao", "cartao_sus"),
        required_fields=("productKey", "publicEntity", "healthCondition", "treatmentUrgency", "administrativeRequestStatus"),
        audiences=("Paciente", "Responsável legal", "Família"),
        sdr="sdr_saude_publica_sus",
        specialist="juridico_saude_publica_sus",
        closer="closer_saude_publica_sus",
    ),
)


AREA_BY_KEY = {area["areaKey"]: area for area in LEGAL_AREA_CATALOG}
LEGACY_AREA_KEY_MAP = {
    legacy: area["areaKey"]
    for area in LEGAL_AREA_CATALOG
    for legacy in area.get("legacyKeys", [])
}
PRODUCT_BY_KEY = {
    (area["areaKey"], product["key"]): product
    for area in LEGAL_AREA_CATALOG
    for product in area["products"]
}
SPECIALIZED_AGENT_BY_KEY = {
    agent["key"]: {**agent, "areaKey": area["areaKey"]}
    for area in LEGAL_AREA_CATALOG
    for agent in area["subagents"]
}


def canonical_area_key(value: str | None) -> str | None:
    if not value:
        return None
    return LEGACY_AREA_KEY_MAP.get(value, value)


def _search_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(character for character in decomposed if not unicodedata.combining(character))
        .replace("_", " ")
        .split()
    )


def infer_area(text: str | None) -> dict[str, Any] | None:
    """Infer an area deterministically without giving the model routing authority."""
    if not text:
        return None
    haystack = _search_text(text)
    matches: list[tuple[int, int, str, dict[str, Any]]] = []
    for area in LEGAL_AREA_CATALOG:
        score = 0
        strongest_match = 0
        area_terms = [area["areaKey"], area["name"], *area.get("legacyKeys", [])]
        for term in area_terms:
            normalized = _search_text(str(term))
            if normalized and normalized in haystack:
                weight = max(3, len(normalized.split()) + 2)
                score += weight
                strongest_match = max(strongest_match, len(normalized))
        for product in area["products"]:
            terms = [product["label"], *product["keywords"]]
            for term in terms:
                normalized = _search_text(str(term))
                if normalized and normalized in haystack:
                    weight = max(2, len(normalized.split()) + 1)
                    score += weight
                    strongest_match = max(strongest_match, len(normalized))
        if score:
            matches.append((score, strongest_match, area["areaKey"], area))
    if not matches:
        return None
    matches.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return deepcopy(matches[0][3])


def catalog_area(area_key: str) -> dict[str, Any] | None:
    area = AREA_BY_KEY.get(canonical_area_key(area_key) or "")
    return deepcopy(area) if area else None


def catalog_blueprint(area_key: str) -> dict[str, Any] | None:
    area = catalog_area(area_key)
    if not area:
        return None
    return {
        key: deepcopy(value)
        for key, value in area.items()
        if key not in {"areaKey", "name", "description", "defaultActive", "legacyKeys"}
    }


def route_for(area_key: str | None, product_key: str | None = None) -> dict[str, Any]:
    canonical = canonical_area_key(area_key) or "saude_suplementar"
    area = AREA_BY_KEY.get(canonical)
    if area is None:
        return {
            "areaKey": canonical,
            "areaLabel": canonical.replace("_", " ").title(),
            "productKey": product_key,
            "productLabel": None,
            "coordinationAgentKey": "orquestrador",
            "sdrAgentKey": "sdr",
            "specialistAgentKey": "juridico",
            "closerAgentKey": "closer",
            "manualOverrideWins": True,
        }
    product = PRODUCT_BY_KEY.get((area["areaKey"], product_key or ""))
    routing = area["routing"]
    return {
        "areaKey": area["areaKey"],
        "areaLabel": area["name"],
        "productKey": product["key"] if product else None,
        "productLabel": product["label"] if product else None,
        "coordinationAgentKey": routing["coordinationAgentKey"],
        "sdrAgentKey": routing["sdrAgentKey"],
        "specialistAgentKey": product["specialistAgentKey"] if product else routing["specialistAgentKey"],
        "closerAgentKey": product["closerAgentKey"] if product else routing["closerAgentKey"],
        "manualOverrideWins": True,
    }


def infer_product(area_key: str | None, text: str | None) -> dict[str, Any] | None:
    canonical = canonical_area_key(area_key)
    if not canonical or canonical not in AREA_BY_KEY or not text:
        return None
    haystack = _search_text(text)
    matches: list[tuple[int, dict[str, Any]]] = []
    for product in AREA_BY_KEY[canonical]["products"]:
        score = sum(
            1
            for keyword in product["keywords"]
            if _search_text(keyword) in haystack
        )
        if score:
            matches.append((score, product))
    if not matches:
        return None
    matches.sort(key=lambda item: (-item[0], item[1]["label"]))
    return deepcopy(matches[0][1])
