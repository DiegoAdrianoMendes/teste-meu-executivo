from app.core.tenant_cost_privacy import strip_internal_provider_cost_fields


def test_provider_cost_scrubber_preserves_office_finance_and_legal_fees():
    payload = {
        "dailyBudgetBrl": 50,
        "average_cost_brl": 0.12,
        "estimatedCostBrl": 0.20,
        "providerCost": 0.18,
        "costAmount": 0.18,
        "costCurrency": "BRL",
        "costEntry": {"amount": 0.18, "currency": "BRL"},
        "externalCost": 0.18,
        "costState": {
            "currency": "BRL",
            "messageCount": 7,
            "actualCostsByCurrency": {"BRL": "0.18"},
            "externalCost": "0.18",
            "byTransport": {"cloud_api": 7},
        },
        "commercial": {
            "minimumInitialFeeBrl": 4000,
            "maximumInitialFeeBrl": 8000,
            "successOnlyAllowed": True,
        },
        "officeFinance": {
            "amount": 12000,
            "currency": "BRL",
            "revenue": 15000,
            "expense": 3000,
        },
    }

    cleaned = strip_internal_provider_cost_fields(payload)

    assert cleaned["costState"] == {
        "messageCount": 7,
        "byTransport": {"cloud_api": 7},
    }
    assert cleaned["commercial"] == payload["commercial"]
    assert cleaned["officeFinance"] == payload["officeFinance"]
    for key in (
        "dailyBudgetBrl",
        "average_cost_brl",
        "estimatedCostBrl",
        "providerCost",
        "costAmount",
        "costCurrency",
        "costEntry",
        "externalCost",
    ):
        assert key not in cleaned
