"""Versioned, source-bound tax-reform facts used for safe simulations.

This catalog is not a tax calculator and never selects a tax regime. It only
exposes dated official-source facts and human-review gates to the workspace.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

OFFICIAL_REFORM_SOURCE = (
    "https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/acoes-e-programas/"
    "programas-e-atividades/reforma-tributaria-do-consumo/entenda"
)
LC214_SOURCE = "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp214compilado.htm"
SIMPLES_IV_SOURCE = (
    "https://www.gov.br/receitafederal/pt-br/assuntos/orientacao-tributaria/"
    "cobrancas-e-intimacoes/contribuicao-previdenciaria-anexo-iv-do-simples-nacional"
)
TAX_REFORM_ALERT_OFFSETS = (180, 90, 30, 7)


def _timeline_item(
    *,
    year: int,
    phase: str,
    review_date: str,
    summary: str,
    source_url: str,
) -> dict[str, Any]:
    """Return a source-bound *review* milestone, not a tax filing deadline.

    The automated reminders ask the accountant to assess the transition before
    an official phase. They never claim that the supplied date is a taxpayer's
    individual filing deadline; that still depends on the workspace profile,
    municipality and accountant validation.
    """
    return {
        "year": year,
        "phase": phase,
        "reviewDate": review_date,
        "summary": summary,
        "sourceUrl": source_url,
    }


def tax_rule_catalog(*, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    timeline = [
        _timeline_item(
            year=2026,
            phase="test",
            review_date="2026-01-01",
            summary=(
                "Ano-teste de CBS e IBS; confirmar obrigações acessórias "
                "e compensações aplicáveis."
            ),
            source_url=OFFICIAL_REFORM_SOURCE,
        ),
        _timeline_item(
            year=2027,
            phase="cbs",
            review_date="2027-01-01",
            summary=(
                "Início da etapa CBS e extinção de PIS/Cofins conforme "
                "cronograma legal aplicável."
            ),
            source_url=OFFICIAL_REFORM_SOURCE,
        ),
        _timeline_item(
            year=2029,
            phase="ibs_10",
            review_date="2029-01-01",
            summary="Transição ISS/ICMS para IBS: referência oficial 10%/90%.",
            source_url=OFFICIAL_REFORM_SOURCE,
        ),
        _timeline_item(
            year=2030,
            phase="ibs_20",
            review_date="2030-01-01",
            summary="Transição ISS/ICMS para IBS: referência oficial 20%/80%.",
            source_url=OFFICIAL_REFORM_SOURCE,
        ),
        _timeline_item(
            year=2031,
            phase="ibs_30",
            review_date="2031-01-01",
            summary="Transição ISS/ICMS para IBS: referência oficial 30%/70%.",
            source_url=OFFICIAL_REFORM_SOURCE,
        ),
        _timeline_item(
            year=2032,
            phase="ibs_40",
            review_date="2032-01-01",
            summary="Transição ISS/ICMS para IBS: referência oficial 40%/60%.",
            source_url=OFFICIAL_REFORM_SOURCE,
        ),
        _timeline_item(
            year=2033,
            phase="full",
            review_date="2033-01-01",
            summary="Vigência integral do novo modelo segundo o cronograma oficial atual.",
            source_url=OFFICIAL_REFORM_SOURCE,
        ),
    ]
    return {
        "schemaVersion": 1,
        "verifiedAt": "2026-08-02",
        "asOf": as_of.isoformat(),
        "reviewRequired": as_of > date(2026, 9, 1),
        "timeline": timeline,
        "rules": [
            {
                "key": "legal_services_reduced_rate_candidate",
                "status": "human_validation_required",
                "summary": (
                    "Serviços profissionais regulados podem ter redução legal; "
                    "confirmar enquadramento da receita e texto consolidado antes de simular."
                ),
                "sourceUrl": LC214_SOURCE,
            },
            {
                "key": "simples_anexo_iv_cpp_separate",
                "status": "official_guidance",
                "summary": (
                    "Serviços advocatícios no Anexo IV exigem análise separada "
                    "da contribuição patronal previdenciária."
                ),
                "sourceUrl": SIMPLES_IV_SOURCE,
            },
        ],
        "mandatoryHumanGate": "contador",
        "disclaimer": (
            "Simulação gerencial. Não transmite obrigação, escolhe regime, "
            "altera preço ou substitui validação do contador."
        ),
    }


def tax_reform_reminder_schedule(
    profile: Any | None,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Compute source-bound review windows without creating notifications.

    A worker is the only component that calls a human. The UI can safely show
    this projection and distinguish an upcoming window from a due one without
    pretending a delivery has occurred.
    """
    as_of = as_of or date.today()
    catalog = tax_rule_catalog(as_of=as_of)
    profile_version = int(getattr(profile, "version", 0) or 0)
    rows: list[dict[str, Any]] = []
    for item in catalog["timeline"]:
        try:
            milestone_date = date.fromisoformat(str(item["reviewDate"]))
        except (KeyError, TypeError, ValueError):
            continue
        # Completed phases are historical context, never a source of a late
        # autonomous call. The finance UI retains them in the official timeline.
        if milestone_date <= as_of:
            continue
        for offset_days in TAX_REFORM_ALERT_OFFSETS:
            trigger_date = milestone_date - timedelta(days=offset_days)
            state = "due" if trigger_date <= as_of else "scheduled"
            rows.append(
                {
                    "scheduleKey": (
                        f"tax-reform:v{catalog['schemaVersion']}:p{profile_version}:"
                        f"{item['phase']}:d{offset_days}"
                    ),
                    "catalogVersion": catalog["schemaVersion"],
                    "profileVersion": profile_version,
                    "milestoneKey": item["phase"],
                    "milestoneDate": milestone_date.isoformat(),
                    "triggerDate": trigger_date.isoformat(),
                    "offsetDays": offset_days,
                    "label": f"D-{offset_days}",
                    "state": state,
                    "profileConfigured": profile is not None,
                    "summary": item["summary"],
                    "sourceUrl": item["sourceUrl"],
                }
            )
    return rows


def due_tax_reform_reminders(
    profile: Any | None,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Return at most one current reminder per official transition phase.

    If a scheduler starts late, issuing every obsolete D-180/D-90 notice in
    one run would be noisy and look like a loop. The nearest outstanding
    window is the only action created; subsequent windows become due in time.
    """
    schedule = tax_reform_reminder_schedule(profile, as_of=as_of)
    due: list[dict[str, Any]] = []
    by_milestone: dict[str, list[dict[str, Any]]] = {}
    for item in schedule:
        if item["state"] == "due":
            by_milestone.setdefault(str(item["milestoneKey"]), []).append(item)
    for rows in by_milestone.values():
        due.append(min(rows, key=lambda item: int(item["offsetDays"])))
    return sorted(due, key=lambda item: (item["milestoneDate"], int(item["offsetDays"])))


def tax_profile_alerts(profile, *, as_of: date | None = None) -> list[dict]:
    as_of = as_of or date.today()
    alerts: list[dict] = []
    if profile is None:
        return [
            {
                "severity": "high",
                "code": "tax_profile_missing",
                "message": "Perfil tributário não configurado; solicite validação do contador.",
            }
        ]
    if not profile.sources:
        alerts.append(
            {
                "severity": "high",
                "code": "tax_source_missing",
                "message": "Regra tributária sem fonte identificada.",
            }
        )
    if profile.regime == "simples_anexo_iv":
        alerts.append(
            {
                "severity": "medium",
                "code": "cpp_outside_das_review",
                "message": "Confirme com o contador a CPP patronal fora do DAS para o Anexo IV.",
            }
        )
    if as_of.year == 2026:
        alerts.append(
            {
                "severity": "high",
                "code": "reform_test_year",
                "message": (
                    "Ano-teste da reforma: confirme obrigações acessórias "
                    "de CBS/IBS antes do prazo aplicável."
                ),
            }
        )
    if as_of.year <= 2032:
        alerts.append(
            {
                "severity": "medium",
                "code": "reform_transition_pending",
                "message": "Mantenha a simulação de transição 2029–2032 revisada pelo contador.",
            }
        )
    return alerts
