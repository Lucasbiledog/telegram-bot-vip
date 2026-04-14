"""
Módulo para buscar imagens de preview no Fab.com via Bing Image Search.
Fab.com está atrás de Cloudflare que bloqueia IPs de cloud (403).
Solução: buscar imagens via Bing Images (sem bloqueio) filtrando por
domínios CDN do Epic/Fab que são servidos diretamente sem proteção.
"""
from __future__ import annotations

import io
import logging
import re
from html import unescape
from typing import Optional

import httpx

LOG = logging.getLogger("fab_scraper")

# Bing Image Search URL
_BING_IMG = "https://www.bing.com/images/search"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# Padrão para URLs de CDN do Epic/Fab com extensão de imagem
_CDN_PAT = re.compile(
    r"https?://(?:cdn1\.epicgames\.com|cdn2\.epicgames\.com|media\.fab\.com|cdn\.fab\.com)"
    r"[^\x00-\x20\"'<>]+",
    re.IGNORECASE,
)
_IMG_EXT = re.compile(r"\.(jpg|jpeg|png|webp)", re.IGNORECASE)


def _extract_cdn_urls(html_text: str) -> list[str]:
    """Extrai URLs de CDN do Epic/Fab com extensão de imagem do HTML do Bing."""
    unescaped = unescape(html_text)
    all_urls = _CDN_PAT.findall(unescaped)
    # Filtra somente URLs com extensão de imagem na URL
    img_urls = [u for u in all_urls if _IMG_EXT.search(u)]
    # Deduplica mantendo ordem
    return list(dict.fromkeys(img_urls))


async def _search_bing(client: httpx.AsyncClient, query: str, count: int) -> list[str]:
    """Busca imagens no Bing e retorna URLs de CDN do Epic/Fab."""
    # Adiciona contexto 'unreal' para melhorar relevância dos resultados do Fab
    bing_query = f"fab.com {query} unreal"
    try:
        resp = await client.get(
            _BING_IMG,
            params={"q": bing_query, "count": count * 4},
            headers=_HEADERS,
            timeout=14,
        )
        LOG.debug("[fab_bing] GET %s → %d (len=%d)", resp.url, resp.status_code, len(resp.text))
        if resp.status_code != 200:
            LOG.warning("[fab_bing] Status %d para '%s'", resp.status_code, query)
            return []
        urls = _extract_cdn_urls(resp.text)
        LOG.info("[fab_bing] %d URL(s) CDN para '%s'", len(urls), query)
        return urls
    except Exception as exc:
        LOG.warning("[fab_bing] Erro para '%s': %s", query, exc)
        return []


async def _download_images(client: httpx.AsyncClient, urls: list[str], count: int) -> list[bytes]:
    """Baixa até `count` imagens das URLs fornecidas."""
    result: list[bytes] = []
    for url in urls:
        if len(result) >= count:
            break
        # Remove query string para evitar parâmetros de resize
        clean_url = url.split("?")[0]
        try:
            resp = await client.get(
                clean_url,
                headers={"User-Agent": _HEADERS["User-Agent"]},
                timeout=12,
                follow_redirects=True,
            )
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and ("image/" in ct or len(resp.content) > 5000):
                result.append(resp.content)
                LOG.debug("[fab_dl] OK %s (%d bytes)", clean_url[:80], len(resp.content))
            else:
                LOG.debug("[fab_dl] SKIP %s → status=%d ct=%s", clean_url[:80], resp.status_code, ct)
        except Exception as exc:
            LOG.debug("[fab_dl] Erro %s: %s", clean_url[:80], exc)
    return result


# ---------------------------------------------------------------------------
# Função pública principal
# ---------------------------------------------------------------------------

async def fetch_fab_images(pack_title: str, count: int = 3) -> list[bytes]:
    """
    Busca até `count` imagens de preview no Fab.com para o título do pack.
    Usa Bing Image Search para contornar o Cloudflare do Fab.com.
    Retorna lista de bytes. Nunca levanta exceção.
    """
    query = pack_title.strip()
    if not query:
        return []

    LOG.info("[fab] Buscando '%s' (count=%d)", query, count)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            urls = await _search_bing(client, query, count)

            if not urls:
                LOG.info("[fab] Nenhuma URL CDN encontrada para '%s'", query)
                return []

            LOG.info("[fab] %d URL(s) candidata(s), baixando até %d...", len(urls), count)
            images = await _download_images(client, urls, count)
            LOG.info("[fab] %d imagem(ns) baixada(s) para '%s'", len(images), query)
            return images

    except Exception as exc:
        LOG.warning("[fab] Erro inesperado para '%s': %s", query, exc)
        return []


def to_input_media(image_bytes: bytes) -> "InputMediaPhoto":  # type: ignore[name-defined]
    """Converte bytes em InputMediaPhoto."""
    from telegram import InputMediaPhoto
    return InputMediaPhoto(media=io.BytesIO(image_bytes))
