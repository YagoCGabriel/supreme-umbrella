"""Small compatibility CLI for the legacy song endpoint."""

import os
import sys

import requests
import typer

CIFRACLUB_API_URL = os.getenv("CIFRACLUB_API_URL", "http://localhost:3000").rstrip("/")
app = typer.Typer(help="Cliente de terminal da Cifra Club API.")


@app.command()
def get(artist: str, song: str) -> None:
    """Obtém e imprime a cifra de uma música."""
    url = f"{CIFRACLUB_API_URL}/artists/{artist}/{song}"
    try:
        response = requests.get(url, timeout=20)
        payload = response.json()
        response.raise_for_status()
    except requests.RequestException as exc:
        typer.echo(f"Falha ao consultar a API: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.echo("A API retornou uma resposta que não é JSON válido.", err=True)
        raise typer.Exit(code=1) from exc

    for line in payload.get("cifra", []):
        typer.echo(line)


if __name__ == "__main__":
    app()
