import pytest
from pydantic import ValidationError

from app.schemas.matter import MatterUpdate


@pytest.mark.parametrize(
    "payload",
    [
        {"columnId": "qualificacao"},
        {"column_id": "qualificacao"},
        {"microStatus": "lead_novo"},
        {"micro_status": None},
    ],
)
def test_generic_matter_update_rejects_kanban_stage_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="comando de movimentação do Kanban"):
        MatterUpdate.model_validate(payload)


def test_generic_matter_update_still_accepts_dossier_fields() -> None:
    update = MatterUpdate.model_validate(
        {
            "priority": "alta",
            "nextAction": "Solicitar relatório médico",
            "strategyNotes": "Revisão humana antes de qualquer envio externo.",
        }
    )

    assert update.priority.value == "alta"
    assert update.next_action == "Solicitar relatório médico"
