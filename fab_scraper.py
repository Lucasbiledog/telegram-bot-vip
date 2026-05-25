"""
Módulo para buscar imagens de preview no Fab.com via Bing Image Search.
Fab.com está atrás de Cloudflare que bloqueia IPs de cloud (403).
Solução: buscar imagens via Bing Images, filtrar CDN do Epic/Fab,
priorizar screenshots 1920x1080, descartar thumbnails pequenos.
"""
from __future__ import annotations

import io
import logging
import re
import urllib.parse
from html import unescape

import httpx

LOG = logging.getLogger("fab_scraper")

_BING_IMG = "https://www.bing.com/images/search"

# User-Agent atualizado + headers realistas para evitar bloqueio do Bing
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.bing.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

# CDN URLs do Epic/Fab (sem Cloudflare, acesso direto)
# Padrão expandido para cobrir mais subdomínios do Epic/Fab
_CDN_PAT = re.compile(
    r"https?://(?:"
    r"cdn\d*\.epicgames\.com"
    r"|(?:media|cdn|static|images|assets)\.fab\.com"
    r"|fab\.com/cdn-cgi"
    r")"
    r"[^\x00-\x20\"'<>\\]+",
    re.IGNORECASE,
)
_IMG_EXT = re.compile(r"\.(jpg|jpeg|png|webp)", re.IGNORECASE)

# Hash hexadecimal no final do nome de arquivo (para dedup canônico)
_HASH_SUFFIX = re.compile(r"-[0-9a-f]{8,}$", re.IGNORECASE)

# Padrão para extrair murl/iurl de blobs JSON embutidos no HTML do Bing
# Ex: {"turl":"...thumb...","murl":"https://cdn1.epicgames.com/..."}
_MURL_PAT = re.compile(r'"(?:murl|iurl)"\s*:\s*"(https?://[^"\\]+)"')


def _canonical_key(url: str) -> str:
    """Chave de deduplicação: path sem hash e sem query string."""
    path = url.split("?")[0].lower()
    path = _HASH_SUFFIX.sub("", path)
    return path


def _score_url(url: str) -> int:
    """
    Pontuação de qualidade da URL:
      4 = Screenshot 1920x1080 (melhor)
      3 = media.fab.com gallery image
      2 = Featured (894x488)
      1 = outro
      0 = Thumbnail pequeno (descartar)
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


def _collect_raw_urls(text: str) -> list[str]:
    """
    Coleta URLs CDN candidatas usando três estratégias:
      1. JSON blobs (campo murl/iurl) — formato atual do Bing
      2. Regex direto no HTML unescapado
      3. Regex após URL-decode (Bing às vezes codifica as URLs)
    """
    candidates: list[str] = []

    # Estratégia 1: murl/iurl em JSON embutido no HTML
    for m in _MURL_PAT.finditer(text):
        url = m.group(1)
        # Desfaz escapes JSON (\/ → /)
        url = url.replace("\\/", "/")
        candidates.append(url)

    # Estratégia 2: padrão CDN direto no HTML
    candidates.extend(_CDN_PAT.findall(text))

    # Estratégia 3: após URL-decode (para URLs codificadas como %2F etc.)
    url_decoded = urllib.parse.unquote(text)
    if url_decoded != text:
        candidates.extend(_CDN_PAT.findall(url_decoded))

    return candidates


def _extract_and_rank_urls(html_text: str) -> list[str]:
    """
    Extrai, deduplica (por chave canônica) e ordena URLs CDN por qualidade.
    Só aceita URLs do CDN do Epic/Fab — descarta qualquer outro domínio,
    thumbnails e duplicatas.
    """
    unescaped = unescape(html_text)
    raw = _collect_raw_urls(unescaped)

    seen_keys: set[str] = set()
    ranked: list[tuple[int, str]] = []

    for url in raw:
        # Garante que é imagem E vem do CDN do Fab/Epic
        if not _IMG_EXT.search(url):
            continue
        if not _CDN_PAT.match(url):
            continue
        score = _score_url(url)
        if score == 0:
            continue
        key = _canonical_key(url)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ranked.append((score, url))

    ranked.sort(key=lambda x: x[0], reverse=True)
    return [url for _, url in ranked]


async def _search_bing(client: httpx.AsyncClient, query: str, count: int) -> list[str]:
    """Busca imagens no Bing e retorna URLs CDN rankeadas por qualidade."""
    bing_query = f"fab.com {query} unreal engine"
    try:
        resp = await client.get(
            _BING_IMG,
            params={"q": bing_query, "count": count * 8, "first": 1},
            headers=_HEADERS,
            timeout=18,
        )
        if resp.status_code != 200:
            LOG.warning("[fab_bing] Status %d para '%s'", resp.status_code, query)
            return []
        urls = _extract_and_rank_urls(resp.text)
        LOG.info("[fab_bing] %d URL(s) rankeadas para '%s'", len(urls), query)
        if not urls:
            LOG.debug("[fab_bing] HTML snippet: %s", resp.text[:500])
        return urls
    except Exception as exc:
        LOG.warning("[fab_bing] Erro para '%s': %s", query, exc)
        return []


async def _search_ddg(client: httpx.AsyncClient, query: str, count: int) -> list[str]:
    """Fallback: busca no DuckDuckGo Images e retorna URLs CDN rankeadas."""
    ddg_query = f"site:fab.com {query}"
    try:
        resp = await client.get(
            "https://duckduckgo.com/",
            params={"q": ddg_query, "iax": "images", "ia": "images"},
            headers={**_HEADERS, "Referer": "https://duckduckgo.com/"},
            timeout=18,
        )
        if resp.status_code != 200:
            LOG.debug("[fab_ddg] Status %d para '%s'", resp.status_code, query)
            return []
        urls = _extract_and_rank_urls(resp.text)
        LOG.info("[fab_ddg] %d URL(s) para '%s'", len(urls), query)
        return urls
    except Exception as exc:
        LOG.debug("[fab_ddg] Erro para '%s': %s", query, exc)
        return []


async def _download_images(client: httpx.AsyncClient, urls: list[str], count: int) -> list[bytes]:
    """Baixa até `count` imagens das URLs fornecidas (sem query string)."""
    result: list[bytes] = []
    for url in urls:
        if len(result) >= count:
            break
        clean_url = url.split("?")[0]
        try:
            resp = await client.get(
                clean_url,
                headers={"User-Agent": _HEADERS["User-Agent"]},
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
    Prioriza screenshots 1920x1080, descarta thumbnails pequenos.
    Usa Bing como fonte principal e DuckDuckGo como fallback.
    Retorna lista de bytes. Nunca levanta exceção.
    """
    query = pack_title.strip()
    if not query:
        return []

    LOG.info("[fab] Buscando '%s' (count=%d)", query, count)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            urls = await _search_bing(client, query, count)

            if not urls:
                LOG.info("[fab] Bing sem resultado, tentando DuckDuckGo para '%s'", query)
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
    """Converte bytes em InputMediaPhoto."""
    from telegram import InputMediaPhoto
    return InputMediaPhoto(media=io.BytesIO(image_bytes))
