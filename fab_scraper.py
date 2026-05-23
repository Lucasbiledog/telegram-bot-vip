"""
Módulo para buscar imagens de preview no Fab.com via Bing Image Search.
Fab.com está atrás de Cloudflare que bloqueia IPs de cloud (403).
Solução: buscar imagens via Bing Images, extrair URLs via JSON murl/imgurl
e como fallback via regex CDN, priorizar screenshots 1920x1080.
"""
from __future__ import annotations

import io
import logging
import re
from html import unescape

import httpx

LOG = logging.getLogger("fab_scraper")

_BING_IMG = "https://www.bing.com/images/search"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}

# Extração robusta: JSON murl/imgurl do Bing (mais estável que regex CDN)
_MURL_PAT = re.compile(r'"murl"\s*:\s*"(https?://[^"\\]+)"', re.IGNORECASE)
_IMGURL_PAT = re.compile(r'"imgurl"\s*:\s*"(https?://[^"\\]+)"', re.IGNORECASE)

# CDN URLs do Epic/Fab — lista expandida de domínios conhecidos
_CDN_PAT = re.compile(
    r"https?://(?:"
    r"cdn\d*\.epicgames\.com"
    r"|[a-z\-]*\.fab\.com"
    r"|fab\.com"
    r"|[a-z\-]*\.unrealengine\.com"
    r"|unrealengine\.com"
    r"|[a-z\-]*\.epicassets\.com"
    r"|epicassets\.com"
    r"|[a-z\-]*\.epicgamescontent\.com"
    r")[^\x00-\x20\"'<>\\]+",
    re.IGNORECASE,
)

_IMG_EXT = re.compile(r"\.(jpg|jpeg|png|webp)", re.IGNORECASE)

# Hash hexadecimal no final do nome de arquivo (para dedup canônico)
_HASH_SUFFIX = re.compile(r"-[0-9a-f]{8,}$", re.IGNORECASE)


def _canonical_key(url: str) -> str:
    """Chave de deduplicação: path sem hash e sem query string."""
    path = url.split("?")[0].lower()
    path = _HASH_SUFFIX.sub("", path)
    return path


def _score_url(url: str) -> int:
    """
    Pontuação de qualidade da URL:
      5 = Screenshot 1920x1080 no CDN Epic/Fab (melhor)
      4 = Qualquer imagem 1920x1080
      3 = media.fab.com / gallery image
      2 = Featured (894x488) ou imagem CDN Epic/Fab
      1 = outro CDN válido
      0 = Thumbnail pequeno (descartar)
    """
    u = url.lower()
    if "/thumbnail/" in u or "thumb-284x284" in u or "284x284" in u or "_thumb-" in u:
        return 0
    if "/screenshot/" in u and "1920x1080" in u:
        return 5
    if "1920x1080" in u:
        return 4
    if "media.fab.com" in u or "cdn.fab.com" in u:
        return 3
    if "/featured/" in u:
        return 2
    if any(d in u for d in ("epicgames.com", "fab.com", "unrealengine.com", "epicassets.com")):
        return 2
    return 1


def _extract_and_rank_urls(html_text: str) -> list[str]:
    """
    Extrai, deduplica e ordena URLs de imagem por qualidade.
    Estratégia 1: extrai murl/imgurl de JSON do Bing (mais robusto).
    Estratégia 2: regex direto de URLs CDN (fallback).
    """
    unescaped = unescape(html_text)

    seen_keys: set[str] = set()
    ranked: list[tuple[int, str]] = []

    def _add(url: str) -> None:
        if not _IMG_EXT.search(url):
            return
        score = _score_url(url)
        if score == 0:
            return
        key = _canonical_key(url)
        if key in seen_keys:
            return
        seen_keys.add(key)
        ranked.append((score, url))

    # Estratégia 1: JSON murl/imgurl (cobertura mais ampla de domínios)
    for pat in (_MURL_PAT, _IMGURL_PAT):
        for url in pat.findall(unescaped):
            _add(url)

    # Estratégia 2: CDN regex direto (fallback para formatos de página sem JSON)
    for url in _CDN_PAT.findall(unescaped):
        _add(url)

    ranked.sort(key=lambda x: x[0], reverse=True)
    LOG.debug("[fab_extract] %d URL(s) extraídas", len(ranked))
    return [url for _, url in ranked]


async def _search_bing(client: httpx.AsyncClient, query: str, count: int) -> list[str]:
    """Busca imagens no Bing e retorna URLs rankeadas por qualidade."""
    bing_query = f"fab.com {query} unreal engine"
    try:
        resp = await client.get(
            _BING_IMG,
            params={"q": bing_query, "count": count * 6},
            headers=_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            LOG.warning("[fab_bing] Status %d para '%s' — tentando query alternativa", resp.status_code, query)
            # Tenta query sem site: se o Bing bloqueou o site: operator
            resp2 = await client.get(
                _BING_IMG,
                params={"q": f"{query} unreal engine asset preview", "count": count * 6},
                headers=_HEADERS,
                timeout=15,
            )
            if resp2.status_code != 200:
                LOG.warning("[fab_bing] Status %d na query alternativa para '%s'", resp2.status_code, query)
                return []
            resp = resp2

        urls = _extract_and_rank_urls(resp.text)
        LOG.info("[fab_bing] %d URL(s) encontradas para '%s'", len(urls), query)

        if not urls:
            # Log de diagnóstico: mostra trecho do HTML para depuração
            snippet = resp.text[:500].replace("\n", " ")
            LOG.warning("[fab_bing] 0 URLs — snippet HTML: %s", snippet)

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
    Prioriza screenshots 1920x1080, descarta thumbnails pequenos.
    Retorna lista de bytes. Nunca levanta exceção.
    """
    query = pack_title.strip()
    if not query:
        return []

    LOG.info("[fab] Buscando '%s' (count=%d)", query, count)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=16) as client:
            urls = await _search_bing(client, query, count)

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
    """Converte bytes em InputMediaPhoto."""
    from telegram import InputMediaPhoto
    return InputMediaPhoto(media=io.BytesIO(image_bytes))
