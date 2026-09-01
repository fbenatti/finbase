from app.services.azure_active_classification_sync import Candidate, _candidates, canon


def test_canon_matches_classification_engine_semantics():
    assert canon("VIVO+") == canon("Vivo +") == "vivo"
    assert canon("RG_AKS-BR") == canon("rg-aks-br")


def test_candidates_accept_only_complete_azure_rows_and_deduplicate():
    rows = [
        {
            "Cloud": "Azure-TI", "RowKey": "csv-subscription", "Projeto": "VIVO+",
            "SubscriptionRG": "rg-csor-brsouth-prod",
        },
        {
            "Cloud": "Azure-TI", "RowKey": "csv-subscription", "Projeto": "Vivo +",
            "SubscriptionRG": "RG_CSOR-BRSOUTH-PROD",
        },
        {
            "Cloud": "GCP-TI", "RowKey": "ignored", "Projeto": "VIVO+", "SubscriptionRG": "rg",
        },
        {"Cloud": "Azure-TI", "RowKey": "", "Projeto": "VIVO+", "SubscriptionRG": "rg"},
    ]

    assert _candidates(rows) == [
        Candidate("Azure-TI", "csv-subscription", "VIVO+", "rg-csor-brsouth-prod"),
    ]
