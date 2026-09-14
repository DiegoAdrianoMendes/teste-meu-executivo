"""Biblioteca curada de templates Meta UTILITY do Meu Executivo.

O catálogo vive em código por desenho: o escritório acompanha e provisiona a
versão publicada, mas não edita a política padrão. Cada variável possui um
exemplo realista porque a Graph API exige ``example.body_text`` na submissão.

Não adicionar templates MARKETING aqui. O provisionamento automático existe
somente para comunicações de utilidade numa relação profissional já existente.
"""
from __future__ import annotations

from dataclasses import dataclass

CATALOG_VERSION = "2026.09.1"


@dataclass(frozen=True)
class UtilityTemplateVariable:
    key: str
    example: str


@dataclass(frozen=True)
class UtilityTemplateDefinition:
    key: str
    version: int
    body: str
    variables: tuple[UtilityTemplateVariable, ...]
    practice_areas: tuple[str, ...]
    category: str = "utility"
    language: str = "pt_BR"

    @property
    def meta_name(self) -> str:
        return f"meu_executivo_{self.key}_v{self.version}"

    @property
    def examples(self) -> list[str]:
        return [variable.example for variable in self.variables]


UTILITY_TEMPLATE_CATALOG: tuple[UtilityTemplateDefinition, ...] = (
    UtilityTemplateDefinition(
        key="confirmacao_consulta",
        version=1,
        body=(
            "Olá, {{1}}. Confirmamos sua consulta com {{2}} em {{3}}, às {{4}}, "
            "no endereço {{5}}. Se precisar remarcar, responda a esta mensagem."
        ),
        variables=(
            UtilityTemplateVariable("nome_cliente", "Maria Silva"),
            UtilityTemplateVariable("responsavel", "Dra. Ana Souza"),
            UtilityTemplateVariable("data", "18/08/2026"),
            UtilityTemplateVariable("hora", "14:30"),
            UtilityTemplateVariable("endereco", "Av. Central, 100, sala 12"),
        ),
        practice_areas=("todas",),
    ),
    UtilityTemplateDefinition(
        key="lembrete_audiencia",
        version=2,
        body=(
            "Olá, {{1}}. Lembramos que sua {{2}} está marcada para o dia {{3}}, às {{4}}, "
            "no endereço {{5}}. O acompanhamento fica a cargo de {{6}}, da nossa equipe. "
            "Se tiver alguma dúvida antes da data, responda a esta mensagem."
        ),
        variables=(
            UtilityTemplateVariable("nome_cliente", "João Pereira"),
            UtilityTemplateVariable("tipo_evento", "audiência"),
            UtilityTemplateVariable("data", "20/08/2026"),
            UtilityTemplateVariable("hora", "09:00"),
            UtilityTemplateVariable("local", "Fórum de Brasília, sala 3"),
            UtilityTemplateVariable("responsavel", "Dr. Carlos Lima"),
        ),
        practice_areas=("todas",),
    ),
    UtilityTemplateDefinition(
        key="solicitacao_documentos",
        version=2,
        body=(
            "Olá, {{1}}. Para dar continuidade ao seu atendimento, ainda precisamos "
            "destes documentos: {{2}}. Pedimos que faça o envio até o dia {{3}}, pelo canal "
            "{{4}}, para não atrasar a análise. Se já enviou ou tiver alguma dificuldade, "
            "responda a esta mensagem."
        ),
        variables=(
            UtilityTemplateVariable("nome_cliente", "Fernanda Alves"),
            UtilityTemplateVariable("documentos", "RG, comprovante de residência e contrato"),
            UtilityTemplateVariable("prazo", "22/08/2026"),
            UtilityTemplateVariable("canal_envio", "portal seguro do escritório"),
        ),
        practice_areas=("todas",),
    ),
    UtilityTemplateDefinition(
        key="atualizacao_processo",
        version=2,
        body=(
            "Olá, {{1}}. Houve uma atualização no processo {{2}}. Movimentação registrada: "
            "{{3}}. O próximo passo previsto pela equipe é {{4}}, e avisaremos assim que "
            "houver novidade. Se quiser conversar sobre essa etapa, responda a esta mensagem."
        ),
        variables=(
            UtilityTemplateVariable("nome_cliente", "Roberto Santos"),
            UtilityTemplateVariable("numero_processo", "0001234-56.2026.8.07.0001"),
            UtilityTemplateVariable("movimentacao", "a audiência foi designada"),
            UtilityTemplateVariable("proximo_passo", "preparação dos documentos para a audiência"),
        ),
        practice_areas=("todas",),
    ),
    UtilityTemplateDefinition(
        key="retomada_atendimento",
        version=1,
        body=(
            "Olá, {{1}}. Seu atendimento sobre {{2}} foi iniciado e está aguardando {{3}} "
            "para prosseguir. Se já providenciou, responda a esta mensagem."
        ),
        variables=(
            UtilityTemplateVariable("nome_cliente", "Patrícia Costa"),
            UtilityTemplateVariable("assunto", "revisão do contrato"),
            UtilityTemplateVariable("pendencia", "o envio do documento assinado"),
        ),
        practice_areas=("todas",),
    ),
    UtilityTemplateDefinition(
        key="confirmacao_dados",
        version=1,
        body=(
            "Olá, {{1}}. Antes de elaborar {{2}}, confirme se estes dados estão corretos: "
            "{{3}}. Responda com as correções necessárias."
        ),
        variables=(
            UtilityTemplateVariable("nome_cliente", "Marcos Oliveira"),
            UtilityTemplateVariable("documento", "a petição inicial"),
            UtilityTemplateVariable(
                "dados",
                "nome completo, CPF e endereço informados no cadastro",
            ),
        ),
        practice_areas=("todas",),
    ),
    UtilityTemplateDefinition(
        key="pesquisa_satisfacao",
        version=1,
        body=(
            "Olá, {{1}}. O atendimento {{2}} foi concluído em {{3}}. Para registrarmos a "
            "qualidade desse atendimento, responda com uma nota de 1 a 5."
        ),
        variables=(
            UtilityTemplateVariable("nome_cliente", "Luciana Rocha"),
            UtilityTemplateVariable("identificacao_atendimento", "Consulta 4821"),
            UtilityTemplateVariable("data_conclusao", "08/08/2026"),
        ),
        practice_areas=("todas",),
    ),
    UtilityTemplateDefinition(
        key="encerramento_processo",
        version=2,
        body=(
            "Olá, {{1}}. O processo {{2}} foi encerrado com o seguinte resultado: {{3}}. "
            "Os documentos finais estão disponíveis em {{4}} e ficam à sua disposição. "
            "Se tiver alguma dúvida sobre o encerramento, responda a esta mensagem."
        ),
        variables=(
            UtilityTemplateVariable("nome_cliente", "Eduardo Martins"),
            UtilityTemplateVariable("numero_processo", "0009876-54.2025.8.07.0001"),
            UtilityTemplateVariable("resultado", "acordo homologado e obrigação cumprida"),
            UtilityTemplateVariable("local_documentos", "portal seguro do escritório"),
        ),
        practice_areas=("todas",),
    ),
)

UTILITY_TEMPLATE_BY_KEY = {item.key: item for item in UTILITY_TEMPLATE_CATALOG}
