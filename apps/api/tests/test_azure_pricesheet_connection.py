from app.services import azure_pricesheet


def test_pricesheet_client_uses_its_independent_connection_credentials(monkeypatch):
    captured = {}

    class FakeBlobServiceClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(azure_pricesheet, "ClientSecretCredential", lambda **kwargs: kwargs)
    monkeypatch.setattr(azure_pricesheet, "BlobServiceClient", FakeBlobServiceClient)
    network = {}
    monkeypatch.setattr(azure_pricesheet, "register_storage_ip", lambda account, ip=None: network.update(account=account, ip=ip))

    service, container = azure_pricesheet._azure_client({
        "tenant_id": "pricesheet-tenant",
        "client_id": "pricesheet-client",
        "client_secret": "pricesheet-secret",
        "storage_account": "pricesheetstorage",
        "storage_ip": "10.0.0.5",
        "container_name": "pricesheet-container",
    })

    assert isinstance(service, FakeBlobServiceClient)
    assert container == "pricesheet-container"
    assert network == {"account": "pricesheetstorage", "ip": "10.0.0.5"}
    assert captured["account_url"] == "https://pricesheetstorage.blob.core.windows.net"
    assert captured["credential"] == {
        "tenant_id": "pricesheet-tenant",
        "client_id": "pricesheet-client",
        "client_secret": "pricesheet-secret",
    }


def test_pricesheet_client_requires_connection_credentials():
    try:
        azure_pricesheet._azure_client({})
    except RuntimeError as exc:
        assert "tenant_id" in str(exc)
    else:
        raise AssertionError("sem configuração da conexão não há credencial de fallback")


def test_pricesheet_refuses_partial_connection_credentials():
    try:
        azure_pricesheet._azure_client({
            "tenant_id": "pricesheet-tenant",
            "client_id": "pricesheet-client",
            "storage_account": "pricesheet-storage",
            "container_name": "pricesheet-container",
        })
    except RuntimeError as exc:
        assert "client_secret" in str(exc)
    else:
        raise AssertionError("configuração incompleta da conexão deve ser recusada")
