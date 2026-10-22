from app.services.azure_active_classification_sync import Candidate, _candidates, canon


def test_canon_matches_classification_engine_semantics():
    assert canon("ALFA+") == canon("Alfa +") == "alfa"
    assert canon("RG_AKS-BR") == canon("rg-aks-br")


def test_candidates_accept_only_complete_azure_rows_and_deduplicate():
    rows = [
        {
            "Cloud": "Azure-TI", "RowKey": "csv-subscription", "Projeto": "ALFA+",
            "SubscriptionRG": "rg-csor-brsouth-prod",
        },
        {
            "Cloud": "Azure-TI", "RowKey": "csv-subscription", "Projeto": "Alfa +",
            "SubscriptionRG": "RG_CSOR-BRSOUTH-PROD",
        },
        {
            "Cloud": "GCP-TI", "RowKey": "ignored", "Projeto": "ALFA+", "SubscriptionRG": "rg",
        },
        {"Cloud": "Azure-TI", "RowKey": "", "Projeto": "ALFA+", "SubscriptionRG": "rg"},
    ]

    assert _candidates(rows) == [
        Candidate("Azure-TI", "csv-subscription", "ALFA+", "rg-csor-brsouth-prod"),
    ]
