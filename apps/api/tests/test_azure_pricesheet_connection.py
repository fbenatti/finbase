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


def test_pricesheet_client_keeps_legacy_settings_fallback(monkeypatch):
    captured = {}

    class FakeBlobServiceClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(azure_pricesheet, "ClientSecretCredential", lambda **kwargs: kwargs)
    monkeypatch.setattr(azure_pricesheet, "BlobServiceClient", FakeBlobServiceClient)
    monkeypatch.setattr(azure_pricesheet.settings, "azure_4p_tenant_id", "legacy-tenant")
    monkeypatch.setattr(azure_pricesheet.settings, "azure_4p_client_id", "legacy-client")
    monkeypatch.setattr(azure_pricesheet.settings, "azure_4p_client_secret", "legacy-secret")
    monkeypatch.setattr(azure_pricesheet.settings, "azure_4p_storage_account", "legacy-storage")
    monkeypatch.setattr(azure_pricesheet.settings, "azure_4p_container", "legacy-container")

    service, container = azure_pricesheet._azure_client({})

    assert isinstance(service, FakeBlobServiceClient)
    assert container == "legacy-container"
    assert captured["account_url"] == "https://legacy-storage.blob.core.windows.net"


def test_pricesheet_refuses_partial_independent_credentials_instead_of_mixing(monkeypatch):
    monkeypatch.setattr(azure_pricesheet.settings, "azure_4p_tenant_id", "legacy-tenant")
    monkeypatch.setattr(azure_pricesheet.settings, "azure_4p_client_id", "legacy-client")
    monkeypatch.setattr(azure_pricesheet.settings, "azure_4p_client_secret", "legacy-secret")
    monkeypatch.setattr(azure_pricesheet.settings, "azure_4p_storage_account", "legacy-storage")
    monkeypatch.setattr(azure_pricesheet.settings, "azure_4p_container", "legacy-container")

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
        raise AssertionError("configuração incompleta não deve misturar credenciais legadas")
