"""
Módulo para buscar imagens de preview no Fab.com (marketplace Epic Games).
Estratégias em ordem: API interna → HTML/Next.js → img tags → meta tags.
"""
from __future__ import annotations

import io
import json
import logging
import re
from typing import Optional

import httpx

LOG = logging.getLogger("fab_scraper")

FAB_SEARCH_URL = "https://www.fab.com/search"
FAB_API_URLS = [
    "https://www.fab.com/i/listings",
    "https://www.fab.com/api/v1/listings",
    "https://www.fab.com/i/search/listings",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.fab.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# Extensões de imagem aceitas (com ou sem query string)
_IMG_EXT = re.compile(r"\.(jpg|jpeg|png|webp)(\?[^\s\"'<>]*)?$", re.IGNORECASE)

# CDNs conhecidos do Fab/Epic
_CDN_HOSTS = (
    "cdn1.epicgames.com",
    "cdn2.epicgames.com",
    "media.fab.com",
    "cdn.fab.com",
    "fab-assets.com",
    "epicgames-gameinfo.s3.amazonaws.com",
)

# Regex genérico para qualquer URL de imagem CDN no HTML
_IMG_URL_RE = re.compile(
    r'https?://(?:'
    + '|'.join(re.escape(h) for h in _CDN_HOSTS)
    + r')[^\s"\'<>\\]+',
    re.IGNORECASE,
)

# Regex mais amplo: qualquer src/href de imagem que pareça CDN ou asset
_ANY_IMG_RE = re.compile(
    r'(?:src|href|data-src|data-lazy-src|content)=["\']'
    r'(https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s"\'<>]*)?)["\']',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Estratégia 1 – API JSON interna (tenta vários endpoints)
# ---------------------------------------------------------------------------

async def _try_api(client: httpx.AsyncClient, query: str, count: int) -> list[str]:
    params = {"q": query, "sort_by": "relevance", "status": "published", "limit": count * 4}
    api_headers = {**_HEADERS, "Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}

    for api_url in FAB_API_URLS:
        try:
            resp = await client.get(api_url, params=params, headers=api_headers, timeout=12)
            if resp.status_code != 200:
                continue
            data = resp.json()
            urls = _extract_urls_from_api(data, count)
            if urls:
                LOG.info("[fab_api] %d URL(s) via %s", len(urls), api_url)
                return urls
        except Exception as exc:
            LOG.debug("[fab_api] %s → %s", api_url, exc)

    return []


def _extract_urls_from_api(data: dict, count: int) -> list[str]:
    urls: list[str] = []
    results = data.get("results") or data.get("listings") or data.get("data") or []
    if isinstance(data, list):
        results = data

    for item in results:
        if not isinstance(item, dict):
            continue
        url = _extract_thumbnail_from_item(item)
        if url and url not in urls:
            urls.append(url)
        if len(urls) >= count:
            break
    return urls


def _extract_thumbnail_from_item(item: dict) -> Optional[str]:
    # Chaves diretas comuns
    for key in (
        "thumbnailUrl", "thumbnail_url", "coverImageUrl", "cover_image_url",
        "coverImage", "cover_image", "previewUrl", "preview_url",
        "imageUrl", "image_url", "thumbnail", "image",
    ):
        val = item.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val

    # Campo 'images' como lista
    for key in ("images", "thumbnails", "media", "assets"):
        val = item.get(key)
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, str) and first.startswith("http"):
                return first
            if isinstance(first, dict):
                for k in ("url", "src", "href", "uri"):
                    u = first.get(k)
                    if isinstance(u, str) and u.startswith("http"):
                        return u
        elif isinstance(val, dict):
            for k in ("url", "src", "href", "uri"):
                u = val.get(k)
                if isinstance(u, str) and u.startswith("http"):
                    return u

    return None


# ---------------------------------------------------------------------------
# Estratégia 2 – Scraping HTML da página de busca
# ---------------------------------------------------------------------------

async def _try_html(client: httpx.AsyncClient, query: str, count: int) -> list[str]:
    try:
        resp = await client.get(
            FAB_SEARCH_URL,
            params={"q": query},
            headers=_HEADERS,
            timeout=14,
        )
        LOG.debug("[fab_html] GET %s → %d", resp.url, resp.status_code)
        if resp.status_code != 200:
            return []
        html = resp.text
    except Exception as exc:
        LOG.debug("[fab_html] request failed: %s", exc)
        return []

    found: list[str] = []

    # 2a) __NEXT_DATA__ (Next.js)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            nd = json.loads(m.group(1))
            found.extend(_walk_for_images(nd))
        except Exception:
            pass

    # 2b) JSON-LD
    for jld_raw in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            jld = json.loads(jld_raw)
            found.extend(_walk_for_images(jld))
        except Exception:
            pass

    # 2c) CDN URLs conhecidas
    found.extend(u for u in _IMG_URL_RE.findall(html) if _is_image_url(u))

    # 2d) qualquer src/content de imagem no HTML
    found.extend(_ANY_IMG_RE.findall(html))

    # 2e) meta og:image (geralmente thumbnail do primeiro resultado)
    for og in re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](https?://[^"\']+)["\']', html):
        found.append(og)
    for og in re.findall(r'<meta[^>]+content=["\'](https?://[^"\']+)["\'][^>]+property=["\']og:image["\']', html):
        found.append(og)

    unique = _dedup([u for u in found if _is_image_url(u)])
    LOG.debug("[fab_html] %d URL(s) candidatas para '%s'", len(unique), query)
    return unique[:count]


def _walk_for_images(obj, depth: int = 0, found: Optional[list] = None) -> list[str]:
    if found is None:
        found = []
    if depth > 15 or len(found) >= 20:
        return found
    if isinstance(obj, dict):
        for key in (
            "thumbnailUrl", "thumbnail_url", "imageUrl", "image_url",
            "coverImage", "cover_image", "previewUrl", "url", "src",
            "image", "thumbnail", "backgroundImage", "cardImage",
        ):
            val = obj.get(key)
            if isinstance(val, str) and _is_image_url(val):
                found.append(val)
        for val in obj.values():
            if isinstance(val, (dict, list)):
                _walk_for_images(val, depth + 1, found)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _walk_for_images(item, depth + 1, found)
    return found


def _is_image_url(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    # Aceita qualquer extensão de imagem, com ou sem query string
    path = url.split("?")[0]
    return bool(_IMG_EXT.search(path))


def _dedup(lst: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in lst:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ---------------------------------------------------------------------------
# Download das imagens
# ---------------------------------------------------------------------------

async def _download_images(client: httpx.AsyncClient, urls: list[str]) -> list[bytes]:
    result: list[bytes] = []
    for url in urls:
        try:
            resp = await client.get(url, headers=_HEADERS, timeout=12, follow_redirects=True)
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and ("image/" in ct or len(resp.content) > 5000):
                result.append(resp.content)
                LOG.debug("[fab_dl] ✅ %s (%d bytes)", url[:80], len(resp.content))
            else:
                LOG.debug("[fab_dl] ❌ %s → status=%d ct=%s", url[:80], resp.status_code, ct)
        except Exception as exc:
            LOG.debug("[fab_dl] erro %s: %s", url[:80], exc)
    return result


# ---------------------------------------------------------------------------
# Função pública principal
# ---------------------------------------------------------------------------

async def fetch_fab_images(pack_title: str, count: int = 3) -> list[bytes]:
    """
    Busca até `count` imagens de preview no Fab.com para o título do pack.
    Retorna lista de bytes (prontos para InputMediaPhoto via io.BytesIO).
    Nunca levanta exceção – retorna lista vazia em caso de falha.
    """
    query = pack_title.strip()
    if not query:
        return []

    LOG.info("[fab] Buscando '%s' (count=%d)", query, count)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            # Tenta API primeiro
            urls = await _try_api(client, query, count)

            # Fallback: scraping HTML
            if not urls:
                urls = await _try_html(client, query, count)

            if not urls:
                LOG.info("[fab] Nenhuma URL de imagem encontrada para '%s'", query)
                return []

            LOG.info("[fab] %d URL(s) candidata(s) para '%s', baixando...", len(urls), query)
            images = await _download_images(client, urls[:count])
            LOG.info("[fab] %d imagem(ns) baixada(s) para '%s'", len(images), query)
            return images

    except Exception as exc:
        LOG.warning("[fab] Erro inesperado para '%s': %s", query, exc)
        return []


def to_input_media(image_bytes: bytes) -> "InputMediaPhoto":  # type: ignore[name-defined]
    """Converte bytes em InputMediaPhoto."""
    from telegram import InputMediaPhoto
    return InputMediaPhoto(media=io.BytesIO(image_bytes))
