"""
Módulo para buscar imagens de preview no Fab.com.
Estratégias em ordem:
  1. API interna do Fab.com (JSON direto, sem Cloudflare nos endpoints de API)
  2. Bing Image Search (extrai murl de blobs JSON + regex CDN)
  3. DuckDuckGo Images (VQD token + endpoint JSON estruturado)
"""
from __future__ import annotations

import io
import json
import logging
import re
import urllib.parse
from html import unescape

import httpx

LOG = logging.getLogger("fab_scraper")

_BING_IMG = "https://www.bing.com/images/search"
_FAB_SEARCH = "https://www.fab.com/i/listings/search"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)

_HEADERS_BROWSER = {
    "User-Agent": _UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.bing.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

_HEADERS_JSON = {
    "User-Agent": _UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.fab.com/",
}

# CDN URLs do Epic/Fab/UE (sem Cloudflare, acesso direto)
_CDN_PAT = re.compile(
    r"https?://(?:"
    r"cdn\d*\.epicgames\.com"
    r"|(?:media|cdn|static|images|assets|cdn-1|cdn-2)\.fab\.com"
    r"|fab\.com/cdn-cgi"
    r"|(?:cdn|images|assets|static)\.unrealengine\.com"
    r"|(?:cdn|media|static)\.fabstatic\.com"
    r"|(?:cdn|media)\.unrealassets\.com"
    r")"
    r"[^\x00-\x20\"'<>\\]+",
    re.IGNORECASE,
)
_IMG_EXT = re.compile(r"\.(jpg|jpeg|png|webp)", re.IGNORECASE)
_HASH_SUFFIX = re.compile(r"-[0-9a-f]{8,}$", re.IGNORECASE)

# murl/iurl em blobs JSON embutidos no Bing
_MURL_PAT = re.compile(r'"(?:murl|iurl)"\s*:\s*"(https?://[^"\\]+)"')

# VQD token do DuckDuckGo
_VQD_PAT = re.compile(r'vqd=([^&"\']+)')


def _canonical_key(url: str) -> str:
    path = url.split("?")[0].lower()
    path = _HASH_SUFFIX.sub("", path)
    return path


def _score_url(url: str) -> int:
    """
    4 = Screenshot 1920x1080
    3 = media.fab.com
    2 = featured
    1 = outro CDN válido
    0 = thumbnail (descartar)
    """
    u = url.lower()
    if "/thumbnail/" in u or "thumb-284x284" in u or "284x284" in u or "_thumb-" in u:
        return 0
    if "/screenshot/" in u and "1920x1080" in u:
        return 4
    if "media.fab.com" in u:
        return 3
    if "/featured/" in u:
        return 2
    return 1


def _rank_urls(candidates: list[str]) -> list[str]:
    seen: set[str] = set()
    ranked: list[tuple[int, str]] = []
    for url in candidates:
        if not _IMG_EXT.search(url):
            continue
        if not _CDN_PAT.match(url):
            LOG.info("[fab_filter] URL rejeitada (domínio fora do CDN): %s", url[:120])
            continue
        score = _score_url(url)
        if score == 0:
            LOG.debug("[fab_filter] URL rejeitada (thumbnail): %s", url[:120])
            continue
        key = _canonical_key(url)
        if key in seen:
            continue
        seen.add(key)
        ranked.append((score, url))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [u for _, u in ranked]


# ---------------------------------------------------------------------------
# Estratégia 1: API interna do Fab.com
# ---------------------------------------------------------------------------

async def _search_fab_api(client: httpx.AsyncClient, query: str) -> list[str]:
    """Tenta buscar imagens direto na API do Fab.com (retorna JSON)."""
    try:
        resp = await client.get(
            _FAB_SEARCH,
            params={"q": query, "product_type": "unreal_engine", "page_size": "6"},
            headers=_HEADERS_JSON,
            timeout=12,
        )
        if resp.status_code != 200:
            LOG.debug("[fab_api] Status %d para '%s'", resp.status_code, query)
            return []

        data = resp.json()
        urls: list[str] = []

        for listing in data.get("results", []):
            # Tenta campos comuns de imagem na resposta JSON
            for field in ("images", "gallery", "screenshots", "media"):
                for img in listing.get(field, []):
                    if isinstance(img, dict):
                        url = img.get("url") or img.get("src") or img.get("original") or ""
                    else:
                        url = str(img)
                    if url and _IMG_EXT.search(url):
                        urls.append(url)
            # thumbnail de preview direto no listing
            for field in ("thumbnail", "preview_image", "cover_image"):
                url = listing.get(field) or ""
                if isinstance(url, dict):
                    url = url.get("url", "")
                if url and _IMG_EXT.search(url):
                    urls.append(url)

        LOG.info("[fab_api] %d URL(s) brutas para '%s'", len(urls), query)
        return urls

    except Exception as exc:
        LOG.debug("[fab_api] Erro para '%s': %s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Estratégia 2: Bing Image Search
# ---------------------------------------------------------------------------

def _extract_bing_urls(html_text: str) -> list[str]:
    unescaped = unescape(html_text)
    candidates: list[str] = []

    # murl/iurl em JSON embutido
    for m in _MURL_PAT.finditer(unescaped):
        url = m.group(1).replace("\\/", "/")
        candidates.append(url)

    # Padrão CDN direto no HTML
    candidates.extend(_CDN_PAT.findall(unescaped))

    # URL-decode + CDN
    url_decoded = urllib.parse.unquote(unescaped)
    if url_decoded != unescaped:
        candidates.extend(_CDN_PAT.findall(url_decoded))

    return candidates


async def _bing_img_query(client: httpx.AsyncClient, query: str, count: int, label: str) -> list[str]:
    """Executa uma query no Bing Images e retorna URLs CDN rankeadas."""
    try:
        resp = await client.get(
            _BING_IMG,
            params={"q": query, "count": count * 8, "first": 1},
            headers=_HEADERS_BROWSER,
            timeout=18,
        )
        if resp.status_code != 200:
            LOG.debug("[fab_bing/%s] Status %d", label, resp.status_code)
            return []
        raw = _extract_bing_urls(resp.text)
        urls = _rank_urls(raw)
        LOG.info("[fab_bing/%s] %d URL(s) CDN (brutas=%d) para '%s'", label, len(urls), len(raw), query)
        return urls
    except Exception as exc:
        LOG.debug("[fab_bing/%s] Erro: %s", label, exc)
        return []


async def _search_bing_web(client: httpx.AsyncClient, query: str) -> list[str]:
    """Busca no Bing Web (não imagem) por site:fab.com e extrai CDN URLs dos snippets."""
    try:
        resp = await client.get(
            "https://www.bing.com/search",
            params={"q": f'site:fab.com "{query}"', "count": 5},
            headers=_HEADERS_BROWSER,
            timeout=15,
        )
        if resp.status_code != 200:
            LOG.debug("[fab_web] Status %d", resp.status_code)
            return []
        raw = _extract_bing_urls(resp.text)
        urls = _rank_urls(raw)
        LOG.info("[fab_web] %d URL(s) CDN para '%s'", len(urls), query)
        return urls
    except Exception as exc:
        LOG.debug("[fab_web] Erro: %s", exc)
        return []


async def _search_bing(client: httpx.AsyncClient, query: str, count: int) -> list[str]:
    """Tenta várias queries no Bing até encontrar URLs do CDN Fab/Epic."""

    # 1. Query genérica
    urls = await _bing_img_query(client, f"fab.com {query} unreal engine", count, "geral")
    if urls:
        return urls

    # 2. Restrito ao CDN media.fab.com
    urls = await _bing_img_query(client, f'site:media.fab.com "{query}"', count, "media.fab")
    if urls:
        return urls

    # 3. Restrito ao CDN Epic Games
    urls = await _bing_img_query(client, f'site:cdn1.epicgames.com "{query}"', count, "cdn.epic")
    if urls:
        return urls

    # 4. Bing Web Search — extrai og:images dos snippets de resultados do fab.com
    urls = await _search_bing_web(client, query)
    if urls:
        return urls

    # Log snippet da última resposta para diagnóstico
    LOG.info("[fab_bing] Nenhuma URL CDN encontrada para '%s'", query)
    return []


# ---------------------------------------------------------------------------
# Estratégia 3: DuckDuckGo Images (VQD token + endpoint JSON)
# ---------------------------------------------------------------------------

async def _search_ddg(client: httpx.AsyncClient, query: str, count: int) -> list[str]:
    """DuckDuckGo Images com VQD token para obter JSON estruturado com URLs reais."""
    ddg_query = f"fab.com {query} unreal engine"

    # Passo 1: obter VQD token
    try:
        resp = await client.get(
            "https://duckduckgo.com/",
            params={"q": ddg_query, "iax": "images", "ia": "images"},
            headers={**_HEADERS_BROWSER, "Referer": "https://duckduckgo.com/"},
            timeout=12,
        )
        vqd_m = _VQD_PAT.search(resp.text)
        if not vqd_m:
            LOG.debug("[fab_ddg] VQD não encontrado para '%s'", query)
            return []
        vqd = vqd_m.group(1)
    except Exception as exc:
        LOG.debug("[fab_ddg] Erro VQD para '%s': %s", query, exc)
        return []

    # Passo 2: buscar imagens via endpoint JSON com o VQD
    try:
        resp = await client.get(
            "https://duckduckgo.com/i.js",
            params={"q": ddg_query, "vqd": vqd, "o": "json", "f": ",,,,,", "p": "1"},
            headers={
                "User-Agent": _UA,
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "Referer": "https://duckduckgo.com/",
            },
            timeout=12,
        )
        if resp.status_code != 200:
            LOG.debug("[fab_ddg] JSON status %d para '%s'", resp.status_code, query)
            return []

        data = resp.json()
        candidates = [
            r.get("image", "")
            for r in data.get("results", [])
            if r.get("image")
        ]
        urls = _rank_urls(candidates)
        LOG.info("[fab_ddg] %d URL(s) CDN para '%s'", len(urls), query)
        return urls

    except Exception as exc:
        LOG.debug("[fab_ddg] Erro JSON para '%s': %s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

async def _download_images(client: httpx.AsyncClient, urls: list[str], count: int) -> list[bytes]:
    result: list[bytes] = []
    for url in urls:
        if len(result) >= count:
            break
        clean_url = url.split("?")[0]
        try:
            resp = await client.get(
                clean_url,
                headers={"User-Agent": _UA},
                timeout=15,
                follow_redirects=True,
            )
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and ("image/" in ct or len(resp.content) > 5000):
                result.append(resp.content)
                LOG.debug("[fab_dl] OK %s (%d bytes)", clean_url[:80], len(resp.content))
            else:
                LOG.debug("[fab_dl] SKIP %s → %d %s", clean_url[:80], resp.status_code, ct)
        except Exception as exc:
            LOG.debug("[fab_dl] Erro %s: %s", clean_url[:80], exc)
    return result


# ---------------------------------------------------------------------------
# Função pública principal
# ---------------------------------------------------------------------------

async def fetch_fab_images(pack_title: str, count: int = 3) -> list[bytes]:
    """
    Busca até `count` imagens de preview no Fab.com para o título do pack.
    Tenta: API Fab → Bing → DuckDuckGo.
    Retorna lista de bytes. Nunca levanta exceção.
    """
    query = pack_title.strip()
    if not query:
        return []

    LOG.info("[fab] Buscando '%s' (count=%d)", query, count)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:

            # 1. API do Fab.com
            urls = _rank_urls(await _search_fab_api(client, query))
            if urls:
                LOG.info("[fab] API Fab retornou %d URL(s)", len(urls))

            # 2. Bing
            if not urls:
                urls = await _search_bing(client, query, count)

            # 3. DuckDuckGo
            if not urls:
                LOG.info("[fab] Tentando DuckDuckGo para '%s'", query)
                urls = await _search_ddg(client, query, count)

            if not urls:
                LOG.info("[fab] Nenhuma URL encontrada para '%s'", query)
                return []

            LOG.info("[fab] %d candidata(s), baixando até %d...", len(urls), count)
            images = await _download_images(client, urls, count)
            LOG.info("[fab] %d imagem(ns) para '%s'", len(images), query)
            return images

    except Exception as exc:
        LOG.warning("[fab] Erro inesperado para '%s': %s", query, exc)
        return []


def to_input_media(image_bytes: bytes) -> "InputMediaPhoto":  # type: ignore[name-defined]
    from telegram import InputMediaPhoto
    return InputMediaPhoto(media=io.BytesIO(image_bytes))
