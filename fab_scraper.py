"""
Busca imagens de preview no Fab.com.

Estratégia em ordem de prioridade:
  1. API interna do Fab.com (JSON) — garante imagens do produto CORRETO
  2. Fallback: Bing filtrado para CDN do Epic/Fab com verificação de título

Segurança:
  - Nunca aceita imagens fora do CDN oficial (media.fab.com, cdn1/cdn2.epicgames.com)
  - Verifica similaridade de título antes de usar o resultado
  - Fab.com é marketplace profissional da Epic — sem conteúdo 18+
"""
from __future__ import annotations

import io
import logging
import re
import unicodedata
from html import unescape
from typing import Optional

import httpx

LOG = logging.getLogger("fab_scraper")

# ── Endpoints ────────────────────────────────────────────────────────────────
_FAB_SEARCH = "https://www.fab.com/i/listings"      # API interna (retorna JSON)
_BING_IMG   = "https://www.bing.com/images/search"  # fallback

# ── CDN allowlist — APENAS estes domínios são aceitos ────────────────────────
_ALLOWED_CDN = frozenset([
    "media.fab.com",
    "cdn1.epicgames.com",
    "cdn2.epicgames.com",
    "cdn.fab.com",
])

_CDN_PAT = re.compile(
    r"https?://(?:cdn1\.epicgames\.com|cdn2\.epicgames\.com|media\.fab\.com|cdn\.fab\.com)"
    r"[^\x00-\x20\"'<>]+",
    re.IGNORECASE,
)
_IMG_EXT    = re.compile(r"\.(jpg|jpeg|png|webp)", re.IGNORECASE)
_HASH_SUFFIX = re.compile(r"-[0-9a-f]{8,}$", re.IGNORECASE)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Similaridade mínima para aceitar um resultado do Fab como o pack correto
_MIN_TITLE_SIMILARITY = 0.35


# ── Utilitários ───────────────────────────────────────────────────────────────

def _normalize_title(t: str) -> str:
    """Normaliza título para comparação: minúsculas, sem acentos, apenas letras/números."""
    t = t.lower().strip()
    t = unicodedata.normalize("NFKD", t)
    t = t.encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _title_similarity(a: str, b: str) -> float:
    """Jaccard de palavras entre dois títulos normalizados."""
    wa = set(_normalize_title(a).split())
    wb = set(_normalize_title(b).split())
    if not wa or not wb:
        return 0.0
    intersection = wa & wb
    union = wa | wb
    return len(intersection) / len(union)


def _is_cdn_url(url: str) -> bool:
    """Retorna True somente se a URL é de um CDN oficial da Epic/Fab."""
    try:
        host = url.split("/")[2].lower()
        return any(host == cdn or host.endswith("." + cdn) for cdn in _ALLOWED_CDN)
    except Exception:
        return False


def _score_url(url: str) -> int:
    """
    Pontuação de qualidade da URL:
      4 = screenshot 1920x1080 (melhor)
      3 = media.fab.com gallery
      2 = featured (894x488)
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


def _canonical_key(url: str) -> str:
    path = url.split("?")[0].lower()
    path = _HASH_SUFFIX.sub("", path)
    return path


# ── Estratégia 1: API interna do Fab.com ─────────────────────────────────────

def _extract_image_urls_from_listing(listing: dict) -> list[str]:
    """Extrai URLs de galeria/thumbnail de um item da API do Fab."""
    urls: list[str] = []

    # Campos comuns que a API pode retornar com imagens
    for key in ("gallery", "images", "screenshots", "media", "previews"):
        items = listing.get(key) or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    for field in ("url", "image", "src", "original", "large"):
                        val = item.get(field)
                        if val and isinstance(val, str) and _is_cdn_url(val) and _IMG_EXT.search(val):
                            urls.append(val)
                elif isinstance(item, str) and _is_cdn_url(item) and _IMG_EXT.search(item):
                    urls.append(item)

    # Thumbnail como fallback
    for key in ("thumbnail", "thumbnail_url", "cover", "featured_image"):
        val = listing.get(key)
        if isinstance(val, dict):
            val = val.get("url") or val.get("src")
        if val and isinstance(val, str) and _is_cdn_url(val) and _IMG_EXT.search(val):
            urls.append(val)

    # Dedup preservando ordem
    seen: set[str] = set()
    result: list[str] = []
    for u in urls:
        k = _canonical_key(u)
        if k not in seen:
            seen.add(k)
            score = _score_url(u)
            if score > 0:
                result.append((score, u))

    result.sort(key=lambda x: x[0], reverse=True)
    return [u for _, u in result]


async def _search_fab_api(
    client: httpx.AsyncClient, query: str, count: int
) -> list[str]:
    """
    Consulta a API interna do Fab.com e retorna URLs CDN do produto mais similar.
    Nunca retorna URLs de fora do CDN oficial.
    """
    try:
        resp = await client.get(
            _FAB_SEARCH,
            params={"q": query, "sort_by": "relevance", "currency": "USD"},
            headers={
                **_HEADERS,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.fab.com/",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=14,
        )
        if resp.status_code != 200:
            LOG.info("[fab_api] Status %d para '%s'", resp.status_code, query)
            return []

        data = resp.json()
        LOG.debug("[fab_api] Resposta recebida para '%s'", query)

        # A API pode retornar resultados em diferentes chaves
        results = (
            data.get("results")
            or data.get("listings")
            or data.get("data")
            or data.get("items")
            or []
        )

        if not isinstance(results, list) or not results:
            LOG.info("[fab_api] Nenhum resultado para '%s'", query)
            return []

        # Encontrar o resultado com maior similaridade de título
        best_listing: Optional[dict] = None
        best_score = 0.0

        for listing in results[:10]:
            if not isinstance(listing, dict):
                continue
            title = (
                listing.get("title")
                or listing.get("name")
                or listing.get("label")
                or ""
            )
            sim = _title_similarity(query, title)
            LOG.debug("[fab_api] '%s' vs '%s' → %.2f", query[:40], str(title)[:40], sim)
            if sim > best_score:
                best_score = sim
                best_listing = listing

        if best_listing is None or best_score < _MIN_TITLE_SIMILARITY:
            LOG.info(
                "[fab_api] Melhor match para '%s': %.2f (mínimo %.2f) — ignorado",
                query, best_score, _MIN_TITLE_SIMILARITY,
            )
            return []

        LOG.info(
            "[fab_api] Match aceito: '%.40s' → similaridade %.2f",
            best_listing.get("title", "?"), best_score,
        )

        urls = _extract_image_urls_from_listing(best_listing)
        LOG.info("[fab_api] %d URL(s) de imagem extraídas", len(urls))
        return urls[:count * 2]  # margem para falhas de download

    except Exception as exc:
        LOG.info("[fab_api] Falhou ('%s'): %s — tentando fallback Bing", query, exc)
        return []


# ── Estratégia 2: Bing (fallback) — CDN-only, com verificação de título ──────

def _extract_and_rank_cdn_urls(html_text: str, query: str) -> list[str]:
    """
    Extrai URLs do HTML do Bing, aceita SOMENTE domínios CDN oficiais,
    verifica se o contexto local ao URL contém palavras do título.
    """
    unescaped = unescape(html_text)
    raw = [u for u in _CDN_PAT.findall(unescaped) if _IMG_EXT.search(u)]

    query_words = set(_normalize_title(query).split())

    seen_keys: set[str] = set()
    ranked: list[tuple[int, str]] = []

    for url in raw:
        if not _is_cdn_url(url):
            continue  # garantia extra: nunca aceita fora do CDN
        score = _score_url(url)
        if score == 0:
            continue
        key = _canonical_key(url)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        # Verificar contexto: pegar 200 chars antes da URL no HTML e checar palavras
        idx = unescaped.find(url)
        context = _normalize_title(unescaped[max(0, idx - 200): idx + len(url) + 200])
        context_words = set(context.split())
        overlap = len(query_words & context_words) / max(len(query_words), 1)
        if overlap < 0.2:
            LOG.debug("[fab_bing] URL descartada por baixo overlap de contexto: %s", url[:60])
            continue

        ranked.append((score, url))

    ranked.sort(key=lambda x: x[0], reverse=True)
    return [u for _, u in ranked]


async def _search_bing_cdn(
    client: httpx.AsyncClient, query: str, count: int
) -> list[str]:
    """Busca imagens no Bing, filtra estritamente para CDN oficial da Epic/Fab."""
    bing_query = f'site:fab.com OR site:epicgames.com "{query}"'
    try:
        resp = await client.get(
            _BING_IMG,
            params={"q": bing_query, "count": count * 8},
            headers={**_HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"},
            timeout=14,
        )
        if resp.status_code != 200:
            LOG.warning("[fab_bing] Status %d para '%s'", resp.status_code, query)
            return []
        urls = _extract_and_rank_cdn_urls(resp.text, query)
        LOG.info("[fab_bing] %d URL(s) CDN para '%s'", len(urls), query)
        return urls
    except Exception as exc:
        LOG.warning("[fab_bing] Erro para '%s': %s", query, exc)
        return []


# ── Download ──────────────────────────────────────────────────────────────────

async def _download_images(
    client: httpx.AsyncClient, urls: list[str], count: int
) -> list[bytes]:
    """Baixa até `count` imagens. Só baixa de domínios CDN permitidos."""
    result: list[bytes] = []
    for url in urls:
        if len(result) >= count:
            break
        if not _is_cdn_url(url):
            LOG.warning("[fab_dl] URL fora do CDN ignorada: %s", url[:80])
            continue
        clean_url = url.split("?")[0]
        try:
            resp = await client.get(
                clean_url,
                headers={"User-Agent": _HEADERS["User-Agent"]},
                timeout=12,
                follow_redirects=True,
            )
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "image/" in ct and len(resp.content) > 5_000:
                result.append(resp.content)
                LOG.debug("[fab_dl] OK %s (%d bytes)", clean_url[:80], len(resp.content))
            else:
                LOG.debug("[fab_dl] SKIP %s → %d %s", clean_url[:80], resp.status_code, ct)
        except Exception as exc:
            LOG.debug("[fab_dl] Erro %s: %s", clean_url[:80], exc)
    return result


# ── Função pública ────────────────────────────────────────────────────────────

async def fetch_fab_images(pack_title: str, count: int = 3) -> list[bytes]:
    """
    Busca até `count` imagens de preview do Fab.com para o título do pack.

    Fluxo:
      1. API interna do Fab.com (JSON) — imagens do produto correto
      2. Fallback Bing estrito — CDN-only + verificação de título

    Garante que NUNCA retorna imagens fora do CDN oficial da Epic/Fab.
    Retorna lista de bytes. Nunca levanta exceção.
    """
    query = pack_title.strip()
    if not query:
        return []

    LOG.info("[fab] Buscando '%s' (count=%d)", query, count)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=16) as client:

            # ── 1) API do Fab.com ──────────────────────────────────────────
            urls = await _search_fab_api(client, query, count)

            if urls:
                LOG.info("[fab] API retornou %d URL(s), baixando...", len(urls))
                images = await _download_images(client, urls, count)
                if images:
                    LOG.info("[fab] ✅ %d imagem(ns) via API para '%s'", len(images), query)
                    return images
                LOG.info("[fab] Downloads da API falharam, tentando Bing...")

            # ── 2) Fallback Bing (CDN-only) ────────────────────────────────
            urls = await _search_bing_cdn(client, query, count)
            if not urls:
                LOG.info("[fab] Nenhuma URL encontrada para '%s'", query)
                return []

            images = await _download_images(client, urls, count)
            LOG.info("[fab] %d imagem(ns) via Bing-fallback para '%s'", len(images), query)
            return images

    except Exception as exc:
        LOG.warning("[fab] Erro inesperado para '%s': %s", query, exc)
        return []


def to_input_media(image_bytes: bytes) -> "InputMediaPhoto":  # type: ignore[name-defined]
    """Converte bytes em InputMediaPhoto."""
    from telegram import InputMediaPhoto
    return InputMediaPhoto(media=io.BytesIO(image_bytes))
