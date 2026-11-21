import pytest

from cifraclub import (
    CifraClub,
    ProviderAccessDisabled,
    ScraperSettings,
    UpstreamError,
)


def _client(**overrides):
    values = {
        "fetch_enabled": True,
        "fetch_authorized": True,
        "provider": "auto",
        "scrapfly_api_key": "test-key",
    }
    values.update(overrides)
    return CifraClub(settings=ScraperSettings(**values))


@pytest.mark.parametrize("status_code", [401, 403, 429, 451])
def test_auto_provider_does_not_fallback_after_explicit_denial(monkeypatch, status_code):
    client = _client()
    calls = []

    def denied(_url):
        raise UpstreamError(
            "upstream denied",
            diagnostics={"provider": "httpx", "status_code": status_code},
        )

    def should_not_run(_url):
        calls.append("scrapfly")
        return "<html></html>"

    monkeypatch.setattr(client, "_fetch_httpx", denied)
    monkeypatch.setattr(client, "_fetch_scrapfly", should_not_run)

    with pytest.raises(UpstreamError):
        client._fetch("https://www.cifraclub.com.br/artist/song/")

    assert calls == []


def test_auto_provider_falls_back_to_scrapfly_after_transient_5xx(monkeypatch):
    client = _client()

    def unavailable(_url):
        raise UpstreamError(
            "upstream unavailable",
            diagnostics={"provider": "httpx", "status_code": 503},
        )

    monkeypatch.setattr(client, "_fetch_httpx", unavailable)
    monkeypatch.setattr(client, "_fetch_scrapfly", lambda _url: "<html>ok</html>")

    html = client._fetch("https://www.cifraclub.com.br/artist/song/")

    assert html == "<html>ok</html>"
    assert client._last_provider == "scrapfly"


def test_auto_provider_falls_back_after_transport_error(monkeypatch):
    client = _client()

    def transport_error(_url):
        raise UpstreamError(
            "network unavailable",
            diagnostics={"provider": "httpx", "transport_error": True},
        )

    monkeypatch.setattr(client, "_fetch_httpx", transport_error)
    monkeypatch.setattr(client, "_fetch_scrapfly", lambda _url: "<html>ok</html>")

    assert client._fetch("https://www.cifraclub.com.br/artist/song/") == "<html>ok</html>"
    assert client._last_provider == "scrapfly"


def test_scrapfly_requires_api_key():
    client = _client(provider="scrapfly", scrapfly_api_key=None)

    with pytest.raises(ProviderAccessDisabled, match="SCRAPFLY_API_KEY"):
        client._fetch("https://www.cifraclub.com.br/artist/song/")


def test_scrapfly_still_requires_remote_fetch_authorization():
    client = _client(provider="scrapfly", fetch_enabled=False)

    with pytest.raises(ProviderAccessDisabled):
        client._fetch("https://www.cifraclub.com.br/artist/song/")
