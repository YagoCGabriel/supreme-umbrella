from pathlib import Path

from cifraclub import CifraClub, CifraClubParser, ExtractionError, ProviderAccessDisabled, ScraperSettings

FIXTURE = Path(__file__).parent / "fixtures" / "song_page.html"


def test_extracts_song_metadata_and_raw_lines():
    result = CifraClubParser().parse(FIXTURE.read_text(encoding="utf-8"))

    assert result["name"] == "Canção de Teste"
    assert result["artist"] == "Artista Exemplo"
    assert result["youtube_url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert result["cifraclub_url"].endswith("/artista-exemplo/cancao-de-teste/")
    assert result["composers"] == ["Compositor Exemplo"]
    assert result["extraction"]["status"] == "success"
    assert "[Intro]  C#m7  A9" in result["cifra"]


def test_extracts_chords_sections_and_tab_blocks():
    result = CifraClubParser().parse(FIXTURE.read_text(encoding="utf-8"))

    assert "C#m7" in result["chords"]
    assert "A9" in result["chords"]
    assert "intro" in [section["type"] for section in result["sections"]]
    assert "chorus" in [section["type"] for section in result["sections"]]
    assert len(result["tabs"]) == 1
    assert len(result["tabs"][0]["lines"]) == 6


def test_returns_partial_result_when_metadata_is_missing():
    result = CifraClubParser().parse("<html><body><pre>Am   G\nLinha teste</pre></body></html>")
    assert result["extraction"]["status"] == "partial"
    assert any("Título" in warning for warning in result["extraction"]["warnings"])


def test_rejects_empty_or_oversized_html():
    parser = CifraClubParser()
    try:
        parser.parse("  ")
        assert False, "Expected ExtractionError"
    except ExtractionError:
        pass

    try:
        parser.parse("a" * (2 * 1024 * 1024 + 1))
        assert False, "Expected ExtractionError"
    except ExtractionError:
        pass


def test_extracts_extended_and_slash_chords():
    html = "<html><h1>Teste</h1><pre>E7M(9)   F#7   Bm7(b5)   C7M\nA9   D/F#</pre></html>"
    result = CifraClubParser().parse(html)
    assert result["chords"] == ["E7M(9)", "F#7", "Bm7(b5)", "C7M", "A9", "D/F#"]


def test_remote_fetch_requires_both_explicit_flags():
    scraper = CifraClub(ScraperSettings(fetch_enabled=True, fetch_authorized=False))
    try:
        scraper.cifra("artista", "musica")
        assert False, "Expected ProviderAccessDisabled"
    except ProviderAccessDisabled:
        pass


def test_extracts_chords_after_section_heading():
    html = "<html><h1>Teste</h1><pre>[Intro]  C#m7  A9</pre></html>"
    result = CifraClubParser().parse(html)
    assert result["chords"] == ["C#m7", "A9"]
