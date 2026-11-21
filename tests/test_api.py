from pathlib import Path
from unittest.mock import patch

from api import create_app
from cifraclub import ProviderAccessDisabled

FIXTURE = Path(__file__).parent / "fixtures" / "song_page.html"


def test_health_endpoint_reports_fetch_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CIFRACLUB_FETCH_ENABLED", raising=False)
    monkeypatch.delenv("CIFRACLUB_FETCH_AUTHORIZED", raising=False)
    client = create_app({"TESTING": True}).test_client()
    response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json["components"]["parser"] == "available"
    assert response.json["components"]["provider_fetch"] == "disabled"


def test_html_parse_endpoint_parses_caller_provided_html():
    client = create_app({"TESTING": True}).test_client()
    response = client.post("/v1/parse/html", json={"html": FIXTURE.read_text(encoding="utf-8")})

    assert response.status_code == 200
    assert response.json["name"] == "Canção de Teste"
    assert response.json["extraction"]["method"] == "html_parser"


def test_html_parse_endpoint_rejects_missing_html():
    client = create_app({"TESTING": True}).test_client()
    response = client.post("/v1/parse/html", json={})
    assert response.status_code == 400


def test_legacy_route_keeps_original_route_and_keys():
    client = create_app({"TESTING": True}).test_client()
    fake_song = {
        "artist": "Artista Exemplo", "name": "Canção de Teste", "youtube_url": None,
        "cifraclub_url": "https://www.cifraclub.com.br/", "cifra": ["Am", "G"],
        "chords": ["Am", "G"], "sections": [], "tabs": [], "music": {}, "extraction": {},
    }
    with patch("api.CifraClub.cifra", return_value=fake_song):
        response = client.get("/artists/artista-exemplo/songs/cancao-de-teste")

    assert response.status_code == 200
    assert response.json["cifra"] == ["Am", "G"]
    assert response.json["name"] == "Canção de Teste"


def test_legacy_route_explains_disabled_remote_fetch():
    client = create_app({"TESTING": True}).test_client()
    with patch("api.CifraClub.cifra", side_effect=ProviderAccessDisabled("fetch desligado")):
        response = client.get("/artists/artista-exemplo/songs/cancao-de-teste")

    assert response.status_code == 503
    assert response.json["error"] == "provider_access_disabled"
