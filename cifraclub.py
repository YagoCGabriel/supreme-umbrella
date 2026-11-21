"""Cifra Club HTML parser and optional, authorization-gated fetcher.

Parsing HTML supplied by the caller is independent of network access. Fetching
Cifra Club pages is disabled by default and requires an explicit authorization
flag because the provider's published terms restrict automated data extraction.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Optional
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

CIFRACLUB_URL = "https://www.cifraclub.com.br/"
MAX_HTML_BYTES = 2 * 1024 * 1024


class CifraClubError(Exception):
    """Base exception for API-facing extraction errors."""


class ProviderAccessDisabled(CifraClubError):
    """Raised unless provider fetching was explicitly authorized and enabled."""


class UpstreamError(CifraClubError):
    """Raised when the upstream provider cannot be reached or rejects a request."""

    def __init__(self, message: str, diagnostics: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class SongNotFound(CifraClubError):
    """Raised when the provider reports that the requested song does not exist."""


class ExtractionError(CifraClubError):
    """Raised when a page was retrieved but contains no usable song data."""


@dataclass(frozen=True)
class ScraperSettings:
    fetch_enabled: bool = False
    fetch_authorized: bool = False
    timeout_seconds: float = 15.0
    max_retries: int = 2
    min_request_interval: float = 1.5
    browser_fallback: bool = False
    selenium_remote_url: Optional[str] = None

    @classmethod
    def from_env(cls) -> "ScraperSettings":
        return cls(
            fetch_enabled=os.getenv("CIFRACLUB_FETCH_ENABLED", "false").lower() == "true",
            fetch_authorized=os.getenv("CIFRACLUB_FETCH_AUTHORIZED", "false").lower() == "true",
            timeout_seconds=float(os.getenv("SCRAPER_TIMEOUT_SECONDS", "15")),
            max_retries=max(0, int(os.getenv("SCRAPER_MAX_RETRIES", "2"))),
            min_request_interval=max(0.0, float(os.getenv("SCRAPER_MIN_INTERVAL", "1.5"))),
            browser_fallback=os.getenv("SCRAPER_BROWSER_FALLBACK", "false").lower() == "true",
            selenium_remote_url=os.getenv("SELENIUM_REMOTE_URL"),
        )


# Cifra Club chord names use roots A-G, optional accidentals, quality/extension,
# and optional slash bass. This is intentionally conservative to reduce false hits
# in lyric lines; only chord-like standalone lines are parsed as chord lists.
_CHORD_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"([A-G](?:#|b)?"
    r"(?:maj|min|dim|aug|sus[24]?|add\d+|m|M|°|º|\+)?"
    r"(?:\d{1,2})?"
    r"(?:M|maj)?"
    r"(?:\([^()\s]{1,8}\))?"
    r"(?:/[A-G](?:#|b)?)?)"
    r"(?![A-Za-z0-9])"
)
_TAB_LINE = re.compile(r"^\s*[EADGBe](?:\|.*\||\s*\|.*\|)")
_SECTION = re.compile(r"^\s*\[([^\]]{1,80})\]\s*(.*)$")
_YOUTUBE_ID = re.compile(r"(?:/vi/|youtube(?:-nocookie)?\.com/embed/|youtu\.be/)([A-Za-z0-9_-]{11})")


def _first_text(soup: BeautifulSoup, selectors: Iterable[str]) -> Optional[str]:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)
            if value:
                return str(value).strip()
    return None


def _walk_jsonld(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _walk_jsonld(item)
        for key in ("mainEntity", "mainEntityOfPage", "item", "itemListElement"):
            nested = value.get(key)
            if isinstance(nested, (dict, list)):
                yield from _walk_jsonld(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_jsonld(item)


def _jsonld_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            parsed = json.loads(raw.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        objects.extend(_walk_jsonld(parsed))
    return objects


def _as_name(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _as_name(value.get("name"))
    if isinstance(value, list):
        names = [_as_name(item) for item in value]
        joined = [item for item in names if item]
        return ", ".join(joined) if joined else None
    return None


def _is_chord_line(line: str, matches: list[re.Match[str]]) -> bool:
    if not matches or _TAB_LINE.match(line):
        return False
    matched_chars = sum(len(match.group(0)) for match in matches)
    remainder = _CHORD_TOKEN.sub("", line)
    # A chord row may include spaces and punctuation, but not normal lyric text.
    return len(re.sub(r"[\s|,;:/()\-]", "", remainder)) <= 2 and matched_chars >= 1


def _section_type(name: str) -> str:
    normalized = name.casefold()
    mapping = {
        "intro": "intro", "introdução": "intro", "introducao": "intro",
        "verso": "verse", "primeira parte": "verse", "segunda parte": "verse",
        "refrão": "chorus", "refrao": "chorus", "chorus": "chorus",
        "ponte": "bridge", "bridge": "bridge", "solo": "solo",
        "final": "outro", "outro": "outro", "interlúdio": "interlude",
        "interludio": "interlude",
    }
    for token, section_type in mapping.items():
        if token in normalized:
            return section_type
    return "other"


class CifraClubParser:
    """Extracts structured fields from HTML without making network requests."""

    def parse(self, html: str, source_url: Optional[str] = None) -> dict[str, Any]:
        if not isinstance(html, str) or not html.strip():
            raise ExtractionError("O HTML fornecido está vazio.")
        if len(html.encode("utf-8")) > MAX_HTML_BYTES:
            raise ExtractionError("O HTML ultrapassa o limite de 2 MiB.")

        soup = BeautifulSoup(html, "lxml")
        jsonld = _jsonld_objects(soup)
        primary = next(
            (item for item in jsonld if any(key in item for key in ("byArtist", "composer", "musicDetails"))),
            next((item for item in jsonld if "name" in item), {}),
        )

        title = (
            _first_text(soup, [".cifra h1.t1", ".cifra h1", "h1.t1", "h1", 'meta[property="og:title"]'])
            or _as_name(primary.get("name"))
            or _as_name(primary.get("headline"))
        )
        artist = (
            _first_text(soup, [".cifra h2.t3", "h2.t3", '[itemprop="byArtist"]', 'meta[name="author"]'])
            or _as_name(primary.get("byArtist"))
            or _as_name(primary.get("author"))
        )

        canonical = _first_text(soup, ['link[rel="canonical"]', 'meta[property="og:url"]'])
        # link[rel=canonical] stores href, not content.
        canonical_node = soup.select_one('link[rel="canonical"]')
        if canonical_node and canonical_node.get("href"):
            canonical = canonical_node.get("href")
        page_url = canonical or source_url

        youtube_url = self._youtube_url(soup)
        composers = self._composers(primary)
        lyrics_node = self._find_lyrics_node(soup)
        raw_lines = self._extract_lines(lyrics_node) if lyrics_node else []
        raw_lines = [line.rstrip("\r") for line in raw_lines]
        if raw_lines and not any(line.strip() for line in raw_lines):
            raw_lines = []

        chords = self._extract_chords(raw_lines)
        sections = self._extract_sections(raw_lines)
        tabs = self._extract_tabs(raw_lines)
        music_settings = self._extract_music_settings(soup, primary)

        warnings: list[str] = []
        if not title:
            warnings.append("Título não identificado no HTML.")
        if not artist:
            warnings.append("Artista não identificado no HTML.")
        if not raw_lines:
            warnings.append("Conteúdo da cifra/tablatura não identificado no HTML.")

        status = "success" if title and raw_lines else "partial"
        confidence = 0.98 if status == "success" and artist else 0.82 if status == "success" else 0.35
        return {
            "artist": artist,
            "name": title,
            "youtube_url": youtube_url,
            "cifraclub_url": page_url,
            "composers": composers,
            "music": music_settings,
            "chords": chords,
            "sections": sections,
            "tabs": tabs,
            "cifra": raw_lines,
            "extraction": {
                "status": status,
                "method": "html_parser",
                "confidence": confidence,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "warnings": warnings,
            },
        }

    @staticmethod
    def _youtube_url(soup: BeautifulSoup) -> Optional[str]:
        candidates: list[str] = []
        for node in soup.select(".player-placeholder img, iframe[src], meta[property='og:video'], meta[name='twitter:player']"):
            value = node.get("src") or node.get("content")
            if value:
                candidates.append(str(value))
        for value in candidates:
            match = _YOUTUBE_ID.search(value)
            if match:
                return f"https://www.youtube.com/watch?v={match.group(1)}"
            parsed = urlparse(value)
            if parsed.hostname in {"www.youtube.com", "youtube.com"} and parsed.path == "/watch":
                from urllib.parse import parse_qs
                video_id = parse_qs(parsed.query).get("v", [None])[0]
                if video_id and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                    return f"https://www.youtube.com/watch?v={video_id}"
        return None

    @staticmethod
    def _composers(primary: dict[str, Any]) -> list[str]:
        raw = primary.get("composer") or primary.get("creator")
        values = raw if isinstance(raw, list) else [raw]
        names = [_as_name(value) for value in values]
        return list(dict.fromkeys(name for name in names if name))

    @staticmethod
    def _find_lyrics_node(soup: BeautifulSoup) -> Optional[Tag]:
        # Prefer the most specific known containers and <pre>, which preserve spacing.
        for selector in (".cifra_cnt pre", ".cifra_cnt", "[data-testid='lyrics-content'] pre", "[data-testid='lyrics-content']", "pre"):
            node = soup.select_one(selector)
            if node and node.get_text(strip=True):
                return node
        return None

    @staticmethod
    def _extract_lines(node: Tag) -> list[str]:
        text = node.get_text("\n" if node.name != "pre" else "", strip=False)
        # Keep intentional blank lines and chord alignment, but normalize CRLF.
        return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    @staticmethod
    def _extract_chords(lines: list[str]) -> list[str]:
        chords: list[str] = []
        for line in lines:
            section_match = _SECTION.match(line)
            chord_text = section_match.group(2) if section_match else line
            matches = list(_CHORD_TOKEN.finditer(chord_text))
            if _is_chord_line(chord_text, matches):
                for match in matches:
                    chord = match.group(1)
                    if chord not in chords:
                        chords.append(chord)
        return chords

    @staticmethod
    def _extract_sections(lines: list[str]) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        current: Optional[dict[str, Any]] = None
        for line_number, line in enumerate(lines, start=1):
            match = _SECTION.match(line)
            if match:
                current = {
                    "name": match.group(1).strip(),
                    "type": _section_type(match.group(1).strip()),
                    "start_line": line_number,
                    "lines": [],
                }
                sections.append(current)
                if match.group(2).strip():
                    current["lines"].append(match.group(2))
                continue
            if current is None:
                current = {"name": "Conteúdo", "type": "content", "start_line": 1, "lines": []}
                sections.append(current)
            current["lines"].append(line)
        return sections

    @staticmethod
    def _extract_tabs(lines: list[str]) -> list[dict[str, Any]]:
        tabs: list[dict[str, Any]] = []
        active: Optional[dict[str, Any]] = None
        for line_number, line in enumerate(lines, start=1):
            if _TAB_LINE.match(line):
                if active is None:
                    active = {"instrument": "guitar", "start_line": line_number, "lines": []}
                    tabs.append(active)
                active["lines"].append(line)
            else:
                active = None
        return tabs

    @staticmethod
    def _extract_music_settings(soup: BeautifulSoup, primary: dict[str, Any]) -> dict[str, Any]:
        container = soup.select_one(".cifra") or soup
        key = container.get("data-key") if isinstance(container, Tag) else None
        tuning = container.get("data-tuning") if isinstance(container, Tag) else None
        capo = container.get("data-capo") if isinstance(container, Tag) else None
        tempo = container.get("data-tempo") if isinstance(container, Tag) else None
        signature = container.get("data-time-signature") if isinstance(container, Tag) else None

        music = primary.get("musicDetails") if isinstance(primary, dict) else None
        if isinstance(music, dict):
            key = key or music.get("musicalKey")
        tuning_values: Optional[list[str]] = None
        if tuning:
            if isinstance(tuning, str):
                tuning_values = [part.strip() for part in re.split(r"[,\s]+", tuning) if part.strip()]
            elif isinstance(tuning, list):
                tuning_values = tuning
        return {
            "key": key or None,
            "tuning": tuning_values,
            "capo": _to_number(capo),
            "tempo": _to_number(tempo),
            "time_signature": signature or None,
        }


def _to_number(value: Any) -> Optional[int | float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


class CifraClub:
    """Fetches and parses a song page only when the caller enabled authorized use."""

    _rate_lock = threading.Lock()
    _last_request_at = 0.0

    def __init__(self, settings: Optional[ScraperSettings] = None, parser: Optional[CifraClubParser] = None):
        self.settings = settings or ScraperSettings.from_env()
        self.parser = parser or CifraClubParser()

    @staticmethod
    def build_url(artist: str, song: str) -> str:
        artist_slug = quote(artist.strip().strip("/"), safe="-")
        song_slug = quote(song.strip().strip("/"), safe="-")
        if not artist_slug or not song_slug:
            raise ValueError("Artista e música são obrigatórios.")
        return f"{CIFRACLUB_URL}{artist_slug}/{song_slug}/"

    def cifra(self, artist: str, song: str) -> dict[str, Any]:
        url = self.build_url(artist, song)
        html = self._fetch(url)
        result = self.parser.parse(html, source_url=url)
        if result["extraction"]["status"] != "success":
            if self.settings.browser_fallback and self.settings.selenium_remote_url:
                browser_html = self._fetch_with_browser(url)
                browser_result = self.parser.parse(browser_html, source_url=url)
                if browser_result["extraction"]["status"] == "success":
                    browser_result["extraction"]["method"] = "selenium_fallback"
                    return browser_result
            raise ExtractionError("A página foi obtida, mas não foi possível extrair uma cifra utilizável.")
        return result

    def _assert_fetch_allowed(self) -> None:
        if not (self.settings.fetch_enabled and self.settings.fetch_authorized):
            raise ProviderAccessDisabled(
                "A coleta remota está desativada. Habilite-a somente se você tiver autorização "
                "aplicável do provedor para realizar extração automatizada. Consulte os termos do Cifra Club."
            )

    def _throttle(self) -> None:
        interval = self.settings.min_request_interval
        if interval <= 0:
            return
        with self._rate_lock:
            now = time.monotonic()
            wait = interval - (now - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            type(self)._last_request_at = time.monotonic()

    @staticmethod
    def _response_diagnostics(response: httpx.Response) -> dict[str, Any]:
        """Return safe response metadata without exposing cookies, tokens, or query strings."""
        parsed = urlparse(str(response.url))
        headers = response.headers
        diagnostics: dict[str, Any] = {
            "status_code": response.status_code,
            "host": parsed.hostname,
            "path": parsed.path,
            "content_type": headers.get("content-type"),
            "server": headers.get("server"),
            "via": headers.get("via"),
            "request_id": (
                headers.get("x-request-id")
                or headers.get("request-id")
                or headers.get("x-correlation-id")
            ),
            "cf_ray": headers.get("cf-ray"),
            "retry_after": headers.get("retry-after"),
        }
        return {key: value for key, value in diagnostics.items() if value is not None}

    def _fetch(self, url: str) -> str:
        self._assert_fetch_allowed()
        last_error: Optional[Exception] = None
        headers = {
            "User-Agent": "CifraClubAPI/2.0 (+https://github.com/code4music/cifraclub-api; authorized-access-only)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
        }
        with httpx.Client(timeout=self.settings.timeout_seconds, follow_redirects=False, headers=headers) as client:
            for attempt in range(self.settings.max_retries + 1):
                try:
                    current_url = url
                    for redirect_number in range(4):
                        self._throttle()
                        with client.stream("GET", current_url) as response:
                            if response.status_code in (301, 302, 303, 307, 308):
                                if redirect_number >= 3:
                                    raise UpstreamError("A origem excedeu o limite de redirecionamentos.")
                                location = response.headers.get("location")
                                if not location:
                                    raise UpstreamError("A origem enviou um redirecionamento sem destino.")
                                next_url = urljoin(str(response.url), location)
                                next_parsed = urlparse(next_url)
                                next_host = (next_parsed.hostname or "").lower()
                                allowed_host = next_host == "cifraclub.com.br" or next_host.endswith(".cifraclub.com.br")
                                if next_parsed.scheme != "https" or not allowed_host:
                                    raise UpstreamError("A origem tentou redirecionar para um domínio não permitido.")
                                current_url = next_url
                                continue

                            final_host = (response.url.host or "").lower()
                            allowed_host = final_host == "cifraclub.com.br" or final_host.endswith(".cifraclub.com.br")
                            if response.url.scheme != "https" or not allowed_host:
                                raise UpstreamError("A origem respondeu por um domínio não permitido.")
                            if response.status_code == 404:
                                raise SongNotFound("A música não foi encontrada na origem.")
                            if response.status_code in (401, 403, 451):
                                diagnostics = self._response_diagnostics(response)
                                raise UpstreamError(
                                    f"A origem recusou a solicitação (HTTP {response.status_code}). "
                                    "A API interrompeu novas tentativas e não acionou fallback de navegador.",
                                    diagnostics=diagnostics,
                                )
                            if response.status_code == 429:
                                diagnostics = self._response_diagnostics(response)
                                raise UpstreamError(
                                    "A origem aplicou limite de requisições (HTTP 429).",
                                    diagnostics=diagnostics,
                                )
                            if response.status_code >= 500:
                                diagnostics = self._response_diagnostics(response)
                                raise UpstreamError(
                                    f"A origem apresentou erro HTTP {response.status_code}.",
                                    diagnostics=diagnostics,
                                )
                            response.raise_for_status()
                            chunks: list[bytes] = []
                            size = 0
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > MAX_HTML_BYTES:
                                    raise UpstreamError("A resposta da origem ultrapassou o limite de 2 MiB.")
                                chunks.append(chunk)
                            encoding = response.encoding or "utf-8"
                            return b"".join(chunks).decode(encoding, errors="replace")
                    raise UpstreamError("A origem excedeu o limite de redirecionamentos.")
                except (SongNotFound, ProviderAccessDisabled):
                    raise
                except (httpx.HTTPError, UpstreamError) as exc:
                    last_error = exc
                    # Explicit denials and rate limits are terminal. Do not retry
                    # them or switch to a browser-based request path.
                    if isinstance(exc, UpstreamError) and exc.diagnostics:
                        if exc.diagnostics.get("status_code") in (401, 403, 429, 451):
                            raise
                    if attempt >= self.settings.max_retries:
                        break
                    time.sleep(min(2 ** attempt, 4))
        if isinstance(last_error, UpstreamError) and last_error.diagnostics:
            raise last_error
        if self.settings.browser_fallback and self.settings.selenium_remote_url:
            return self._fetch_with_browser(url)
        raise UpstreamError(f"Não foi possível obter a página: {last_error or 'erro desconhecido'}")

    def _fetch_with_browser(self, url: str) -> str:
        self._assert_fetch_allowed()
        try:
            from selenium import webdriver
            from selenium.common.exceptions import TimeoutException
            from selenium.webdriver.common.by import By
            from selenium.webdriver.firefox.options import Options
            from selenium.webdriver.support.ui import WebDriverWait
        except ImportError as exc:  # pragma: no cover - dependency-specific
            raise UpstreamError("Fallback Selenium solicitado, mas a dependência não está instalada.") from exc

        self._throttle()
        driver = None
        try:
            options = Options()
            options.add_argument("-headless")
            driver = webdriver.Remote(command_executor=self.settings.selenium_remote_url, options=options)
            driver.set_page_load_timeout(self.settings.timeout_seconds)
            try:
                driver.get(url)
            except TimeoutException:
                # Keep any document already rendered; the parser will validate it.
                pass
            try:
                WebDriverWait(driver, min(self.settings.timeout_seconds, 10)).until(
                    lambda current: current.find_elements(By.CSS_SELECTOR, ".cifra_cnt pre, pre")
                )
            except TimeoutException:
                pass
            page_source = driver.page_source
            if len(page_source.encode("utf-8")) > MAX_HTML_BYTES:
                raise UpstreamError("A página renderizada ultrapassou o limite de 2 MiB.")
            return page_source
        except Exception as exc:
            raise UpstreamError(f"Fallback de navegador falhou: {exc}") from exc
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:  # A failed cleanup must not mask the extraction result.
                    pass
