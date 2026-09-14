"""Schemas: BI jurídico + capacidade (Onda 8, Plano Mestre §15).

Ver app/services/legal_bi_service.py e app/services/legal_capacity_service.py
— todo campo aqui espelha 1:1 um dataclass do serviço, sem reformatar
número nem esconder metadado de honestidade.
"""

from __future__ import annotations

from datetime import datetime

from app.schemas.base import CamelCaseSchema


class BiHonestyRead(CamelCaseSchema):
    generated_at: datetime
    period_from: datetime
    period_to: datetime
    sample_size: int
    origin: str
    missing_fields: tuple[str, ...] = ()


class JourneyTimingStatRead(CamelCaseSchema):
    to_state: str
    sample_size: int
    avg_hours: float | None
    median_hours: float | None


class CountRowRead(CamelCaseSchema):
    key: str
    count: int


class AmountRowRead(CamelCaseSchema):
    key: str
    total_value: float
    count: int


class ClientSlaRowRead(CamelCaseSchema):
    client_id: str
    client_name: str
    no_prazo: int
    atrasado: int
    sem_sla_definido: int


class NatureDurationRowRead(CamelCaseSchema):
    nature: str
    sample_size: int
    avg_days: float


class OperationBiRead(CamelCaseSchema):
    honesty: BiHonestyRead
    tempo_por_marco: tuple[JourneyTimingStatRead, ...]
    tarefas_d0: int
    tarefas_d1: int
    tarefas_atrasadas: int
    tarefas_sem_data_classificavel: int
    tarefas_sem_responsavel: int
    retrabalho_correcoes_pendentes: int
    minutas_com_multiplas_versoes: int
    processos_sem_movimentacao: int
    backlog_por_responsavel: tuple[CountRowRead, ...]
    sla_por_cliente: tuple[ClientSlaRowRead, ...]
    excecoes_por_origem: tuple[CountRowRead, ...]


class LegalBiRead(CamelCaseSchema):
    honesty: BiHonestyRead
    estoque_por_fase: tuple[CountRowRead, ...]
    entradas_no_periodo: int
    saidas_no_periodo: int
    duracao_media_dias_por_natureza: tuple[NatureDurationRowRead, ...]
    valores_por_fase: tuple[AmountRowRead, ...]
    contingencia_por_risco: tuple[AmountRowRead, ...]
    acordos_no_periodo: int
    demandas_com_estrategia_registrada: int
    resultado_por_coluna_kanban: tuple[CountRowRead, ...]
    resultado_sem_matter_vinculado: int
    motivo_encerramento_registrado: int


class AiBiRead(CamelCaseSchema):
    honesty: BiHonestyRead
    passos_automatizados_por_agente: tuple[CountRowRead, ...]
    execucoes_totais: int
    execucoes_falhas: int
    execucoes_aguardando_humano: int
    intervencoes_humanas_total: int
    taxa_intervencao_humana: float | None
    decisoes_aceitas: int
    decisoes_corrigidas: int
    decisoes_rejeitadas: int
    custo_usd_por_agente: tuple[AmountRowRead, ...]
    tokens_input_totais: int
    tokens_output_totais: int


class CapacityHonestyRead(CamelCaseSchema):
    generated_at: datetime
    sample_size: int
    origin: str
    missing_fields: tuple[str, ...] = ()


class ResponsibleCapacityRead(CamelCaseSchema):
    responsible_user_id: str
    responsible_name: str
    carga_demandas_ativas: int
    carga_tarefas_abertas: int
    disponibilidade_configurada: bool
    horas_semana_configuradas: float | None
    disponivel_agora: bool
    motivo_disponibilidade: str


class GroupCapacityRead(CamelCaseSchema):
    key: str
    carga_demandas_ativas: int


class CapacityReportRead(CamelCaseSchema):
    honesty: CapacityHonestyRead
    por_responsavel: tuple[ResponsibleCapacityRead, ...]
    por_unidade: tuple[GroupCapacityRead, ...]
    por_equipe: tuple[GroupCapacityRead, ...]
