from unittest.mock import AsyncMock

import pytest

from app.ai.base import AICompletionResult
from app.services.meta_ai_service import MetaAIService


@pytest.mark.asyncio
async def test_analysis_returns_new_contract_and_deterministic_floor():
    service = MetaAIService(db=None)  # type: ignore[arg-type]
    service._complete_with_fallback = AsyncMock(  # type: ignore[method-assign]
        return_value=AICompletionResult(
            text="",
            provider="openai",
            model_used="gpt-test",
            structured_data={
                "predicted_category": "marketing",
                "approval_likelihood": "medium",
                "confidence": 0.8,
                "cost_band": "maior que utility",
                "recommendation": "Revisar categoria",
                "summary": "Mensagem promocional.",
                "signals": ["Oferta comercial", "Chamada para ação"],
                "meta_category_suggested": "marketing",
                "marketing_signals": ["Oferta comercial"],
                "oab_risk": {"level": "low", "signals": []},
            },
        )
    )

    result = await service.analyze_whatsapp_template(
        workspace_id="workspace-1",
        name="causa_ganha",
        category_intent="utility",
        body="Causa ganha: contrate agora o melhor escritório.",
    )

    assert result["meta_category_suggested"] == "marketing"
    assert result["marketing_signals"] == ["Oferta comercial"]
    assert result["oab_risk"]["level"] == "high"
    assert result["oab_risk"]["requires_acknowledgement"] is True
    assert "promessa_resultado" in result["oab_risk"]["signals"]
    assert result["provider"] == "openai"

    call = service._complete_with_fallback.await_args.kwargs
    required = call["response_schema"]["required"]
    assert "meta_category_suggested" in required
    assert "marketing_signals" in required
    assert "oab_risk" in required
