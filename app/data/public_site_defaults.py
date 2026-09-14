"""
Conteúdo inicial do site público do Meu Executivo.
"""
from __future__ import annotations

from copy import deepcopy


DEFAULT_SITE_SETTINGS = {
    "brandName": "Meu Executivo",
    "tagline": "A plataforma que transforma atendimento jurídico em fechamento organizado.",
    "supportEmail": "contato@klmadvogados.com",
    "contactEmail": "contato@klmadvogados.com",
    "whatsappUrl": "https://wa.me/5561993328108",
    "whatsappMessage": "Olá! Quero entender como o Meu Executivo organiza o atendimento e a operação do escritório.",
    "primaryCtaLabel": "Agendar demonstração",
    "secondaryCtaLabel": "Ver como funciona",
    "secondaryCtaSectionId": "como-funciona",
    "floatingWhatsappLabel": "Falar no WhatsApp",
    "seo": {
        "title": "Meu Executivo | Atendimento jurídico, CRM e operação no mesmo fluxo",
        "description": "WhatsApp, IA humanizada, documentos e CRM no mesmo fluxo até o contrato.",
        "canonicalUrl": "https://klmadvogados.com",
        "ogImageUrl": "https://klmadvogados.com/logo.png",
    },
}


DEFAULT_MEDIA_LIBRARY = {
    "items": [
        {
            "key": "hero-demo",
            "label": "Hero demo",
            "posterUrl": "/site-media/hero-system.png",
            "mp4Url": "",
            "webmUrl": "",
            "gifUrl": "/site-media/hero-demo-real.gif",
            "alt": "Demonstração do atendimento jurídico no WhatsApp com CRM vivo",
            "caption": "Hero principal da landing",
            "layoutVariant": "hero",
        },
        {
            "key": "crm-demo",
            "label": "CRM vivo",
            "posterUrl": "/site-media/crm-system.png",
            "mp4Url": "",
            "webmUrl": "",
            "gifUrl": "/site-media/crm-demo-real.gif",
            "alt": "Demonstração do card do lead com histórico, documentos e próximos passos",
            "caption": "Card vivo do lead",
            "layoutVariant": "crm",
        },
        {
            "key": "ia-demo",
            "label": "IA e follow-up",
            "posterUrl": "/site-media/ia-system.png",
            "mp4Url": "",
            "webmUrl": "",
            "gifUrl": "/site-media/ia-demo-real.gif",
            "alt": "Demonstração da IA humanizada conduzindo a triagem inicial",
            "caption": "Fluxo de atendimento com IA",
            "layoutVariant": "chat",
        },
        {
            "key": "kpi-demo",
            "label": "KPIs e gestão",
            "posterUrl": "/site-media/kpi-system.png",
            "mp4Url": "",
            "webmUrl": "",
            "gifUrl": "/site-media/kpi-demo-real.gif",
            "alt": "Demonstração dos dashboards e indicadores da plataforma",
            "caption": "Painel de gestão e KPIs",
            "layoutVariant": "kpi",
        },
    ],
}


DEFAULT_HOME = {
    "sections": [
        {
            "key": "hero",
            "navLabel": "Início",
            "title": "Transforme conversas no WhatsApp em contratos fechados.",
            "subtitle": "O Meu Executivo atende com IA assistida, coleta documentos, alimenta o CRM e organiza o fluxo até o agendamento ou fechamento, sempre com controle humano.",
            "body": "Feita para escritórios que querem responder mais rápido, perder menos leads, organizar o histórico no CRM e fechar com mais clareza.",
            "bullets": [
                "WhatsApp, IA humanizada, documentos e CRM no mesmo fluxo",
                "Equipe humana no controle",
                "Fluxo até o contrato com mais organização",
            ],
            "items": [],
            "cta": {
                "primaryLabel": "Agendar demonstração",
                "secondaryLabel": "Ver como funciona",
                "secondaryTarget": "como-funciona",
            },
            "isVisible": True,
            "order": 1,
            "media": {
                "posterUrl": "/site-media/hero-system.png",
                "mp4Url": "",
                "webmUrl": "",
                "gifUrl": "/site-media/hero-demo-real.gif",
                "alt": "Demonstração da plataforma atendendo um lead via WhatsApp",
                "caption": "Atendimento, CRM e contexto no mesmo fluxo",
                "layoutVariant": "hero",
            },
        },
        {
            "key": "dor",
            "navLabel": "Dor",
            "title": "Seu escritório não perde lead só por falta de demanda. Perde por falta de fluxo.",
            "subtitle": "A mensagem chega. A resposta atrasa. Os documentos vêm incompletos. O histórico fica espalhado. O time esquece follow-up. O CRM depende de preenchimento manual. O contrato demora.",
            "body": "O Meu Executivo organiza esse caminho inteiro, do primeiro contato ao contrato fechado.",
            "bullets": [
                "menos resposta atrasada",
                "menos histórico espalhado",
                "menos CRM manual",
                "menos retrabalho até o contrato",
            ],
            "items": [],
            "cta": None,
            "isVisible": True,
            "order": 2,
            "media": None,
        },
        {
            "key": "proposta-valor",
            "navLabel": "Recursos",
            "title": "Tudo o que precisa acontecer antes da contratação, em um só sistema.",
            "subtitle": "Uma plataforma de atendimento, intake e fechamento para advocacia.",
            "body": "",
            "bullets": [],
            "items": [
                {
                    "title": "Atendimento por WhatsApp com IA humanizada",
                    "description": "A IA responde, conduz a triagem inicial e mantém a conversa fluindo sem parecer robótica. Quando faz sentido, o atendimento passa para o humano com contexto.",
                    "badge": "IA humanizada",
                },
                {
                    "title": "Coleta inteligente de documentos",
                    "description": "A plataforma solicita os documentos certos, registra o que já foi enviado, aponta pendências e mantém tudo organizado no card do lead.",
                    "badge": "Checklist vivo",
                },
                {
                    "title": "CRM jurídico que se alimenta sozinho",
                    "description": "Cada mensagem, anexo, observação, agendamento e mudança de estágio fica salva automaticamente no CRM.",
                    "badge": "CRM vivo",
                },
                {
                    "title": "Agendamento com contexto",
                    "description": "Quando o lead estiver pronto para avançar, o sistema ajuda a levar a conversa para agendamento, análise humana ou fechamento.",
                    "badge": "Contexto completo",
                },
                {
                    "title": "Contrato com menos atrito",
                    "description": "Quando o momento chega, a equipe não começa do zero. O histórico já está registrado, os documentos já foram coletados e o fechamento anda com muito mais fluidez.",
                    "badge": "Mais fluidez",
                },
            ],
            "cta": None,
            "isVisible": True,
            "order": 3,
            "media": {
                "posterUrl": "/site-media/ia-system.png",
                "mp4Url": "",
                "webmUrl": "",
                "gifUrl": "/site-media/flow-demo-real.gif",
                "alt": "Demonstração dos principais blocos operacionais da plataforma",
                "caption": "Fluxo operacional em movimento",
                "layoutVariant": "features",
            },
        },
        {
            "key": "diferenciacao",
            "navLabel": "Diferencial",
            "title": "Não é só inbox. Não é só CRM. Não é só chatbot.",
            "subtitle": "O Meu Executivo foi pensado para a advocacia.",
            "body": "Ela não tenta automatizar a atuação jurídica final. Ela resolve o caos que acontece antes: atendimento inicial, qualificação, coleta documental, registro automático no CRM, histórico completo, insights para o time, agendamento e avanço até o contrato.",
            "bullets": [
                "atendimento inicial",
                "qualificação",
                "coleta documental",
                "registro automático no CRM",
                "agendamento e avanço até o contrato",
            ],
            "items": [],
            "cta": None,
            "isVisible": True,
            "order": 4,
            "media": None,
        },
        {
            "key": "como-funciona",
            "navLabel": "Como funciona",
            "title": "Como funciona na prática",
            "subtitle": "",
            "body": "",
            "bullets": [],
            "items": [
                {
                    "title": "1. O lead chama no WhatsApp",
                    "description": "A conversa começa no canal que o cliente já usa no dia a dia.",
                },
                {
                    "title": "2. A IA conduz a triagem",
                    "description": "Ela faz perguntas iniciais, identifica o assunto e mantém o fluxo de atendimento ativo.",
                },
                {
                    "title": "3. Os documentos começam a ser coletados",
                    "description": "A plataforma pede o que for necessário e organiza o que já chegou.",
                },
                {
                    "title": "4. O CRM é atualizado automaticamente",
                    "description": "O card do lead recebe histórico, anexos, observações, estágio, pendências e insights.",
                },
                {
                    "title": "5. O humano assume com muito mais contexto",
                    "description": "Sua equipe entra no momento certo, com muito menos retrabalho e muito mais clareza.",
                },
                {
                    "title": "6. O fechamento acontece com mais organização",
                    "description": "Agendamento, proposta, contrato e próximos passos seguem em um fluxo mais limpo e previsível.",
                },
            ],
            "cta": None,
            "isVisible": True,
            "order": 5,
            "media": {
                "posterUrl": "/site-media/crm-system.png",
                "mp4Url": "",
                "webmUrl": "",
                "gifUrl": "/site-media/flow-demo-real.gif",
                "alt": "Demonstração do fluxo desde o WhatsApp até o contrato",
                "caption": "Fluxo do primeiro oi ao fechamento",
                "layoutVariant": "timeline",
            },
        },
        {
            "key": "crm-vivo",
            "navLabel": "CRM vivo",
            "title": "O card do lead vira a fonte da verdade.",
            "subtitle": "No Meu Executivo, o CRM é alimentado conforme o atendimento avança.",
            "body": "Ele é atualizado em tempo real conforme a conversa evolui. No card do lead, sua equipe enxerga histórico completo do atendimento, documentos enviados e pendentes, observações internas, insights da IA, estágio do funil, agendamentos, próximos passos e andamento até o contrato.",
            "bullets": [
                "histórico completo do atendimento",
                "documentos enviados e pendentes",
                "insights da IA",
                "estágio do funil e próximos passos",
            ],
            "items": [],
            "cta": None,
            "isVisible": True,
            "order": 6,
            "media": {
                "posterUrl": "/site-media/crm-system.png",
                "mp4Url": "",
                "webmUrl": "",
                "gifUrl": "/site-media/crm-demo-real.gif",
                "alt": "Card vivo do lead com histórico, documentos e próximos passos",
                "caption": "O card se alimenta sozinho",
                "layoutVariant": "crm",
            },
        },
        {
            "key": "ia",
            "navLabel": "IA",
            "title": "IA para acelerar o time. Não para tirar o controle do advogado.",
            "subtitle": "O controle continua com a equipe humana. A tecnologia entra para reduzir atrito, não para substituir estratégia.",
            "body": "A IA ajuda sua operação a responder mais rápido, resumir conversas, registrar contexto, sugerir próximos passos, apontar pendências, sinalizar riscos de perda e manter o CRM vivo.",
            "bullets": [
                "responder mais rápido",
                "resumir conversas",
                "sugerir próximos passos",
                "sinalizar riscos de perda",
            ],
            "items": [],
            "cta": None,
            "isVisible": True,
            "order": 7,
            "media": {
                "posterUrl": "/site-media/ia-system.png",
                "mp4Url": "",
                "webmUrl": "",
                "gifUrl": "/site-media/ia-demo-real.gif",
                "alt": "Demonstração da IA apoiando o atendimento jurídico",
                "caption": "IA no apoio à operação",
                "layoutVariant": "chat",
            },
        },
        {
            "key": "beneficios",
            "navLabel": "Benefícios",
            "title": "Menos improviso. Mais contexto. Mais fechamento.",
            "subtitle": "Com o Meu Executivo, o escritório ganha:",
            "body": "",
            "bullets": [
                "menos lead perdido por demora",
                "menos conversa solta no WhatsApp",
                "menos documento perdido",
                "menos retrabalho no CRM",
                "mais contexto para a equipe",
                "mais previsibilidade comercial",
                "mais velocidade até o contrato",
            ],
            "items": [],
            "cta": None,
            "isVisible": True,
            "order": 8,
            "media": None,
        },
        {
            "key": "kpis",
            "navLabel": "KPIs",
            "title": "Gestão real do funil, não só volume de mensagens.",
            "subtitle": "Acompanhe com clareza tempo de primeira resposta, leads por origem, taxa de qualificação, avanço por etapa, documentos pendentes, agendamentos, contratos e produtividade do time.",
            "body": "A plataforma ajuda o escritório a acompanhar com mais precisão também o potencial de honorários por lead, equipe ou campanha.",
            "bullets": [
                "tempo de primeira resposta",
                "taxa de qualificação",
                "taxa de avanço por etapa",
                "potencial de honorários por lead, equipe ou campanha",
            ],
            "items": [],
            "cta": None,
            "isVisible": True,
            "order": 9,
            "media": {
                "posterUrl": "/site-media/kpi-system.png",
                "mp4Url": "",
                "webmUrl": "",
                "gifUrl": "/site-media/kpi-demo-real.gif",
                "alt": "Painel de indicadores e relatórios da operação jurídica",
                "caption": "KPIs e gestão real do funil",
                "layoutVariant": "kpi",
            },
        },
        {
            "key": "tracking",
            "navLabel": "Tracking",
            "title": "Saiba de onde vêm os leads que realmente fecham.",
            "subtitle": "O Meu Executivo acompanha a origem das oportunidades e mostra quais campanhas geram os melhores leads, quais fontes convertem e onde está o maior potencial comercial.",
            "body": "",
            "bullets": [],
            "items": [],
            "cta": None,
            "isVisible": True,
            "order": 10,
            "media": None,
        },
        {
            "key": "publico",
            "navLabel": "Para quem",
            "title": "Feita para escritórios que querem crescer sem virar reféns do improviso.",
            "subtitle": "Para escritórios que recebem leads pelo WhatsApp, têm equipe comercial, intake ou atendimento, precisam responder rápido sem perder contexto, querem organizar documentos antes do fechamento e desejam transformar atendimento em processo comercial de verdade.",
            "body": "",
            "bullets": [],
            "items": [],
            "cta": None,
            "isVisible": True,
            "order": 11,
            "media": None,
        },
        {
            "key": "prova-social",
            "navLabel": "Resultados",
            "title": "Escritórios que crescem com mais fluxo, menos caos e mais controle.",
            "subtitle": "",
            "body": "",
            "bullets": [],
            "items": [
                {
                    "title": "Nome do cliente — Cargo / Escritório",
                    "description": "Antes, nosso histórico ficava espalhado e cada atendimento dependia muito da pessoa que pegava o lead. Hoje, tudo fica registrado e o fechamento andou muito melhor.",
                },
                {
                    "title": "Nome do cliente — Cargo / Escritório",
                    "description": "Conseguimos responder mais rápido, pedir documentos com mais padrão e parar de perder oportunidade por falha de acompanhamento.",
                },
            ],
            "cta": None,
            "isVisible": True,
            "order": 12,
            "media": None,
        },
        {
            "key": "pricing",
            "navLabel": "Planos",
            "title": "Planos para escritórios em diferentes estágios de operação.",
            "subtitle": "Da operação enxuta ao time com múltiplos atendentes, o Meu Executivo mantém estrutura, contexto e controle.",
            "body": "",
            "bullets": [],
            "items": [],
            "cta": {
                "primaryLabel": "Falar com o time",
                "secondaryLabel": "",
                "secondaryTarget": "",
            },
            "isVisible": True,
            "order": 13,
            "media": None,
        },
        {
            "key": "cta-final",
            "navLabel": "Contato",
            "title": "Se o seu lead chega pelo WhatsApp, o fechamento não deveria depender de improviso.",
            "subtitle": "Organize atendimento, documentos, CRM, agendamento e contrato em um único fluxo.",
            "body": "",
            "bullets": [],
            "items": [],
            "cta": {
                "primaryLabel": "Agendar demonstração",
                "secondaryLabel": "Falar com o time",
                "secondaryTarget": "",
            },
            "isVisible": True,
            "order": 14,
            "media": None,
        },
    ],
    "seo": {
        "title": "Meu Executivo | Atendimento jurídico, CRM e operação no mesmo fluxo",
        "description": "A plataforma que transforma atendimento jurídico em fechamento organizado.",
        "canonicalUrl": "https://klmadvogados.com",
        "ogImageUrl": "https://klmadvogados.com/logo.png",
    },
}


DEFAULT_FAQ = {
    "items": [
        {
            "question": "O Meu Executivo substitui o advogado?",
            "answer": "Não. A plataforma ajuda no atendimento, intake, organização do CRM, coleta documental, agendamento e avanço até o contrato. O controle estratégico continua com a equipe humana.",
        },
        {
            "question": "Funciona só com WhatsApp?",
            "answer": "O foco principal é o WhatsApp, porque é onde muitos escritórios recebem e conduzem leads. A plataforma pode evoluir com outras conexões e integrações.",
        },
        {
            "question": "A IA responde sozinha?",
            "answer": "Ela pode ajudar no atendimento inicial, nas sugestões e na organização da operação. O escritório define como quer usar a automação e quando o humano entra.",
        },
        {
            "question": "O CRM precisa ser alimentado manualmente?",
            "answer": "Não como em CRMs tradicionais. O Meu Executivo registra o histórico conforme a conversa avança.",
        },
        {
            "question": "Dá para organizar documentos no card do lead?",
            "answer": "Sim. O objetivo é centralizar documentos, pendências, observações, histórico, estágios e próximos passos no mesmo lugar.",
        },
        {
            "question": "A plataforma vai até a execução jurídica do caso?",
            "answer": "Não. O foco é o fluxo até o fechamento: atendimento, qualificação, documentos, CRM, agendamento e contrato.",
        },
        {
            "question": "Dá para acompanhar a origem dos leads?",
            "answer": "Sim. A plataforma ajuda a organizar a origem das oportunidades e a leitura comercial do funil.",
        },
        {
            "question": "É indicada para escritório pequeno?",
            "answer": "Sim. A plataforma faz sentido tanto para operações enxutas quanto para times com mais atendentes e maior volume de leads.",
        },
    ]
}


DEFAULT_TERMS = {
    "title": "Termos de Uso",
    "updatedLabel": "abril de 2026",
    "seo": {
        "title": "Termos de Uso | Meu Executivo",
        "description": "Estrutura de termos de uso do Meu Executivo.",
        "canonicalUrl": "https://klmadvogados.com/termos",
        "ogImageUrl": "https://klmadvogados.com/logo.png",
    },
    "sections": [
        {"heading": "1. Apresentação da plataforma", "body": "O Meu Executivo é uma plataforma de atendimento, CRM e operação jurídica."},
        {"heading": "2. Definições", "body": "Este documento define termos básicos de uso da plataforma, workspace, usuário, integrações e fluxos automatizados."},
        {"heading": "3. Cadastro e acesso", "body": "O cliente é responsável pelo cadastro correto, pela guarda de credenciais e pelo uso adequado da conta."},
        {"heading": "4. Perfis de usuário e responsabilidades", "body": "O workspace owner e demais usuários devem respeitar perfis, limites do plano e responsabilidades internas."},
        {"heading": "5. Escopo do serviço", "body": "O foco da plataforma é o fluxo até o fechamento: atendimento, qualificação, documentos, CRM, agendamento e contrato."},
        {"heading": "6. Limites de uso e disponibilidade", "body": "Os limites variam conforme o plano contratado, incluindo usuários, agentes, canais e recursos operacionais."},
        {"heading": "7. Uso adequado da plataforma", "body": "A plataforma deve ser utilizada em conformidade com a legislação aplicável e com as regras profissionais do cliente."},
        {"heading": "8. Responsabilidades do cliente", "body": "O cliente responde pela veracidade dos dados inseridos, pela revisão humana necessária e pelo uso de integrações e automações."},
        {"heading": "9. Integrações de terceiros", "body": "Integrações com terceiros dependem da disponibilidade e das regras de cada provedor externo."},
        {"heading": "10. Propriedade intelectual", "body": "O software, a identidade visual e os materiais da plataforma são protegidos por direitos de propriedade intelectual."},
        {"heading": "11. Tratamento de dados", "body": "O tratamento de dados ocorre conforme a política de privacidade e a LGPD."},
        {"heading": "12. Suspensão e encerramento de conta", "body": "A plataforma pode suspender ou encerrar contas em caso de inadimplência, uso indevido ou violação contratual."},
        {"heading": "13. Limitação de responsabilidade", "body": "A plataforma apoia a operação, mas não substitui a análise profissional nem a revisão humana estratégica."},
        {"heading": "14. Alterações nos termos", "body": "Os termos podem ser atualizados periodicamente, com data de revisão visível ao usuário."},
        {"heading": "15. Legislação aplicável e foro", "body": "Aplica-se a legislação brasileira, com foro definido em contrato aplicável."},
    ],
}


DEFAULT_PRIVACY = {
    "title": "Política de Privacidade",
    "updatedLabel": "abril de 2026",
    "seo": {
        "title": "Política de Privacidade | Meu Executivo",
        "description": "Política de privacidade do Meu Executivo.",
        "canonicalUrl": "https://klmadvogados.com/privacidade",
        "ogImageUrl": "https://klmadvogados.com/logo.png",
    },
    "sections": [
        {"heading": "1. Introdução", "body": "O Meu Executivo trata privacidade como parte estrutural da plataforma."},
        {"heading": "2. Quais dados coletamos", "body": "Coletamos dados de cadastro, uso, operação e integração necessários para prestar o serviço."},
        {"heading": "3. Como usamos os dados", "body": "Os dados são usados para operar a plataforma, melhorar o serviço, dar suporte e cumprir obrigações legais."},
        {"heading": "4. Bases legais aplicáveis", "body": "O tratamento segue as bases legais previstas na LGPD, conforme cada finalidade."},
        {"heading": "5. Compartilhamento com terceiros", "body": "Compartilhamentos ocorrem apenas com provedores essenciais e dentro de contratos adequados."},
        {"heading": "6. Armazenamento e retenção", "body": "Os dados são mantidos pelo período necessário à operação, segurança e cumprimento de obrigações legais."},
        {"heading": "7. Medidas de segurança", "body": "Adotamos controle de acesso, segregação de ambiente, rastreabilidade e boas práticas para reduzir exposição indevida."},
        {"heading": "8. Direitos do titular", "body": "Os titulares podem exercer os direitos previstos na LGPD pelos canais oficiais de contato."},
        {"heading": "9. Cookies e tecnologias semelhantes", "body": "Podemos utilizar recursos técnicos necessários à navegação, segurança e medição de uso."},
        {"heading": "10. Transferências internacionais, se houver", "body": "Quando houver provedores externos, adotaremos salvaguardas compatíveis com a legislação aplicável."},
        {"heading": "11. Atualizações desta política", "body": "A política pode ser atualizada periodicamente, com data de revisão visível."},
        {"heading": "12. Canal de contato para solicitações", "body": "Solicitações sobre privacidade podem ser encaminhadas para contato@klmadvogados.com."},
    ],
}


DEFAULT_PUBLIC_SITE_DOCUMENTS = {
    "settings": {
        "title": "Configurações do site",
        "description": "Metadados globais e CTAs do site público.",
        "content": DEFAULT_SITE_SETTINGS,
    },
    "home": {
        "title": "Home pública",
        "description": "Landing one-page do site institucional.",
        "content": DEFAULT_HOME,
    },
    "faq": {
        "title": "FAQ público",
        "description": "Perguntas frequentes da home.",
        "content": DEFAULT_FAQ,
    },
    "terms": {
        "title": "Termos de Uso",
        "description": "Documento legal publicado no site.",
        "content": DEFAULT_TERMS,
    },
    "privacy": {
        "title": "Política de Privacidade",
        "description": "Documento legal publicado no site.",
        "content": DEFAULT_PRIVACY,
    },
    "media": {
        "title": "Biblioteca de mídias",
        "description": "Assets públicos da landing.",
        "content": DEFAULT_MEDIA_LIBRARY,
    },
}


def get_default_public_site_documents() -> dict:
    return deepcopy(DEFAULT_PUBLIC_SITE_DOCUMENTS)
