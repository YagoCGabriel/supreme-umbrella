"""Flask REST API for parsing and retrieving structured song data."""

from __future__ import annotations

import json
import os
import shlex
from typing import Any

from flask import Flask, jsonify, render_template, request
from flask_sock import Sock

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
    sock = Sock(app)

    @app.get("/")
    def home():
        return jsonify({
            "api": "Cifra Club API",
            "version": API_VERSION,
            "docs": "/v1/docs",
            "health": "/v1/health",
            "legacy_endpoint": "/artists/<artist>/songs/<song>",
        })

    @app.get("/terminal")
    def terminal_page():
        return render_template("terminal.html")

    @app.get("/v1/docs")
    def docs():
        return jsonify({
            "version": API_VERSION,
            "endpoints": [
                {"method": "GET", "path": "/v1/health", "description": "Estado dos componentes"},
                {"method": "GET", "path": "/terminal", "description": "Terminal web para comandos permitidos"},
                {"method": "WEBSOCKET", "path": "/v1/terminal/ws", "description": "Sessão interativa do terminal web"},
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
        provider_mode = os.getenv("SCRAPER_PROVIDER", "auto").strip().lower()
        return jsonify({
            "status": "ok",
            "version": API_VERSION,
            "components": {
                "parser": "available",
                "cache": "process-local",
                "provider_fetch": "enabled" if fetch_enabled and fetch_authorized else "disabled",
                "provider_mode": provider_mode,
                "scrapfly_configured": bool(os.getenv("SCRAPFLY_API_KEY")),
                "scrapfly_render_js": os.getenv("SCRAPFLY_RENDER_JS", "false").lower() == "true",
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


    @sock.route("/v1/terminal/ws")
    def terminal_socket(ws):
        """Interactive, allowlisted command interface; never executes OS shell input."""
        ws.send(json.dumps({"type": "ready", "text": "Cifra Club API terminal conectado. Digite 'help' para ver os comandos."}))
        while True:
            raw = ws.receive()
            if raw is None:
                break
            try:
                if len(raw) > 1000:
                    raise ValueError("Comando muito longo (limite de 1000 caracteres).")
                parts = shlex.split(raw)
                if not parts:
                    ws.send(json.dumps({"type": "prompt"}))
                    continue
                command, *args = parts
                if command in {"help", "ajuda"}:
                    output = (
                        "Comandos disponíveis:\n"
                        "  help                 Mostra esta ajuda\n"
                        "  version              Versão da API\n"
                        "  health               Estado da API e componentes\n"
                        "  endpoints            Lista endpoints principais\n"
                        "  cifra <artista> <música>  Consulta uma cifra (requer coleta autorizada)\n"
                        "  clear                Limpa a tela do terminal"
                    )
                elif command == "version" and not args:
                    output = f"Cifra Club API v{API_VERSION}"
                elif command == "health" and not args:
                    fetch_enabled = os.getenv("CIFRACLUB_FETCH_ENABLED", "false").lower() == "true"
                    fetch_authorized = os.getenv("CIFRACLUB_FETCH_AUTHORIZED", "false").lower() == "true"
                    output = json.dumps({
                        "status": "ok",
                        "version": API_VERSION,
                        "provider_fetch": "enabled" if fetch_enabled and fetch_authorized else "disabled",
                    }, ensure_ascii=False, indent=2)
                elif command == "endpoints" and not args:
                    output = "\n".join([
                        "GET  /v1/health",
                        "GET  /v1/docs",
                        "POST /v1/parse/html",
                        "GET  /v1/songs/<artist>/<song>",
                        "GET  /v1/songs/<artist>/<song>/chords",
                        "GET  /v1/songs/<artist>/<song>/sections",
                        "GET  /v1/songs/<artist>/<song>/tabs",
                    ])
                elif command == "cifra" and len(args) >= 2:
                    artist, song = args[0], " ".join(args[1:])
                    result = CifraClub().cifra(artist, song)
                    lines = result.get("cifra") or []
                    output = "\n".join(lines) if lines else json.dumps(result, ensure_ascii=False, indent=2)
                elif command == "clear" and not args:
                    ws.send(json.dumps({"type": "clear"}))
                    continue
                else:
                    output = f"Comando inválido: {command}. Digite 'help'."
                ws.send(json.dumps({"type": "output", "text": output}, ensure_ascii=False))
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                if isinstance(exc, UpstreamError) and exc.diagnostics:
                    error_text += "\nDiagnóstico: " + json.dumps(exc.diagnostics, ensure_ascii=False, indent=2)
                ws.send(json.dumps({"type": "error", "text": error_text}, ensure_ascii=False))

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
        payload = {"error": "upstream_error", "message": str(exc)}
        if exc.diagnostics:
            payload["diagnostics"] = exc.diagnostics
        return jsonify(payload), 502

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
