"""Flask REST API for parsing and retrieving structured song data."""

from __future__ import annotations

import os
from typing import Any

from flask import Flask, jsonify, request

try:  # Supports both `python api.py` and package-based test imports.
    from .cache import TTLCache
    from .cifraclub import (
        CifraClub,
        CifraClubParser,
        ExtractionError,
        ProviderAccessDisabled,
        SongNotFound,
        UpstreamError,
    )
except ImportError:  # pragma: no cover - exercised by Docker's /app working dir
    from cache import TTLCache
    from cifraclub import (
        CifraClub,
        CifraClubParser,
        ExtractionError,
        ProviderAccessDisabled,
        SongNotFound,
        UpstreamError,
    )


API_VERSION = "2.0.0"
cache = TTLCache(ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "300")))


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=2 * 1024 * 1024)
    app.json.ensure_ascii = False
    if test_config:
        app.config.update(test_config)

    parser = CifraClubParser()

    @app.get("/")
    def home():
        return jsonify({
            "api": "Cifra Club API",
            "version": API_VERSION,
            "docs": "/v1/docs",
            "health": "/v1/health",
            "legacy_endpoint": "/artists/<artist>/songs/<song>",
        })

    @app.get("/v1/docs")
    def docs():
        return jsonify({
            "version": API_VERSION,
            "endpoints": [
                {"method": "GET", "path": "/v1/health", "description": "Estado dos componentes"},
                {"method": "POST", "path": "/v1/parse/html", "description": "Analisa HTML fornecido pelo cliente"},
                {"method": "GET", "path": "/v1/songs/{artist}/{song}", "description": "Música estruturada"},
                {"method": "GET", "path": "/v1/songs/{artist}/{song}/metadata", "description": "Metadados"},
                {"method": "GET", "path": "/v1/songs/{artist}/{song}/chords", "description": "Acordes"},
                {"method": "GET", "path": "/v1/songs/{artist}/{song}/sections", "description": "Seções"},
                {"method": "GET", "path": "/v1/songs/{artist}/{song}/tabs", "description": "Tablaturas"},
                {"method": "GET", "path": "/artists/{artist}/songs/{song}", "description": "Compatibilidade com a API original"},
            ],
        })

    @app.get("/v1/health")
    @app.get("/health")
    def health():
        fetch_enabled = os.getenv("CIFRACLUB_FETCH_ENABLED", "false").lower() == "true"
        fetch_authorized = os.getenv("CIFRACLUB_FETCH_AUTHORIZED", "false").lower() == "true"
        return jsonify({
            "status": "ok",
            "version": API_VERSION,
            "components": {
                "parser": "available",
                "cache": "process-local",
                "provider_fetch": "enabled" if fetch_enabled and fetch_authorized else "disabled",
            },
        })

    @app.post("/v1/parse/html")
    def parse_html():
        """Parse HTML provided by the caller; this route makes no outbound request."""
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "invalid_request", "message": "Envie um objeto JSON com o campo 'html'."}), 400
        html = payload.get("html")
        if not isinstance(html, str) or not html.strip():
            return jsonify({"error": "invalid_request", "message": "Informe o campo 'html' como texto não vazio."}), 400
        if len(html.encode("utf-8")) > 2 * 1024 * 1024:
            return jsonify({"error": "payload_too_large", "message": "O HTML ultrapassa 2 MiB."}), 413
        source_url = payload.get("source_url")
        try:
            result = parser.parse(html, source_url=source_url if isinstance(source_url, str) else None)
            status = 200 if result["extraction"]["status"] == "success" else 422
            return jsonify(result), status
        except ExtractionError as exc:
            return jsonify({"error": "extraction_failed", "message": str(exc)}), 422

    def load_song(artist: str, song: str) -> dict[str, Any]:
        key = f"song:{artist.strip().casefold()}:{song.strip().casefold()}"
        cached = cache.get(key)
        if cached is not None:
            return cached
        result = CifraClub().cifra(artist, song)
        cache.set(key, result)
        return result

    @app.get("/v1/songs/<artist>/<song>")
    def get_song(artist: str, song: str):
        return jsonify(load_song(artist, song))

    @app.get("/v1/songs/<artist>/<song>/metadata")
    def get_song_metadata(artist: str, song: str):
        data = load_song(artist, song)
        return jsonify({
            "artist": data.get("artist"),
            "name": data.get("name"),
            "composers": data.get("composers", []),
            "youtube_url": data.get("youtube_url"),
            "cifraclub_url": data.get("cifraclub_url"),
            "music": data.get("music", {}),
            "extraction": data.get("extraction", {}),
        })

    @app.get("/v1/songs/<artist>/<song>/chords")
    def get_song_chords(artist: str, song: str):
        data = load_song(artist, song)
        return jsonify({"artist": data.get("artist"), "song": data.get("name"), "chords": data.get("chords", [])})

    @app.get("/v1/songs/<artist>/<song>/sections")
    def get_song_sections(artist: str, song: str):
        data = load_song(artist, song)
        return jsonify({"artist": data.get("artist"), "song": data.get("name"), "sections": data.get("sections", [])})

    @app.get("/v1/songs/<artist>/<song>/tabs")
    def get_song_tabs(artist: str, song: str):
        data = load_song(artist, song)
        return jsonify({"artist": data.get("artist"), "song": data.get("name"), "tabs": data.get("tabs", [])})

    # Backwards-compatible route and response keys from the original repository.
    @app.get("/artists/<artist>/songs/<song>")
    def get_legacy_cifra(artist: str, song: str):
        data = load_song(artist, song)
        return jsonify({
            "artist": data.get("artist"),
            "name": data.get("name"),
            "youtube_url": data.get("youtube_url"),
            "cifraclub_url": data.get("cifraclub_url"),
            "cifra": data.get("cifra", []),
            "chords": data.get("chords", []),
            "sections": data.get("sections", []),
            "tabs": data.get("tabs", []),
            "music": data.get("music", {}),
            "extraction": data.get("extraction", {}),
        })

    @app.errorhandler(ProviderAccessDisabled)
    def provider_disabled(exc: ProviderAccessDisabled):
        return jsonify({"error": "provider_access_disabled", "message": str(exc)}), 503

    @app.errorhandler(SongNotFound)
    def song_not_found(exc: SongNotFound):
        return jsonify({"error": "song_not_found", "message": str(exc)}), 404

    @app.errorhandler(ExtractionError)
    def extraction_error(exc: ExtractionError):
        return jsonify({"error": "extraction_failed", "message": str(exc)}), 422

    @app.errorhandler(UpstreamError)
    def upstream_error(exc: UpstreamError):
        return jsonify({"error": "upstream_error", "message": str(exc)}), 502

    @app.errorhandler(ValueError)
    def invalid_value(exc: ValueError):
        return jsonify({"error": "invalid_request", "message": str(exc)}), 400

    @app.errorhandler(413)
    def request_too_large(_exc):
        return jsonify({"error": "payload_too_large", "message": "O corpo da requisição ultrapassa 2 MiB."}), 413

    @app.errorhandler(404)
    def not_found(_exc):
        return jsonify({"error": "not_found", "message": "Endpoint não encontrado."}), 404

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "3000")), debug=False)
