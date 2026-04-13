"""
Módulo para buscar imagens de preview no Fab.com (marketplace Epic Games).
Tenta a API interna do Fab.com e faz fallback para scraping HTML.
"""
from __future__ import annotations

import io
import json
import logging
import re
import urllib.parse
from typing import Optional

import httpx

LOG = logging.getLogger("fab_scraper")

FAB_API_URL    = "https://www.fab.com/i/listings"
FAB_SEARCH_URL = "https://www.fab.com/search"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.fab.com/",
}

# Extensões de imagem aceitas
_IMG_EXT = re.compile(r"\.(jpg|jpeg|png|webp)(\?.*)?$", re.IGNORECASE)

# Padrões de CDN que o Fab/Epic usam
_CDN_PATTERNS = [
    re.compile(r'https://cdn1\.epicgames\.com/[^\s"\'<>]+', re.IGNORECASE),
    re.compile(r'https://media\.fab\.com/[^\s"\'<>]+',      re.IGNORECASE),
    re.compile(r'https://cdn\.fab\.com/[^\s"\'<>]+',        re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Estratégia 1 – API JSON interna
# ---------------------------------------------------------------------------

async def _try_api(client: httpx.AsyncClient, query: str, count: int) -> list[str]:
    try:
        resp = await client.get(
            FAB_API_URL,
            params={"q": query, "sort_by": "relevance", "status": "published", "limit": count * 3},
            headers={**_HEADERS, "Accept": "application/json"},
            timeout=12,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        urls: list[str] = []
        for item in data.get("results", []):
            url = _extract_thumbnail(item)
            if url:
                urls.append(url)
            if len(urls) >= count:
                break
        return urls
    except Exception as exc:
        LOG.debug("[fab_api] %s", exc)
        return []


def _extract_thumbnail(item: dict) -> Optional[str]:
    """Extrai a URL do thumbnail de um item de listagem do Fab."""
    for key in ("thumbnailUrl", "thumbnail_url", "coverImage", "cover_image", "previewUrl"):
        val = item.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val

    # Campo thumbnails pode ser dict com lista de imagens
    thumbs = item.get("thumbnails") or item.get("thumbnail") or {}
    if isinstance(thumbs, dict):
        for sub in ("images", "data", "items"):
            imgs = thumbs.get(sub) or []
            for img in imgs:
                if isinstance(img, dict):
                    url = img.get("url") or img.get("src")
                elif isinstance(img, str):
                    url = img
                else:
                    continue
                if url and url.startswith("http"):
                    return url
    elif isinstance(thumbs, str) and thumbs.startswith("http"):
        return thumbs

    return None


# ---------------------------------------------------------------------------
# Estratégia 2 – Scraping HTML
# ---------------------------------------------------------------------------

async def _try_html(client: httpx.AsyncClient, query: str, count: int) -> list[str]:
    try:
        resp = await client.get(
            FAB_SEARCH_URL,
            params={"q": query},
            headers={**_HEADERS, "Accept": "text/html,*/*"},
            timeout=12,
        )
        if resp.status_code != 200:
            return []
        html = resp.text

        # Tenta __NEXT_DATA__ (Next.js)
        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if m:
            try:
                next_data = json.loads(m.group(1))
                urls = _walk_next_data(next_data)
                if urls:
                    return _dedup(urls)[:count]
            except Exception:
                pass

        # Fallback: regex nas CDNs conhecidas
        found: list[str] = []
        for pat in _CDN_PATTERNS:
            for raw_url in pat.findall(html):
                # Mantém somente URLs de imagens
                if _IMG_EXT.search(raw_url.split("?")[0]):
                    found.append(raw_url)
        return _dedup(found)[:count]

    except Exception as exc:
        LOG.debug("[fab_html] %s", exc)
        return []


def _walk_next_data(obj, depth: int = 0, found: Optional[list] = None) -> list[str]:
    """Percorre recursivamente o __NEXT_DATA__ procurando URLs de imagens."""
    if found is None:
        found = []
    if depth > 12 or len(found) >= 15:
        return found
    if isinstance(obj, dict):
        for key in ("thumbnailUrl", "thumbnail_url", "imageUrl", "image_url",
                    "coverImage", "cover_image", "previewUrl", "url", "src"):
            val = obj.get(key)
            if isinstance(val, str) and val.startswith("http") and _IMG_EXT.search(val.split("?")[0]):
                found.append(val)
        for val in obj.values():
            _walk_next_data(val, depth + 1, found)
    elif isinstance(obj, list):
        for item in obj:
            _walk_next_data(item, depth + 1, found)
    return found


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
            resp = await client.get(url, headers=_HEADERS, timeout=12)
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and ct.startswith("image/"):
                result.append(resp.content)
        except Exception as exc:
            LOG.debug("[fab_download] %s – %s", url, exc)
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

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            urls = await _try_api(client, query, count)
            if not urls:
                urls = await _try_html(client, query, count)

            if not urls:
                LOG.info("[fab] Nenhuma imagem encontrada para '%s'", query)
                return []

            images = await _download_images(client, urls[:count])
            LOG.info("[fab] %d imagem(ns) obtida(s) para '%s'", len(images), query)
            return images

    except Exception as exc:
        LOG.warning("[fab] Erro inesperado para '%s': %s", query, exc)
        return []


def to_input_media(image_bytes: bytes) -> "InputMediaPhoto":  # type: ignore[name-defined]
    """Converte bytes em InputMediaPhoto (importa de telegram para evitar import circular)."""
    from telegram import InputMediaPhoto
    return InputMediaPhoto(media=io.BytesIO(image_bytes))
