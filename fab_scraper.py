"""
Busca imagens de preview do Fab.com via mecanismos de busca.
Fab.com está atrás de Cloudflare que bloqueia IPs de cloud (403).
Solução: buscar imagens via Bing/Google, filtrar APENAS CDNs do Epic/Fab.
Nenhuma imagem de domínio externo passa pelo filtro — se não encontrar, retorna vazio.
"""
from __future__ import annotations

import io
import logging
import re
import urllib.parse
from html import unescape

import httpx

LOG = logging.getLogger("fab_scraper")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
}

# ---------------------------------------------------------------------------
# Filtro CDN — ÚNICA porta de entrada. Somente Epic/Fab CDN passa.
# ---------------------------------------------------------------------------
_CDN_PAT = re.compile(
    r"https?://(?:"
    r"cdn\d*\.epicgames\.com"
    r"|(?:media|cdn|static|images|assets|cdn-1|cdn-2)\.fab\.com"
    r"|(?:cdn|images|assets|static)\.unrealengine\.com"
    r"|(?:cdn|media|static)\.fabstatic\.com"
    r")"
    r"[^\x00-\x20\"'<>\\]+",
    re.IGNORECASE,
)
_IMG_EXT = re.compile(r"\.(jpg|jpeg|png|webp)", re.IGNORECASE)
_HASH_SUFFIX = re.compile(r"-[0-9a-f]{8,}$", re.IGNORECASE)

# Padrões para extrair URLs de blobs JSON (Bing/Google)
_MURL_PAT = re.compile(r'"(?:murl|iurl|ou|imgurl)"\s*:\s*"(https?://[^"\\]+)"')

# UUID do Fab.com para busca por listing direto
_FAB_UUID_PAT = re.compile(
    r"fab\.com/listings/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def _canonical_key(url: str) -> str:
    path = url.split("?")[0].lower()
    return _HASH_SUFFIX.sub("", path)


def _score_url(url: str) -> int:
    u = url.lower()
    if "/thumbnail/" in u or "284x284" in u or "_thumb-" in u:
        return 0  # descarta thumbnails
    if "/screenshot/" in u and "1920x1080" in u:
        return 4
    if "media.fab.com" in u:
        return 3
    if "/featured/" in u:
        return 2
    return 1


def _rank_urls(candidates: list[str]) -> list[str]:
    """
    Filtra e rankeia URLs. SÓ passa CDN do Epic/Fab — qualquer outro domínio
    é descartado silenciosamente, garantindo que imagens aleatórias nunca
    sejam enviadas.
    """
    seen: set[str] = set()
    ranked: list[tuple[int, str]] = []
    for url in candidates:
        if not _IMG_EXT.search(url):
            continue
        if not _CDN_PAT.match(url):
            LOG.debug("[fab_filter] rejeitado (domínio inválido): %s", url[:100])
            continue
        score = _score_url(url)
        if score == 0:
            continue
        key = _canonical_key(url)
        if key in seen:
            continue
        seen.add(key)
        ranked.append((score, url))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [u for _, u in ranked]


def _extract_urls_from_html(html: str) -> list[str]:
    """Extrai URLs candidatas do HTML via murl/JSON e regex CDN direto."""
    text = unescape(html)
    candidates: list[str] = []

    for m in _MURL_PAT.finditer(text):
        candidates.append(m.group(1).replace("\\/", "/"))

    candidates.extend(_CDN_PAT.findall(text))

    decoded = urllib.parse.unquote(text)
    if decoded != text:
        candidates.extend(_CDN_PAT.findall(decoded))

    return candidates


# ---------------------------------------------------------------------------
# Bing Image Search
# ---------------------------------------------------------------------------

async def _bing_search(client: httpx.AsyncClient, query: str, count: int) -> list[str]:
    headers = {**_HEADERS, "Referer": "https://www.bing.com/"}
    queries = [
        f"fab.com {query} unreal engine",
        f'"{query}" unreal engine marketplace',
    ]
    for q in queries:
        try:
            resp = await client.get(
                "https://www.bing.com/images/search",
                params={"q": q, "count": count * 8, "first": 1},
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            urls = _rank_urls(_extract_urls_from_html(resp.text))
            LOG.info("[fab_bing] %d URL(s) CDN para '%s'", len(urls), q)
            if urls:
                return urls
        except Exception as exc:
            LOG.debug("[fab_bing] Erro: %s", exc)
    return []


# ---------------------------------------------------------------------------
# Google Images
# ---------------------------------------------------------------------------

async def _google_search(client: httpx.AsyncClient, query: str, count: int) -> list[str]:
    headers = {**_HEADERS, "Referer": "https://www.google.com/"}
    queries = [
        f"fab.com {query} unreal engine",
        f'"{query}" unreal engine marketplace screenshot',
    ]
    for q in queries:
        try:
            resp = await client.get(
                "https://www.google.com/search",
                params={"q": q, "tbm": "isch", "num": count * 8},
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            urls = _rank_urls(_extract_urls_from_html(resp.text))
            LOG.info("[fab_google] %d URL(s) CDN para '%s'", len(urls), q)
            if urls:
                return urls
        except Exception as exc:
            LOG.debug("[fab_google] Erro: %s", exc)
    return []


# ---------------------------------------------------------------------------
# UUID → listing direto no Fab.com
# ---------------------------------------------------------------------------

async def _try_fab_listing(client: httpx.AsyncClient, query: str) -> list[str]:
    """Busca o UUID do produto via Bing web e tenta a API do Fab.com."""
    try:
        resp = await client.get(
            "https://www.bing.com/search",
            params={"q": f'site:fab.com/listings "{query}"', "count": 5},
            headers={**_HEADERS, "Referer": "https://www.bing.com/"},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        # tenta UUID direto e também em URL-decoded (Bing pode codificar)
        m = _FAB_UUID_PAT.search(resp.text) or _FAB_UUID_PAT.search(urllib.parse.unquote(resp.text))
        if not m:
            return []
        uid = m.group(1)
        LOG.info("[fab_uuid] UUID: %s", uid)
    except Exception:
        return []

    try:
        resp = await client.get(
            f"https://www.fab.com/i/listings/{uid}",
            headers={"User-Agent": _UA, "Accept": "application/json", "Referer": "https://www.fab.com/"},
            timeout=10,
        )
        if resp.status_code != 200:
            LOG.debug("[fab_listing] Status %d uid=%s", resp.status_code, uid)
            return []
        data = resp.json()
        urls: list[str] = []
        for field in ("images", "gallery", "screenshots", "media"):
            for img in data.get(field, []):
                url = (img.get("url") or img.get("src") or "") if isinstance(img, dict) else str(img)
                if url and _IMG_EXT.search(url):
                    urls.append(url)
        for field in ("thumbnail", "preview_image", "cover_image", "hero_image"):
            val = data.get(field) or ""
            url = val.get("url", "") if isinstance(val, dict) else str(val)
            if url and _IMG_EXT.search(url):
                urls.append(url)
        LOG.info("[fab_listing] %d URL(s) uid=%s", len(urls), uid)
        return urls
    except Exception as exc:
        LOG.debug("[fab_listing] Erro uid=%s: %s", uid, exc)
        return []


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

async def _download_images(client: httpx.AsyncClient, urls: list[str], count: int) -> list[bytes]:
    result: list[bytes] = []
    for url in urls:
        if len(result) >= count:
            break
        clean = url.split("?")[0]
        try:
            resp = await client.get(clean, headers={"User-Agent": _UA}, timeout=15, follow_redirects=True)
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and ("image/" in ct or len(resp.content) > 5000):
                result.append(resp.content)
                LOG.debug("[fab_dl] OK %s (%d bytes)", clean[:80], len(resp.content))
            else:
                LOG.debug("[fab_dl] SKIP %s → %d", clean[:80], resp.status_code)
        except Exception as exc:
            LOG.debug("[fab_dl] Erro %s: %s", clean[:80], exc)
    return result


# ---------------------------------------------------------------------------
# Função pública principal
# ---------------------------------------------------------------------------

async def fetch_fab_images(pack_title: str, count: int = 3) -> list[bytes]:
    """
    Busca até `count` imagens do Fab.com para o título do pack.
    Garante que SOMENTE imagens do CDN do Epic/Fab são retornadas.
    Se não encontrar nada, retorna lista vazia — nunca envia imagem errada.
    """
    query = pack_title.strip()
    if not query:
        return []

    LOG.info("[fab] Buscando '%s' (count=%d)", query, count)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:

            # 1. UUID → API direta do Fab.com
            urls = _rank_urls(await _try_fab_listing(client, query))
            if urls:
                LOG.info("[fab] listing direto: %d URL(s)", len(urls))

            # 2. Bing Images
            if not urls:
                urls = await _bing_search(client, query, count)

            # 3. Google Images
            if not urls:
                LOG.info("[fab] tentando Google para '%s'", query)
                urls = await _google_search(client, query, count)

            if not urls:
                LOG.info("[fab] nenhuma imagem do CDN encontrada para '%s'", query)
                return []

            images = await _download_images(client, urls, count)
            LOG.info("[fab] %d imagem(ns) baixada(s) para '%s'", len(images), query)
            return images

    except Exception as exc:
        LOG.warning("[fab] Erro inesperado para '%s': %s", query, exc)
        return []


def to_input_media(image_bytes: bytes) -> "InputMediaPhoto":  # type: ignore[name-defined]
    from telegram import InputMediaPhoto
    return InputMediaPhoto(media=io.BytesIO(image_bytes))
