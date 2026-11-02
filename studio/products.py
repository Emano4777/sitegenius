"""Busca de fotos de produto por EAN ou nome em bases abertas.

Fontes: Open Food Facts / Open Beauty Facts. As imagens são publicadas sob
CC BY-SA 3.0 (atribuição: colaboradores do projeto). A origem e a licença ficam
gravadas no asset e o usuário confirma a imagem antes do uso.
"""
import re

import requests

from .security import ALLOWED_IMAGE_HOSTS, USER_AGENT, UnsafeUrl, check_url

SOURCES = [
    {
        "id": "openfoodfacts",
        "nome": "Open Food Facts",
        "api": "https://world.openfoodfacts.org",
        "search": "https://search.openfoodfacts.org/search",
        "license": "CC BY-SA 3.0",
        "attribution": "Open Food Facts contributors",
    },
    {
        "id": "openbeautyfacts",
        "nome": "Open Beauty Facts",
        "api": "https://world.openbeautyfacts.org",
        "search": None,
        "license": "CC BY-SA 3.0",
        "attribution": "Open Beauty Facts contributors",
    },
]
FIELDS = "code,product_name,product_name_pt,brands,quantity,image_front_url,image_url"
TIMEOUT = (4, 10)


def _candidate(source, product):
    image = product.get("image_front_url") or product.get("image_url")
    if not image:
        return None
    try:
        check_url(image, ALLOWED_IMAGE_HOSTS)
    except UnsafeUrl:
        return None
    nome = product.get("product_name_pt") or product.get("product_name") or ""
    brands = product.get("brands") or ""
    if isinstance(brands, list):
        brands = ", ".join(brands)
    code = str(product.get("code") or "")
    return {
        "fonte": source["nome"],
        "ean": code,
        "nome": str(nome)[:120],
        "marca": str(brands)[:80],
        "quantidade": str(product.get("quantity") or "")[:40],
        "image_url": image,
        "license": source["license"],
        "attribution": source["attribution"],
        "pagina": f"{source['api']}/product/{code}" if code else source["api"],
    }


def lookup_ean(ean, http=requests):
    ean = re.sub(r"\D", "", ean or "")
    if len(ean) not in (8, 12, 13, 14):
        return []
    results = []
    for source in SOURCES:
        try:
            resp = http.get(
                f"{source['api']}/api/v2/product/{ean}",
                params={"fields": FIELDS},
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT,
            )
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        data = resp.json()
        if data.get("status") != 1:
            continue
        candidate = _candidate(source, data.get("product") or {})
        if candidate:
            results.append(candidate)
    return results


def search_name(query, http=requests, limit=12):
    query = re.sub(r"\s+", " ", query or "").strip()[:80]
    if len(query) < 2:
        return []
    source = SOURCES[0]
    try:
        resp = http.get(
            source["search"],
            params={"q": query, "page_size": limit, "fields": FIELDS, "langs": "pt"},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits") or []
    except (requests.RequestException, ValueError):
        hits = []
    if not hits:
        # API clássica como alternativa.
        try:
            resp = http.get(
                f"{source['api']}/cgi/search.pl",
                params={"search_terms": query, "search_simple": 1, "action": "process", "json": 1, "page_size": limit, "fields": FIELDS},
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            hits = resp.json().get("products") or []
        except (requests.RequestException, ValueError):
            hits = []
    results = []
    for product in hits:
        candidate = _candidate(source, product)
        if candidate:
            results.append(candidate)
        if len(results) >= limit:
            break
    return results


INTERNET_REQUEST = re.compile(r"(internet|google|web|online|pesquis|busc|procur|baix)\w*", re.I)
PHOTO_WORDS = re.compile(r"\b(foto|fotos|imagem|imagens|figura)\b", re.I)


def wants_internet_photos(text):
    return bool(INTERNET_REQUEST.search(text or "")) and bool(PHOTO_WORDS.search(text or "") or "internet" in (text or "").lower())


def _tokens(text):
    import unicodedata  # noqa: PLC0415

    folded = "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")
    return {w for w in re.findall(r"[a-z0-9]+", folded) if len(w) >= 3}


def match_score(product_name, candidate):
    """Quanto o resultado corresponde ao produto pedido (0..1): todas as palavras do pedido no nome/marca do resultado."""
    want = _tokens(product_name)
    have = _tokens(f"{candidate.get('nome', '')} {candidate.get('marca', '')}")
    if not want or not have:
        return 0.0
    return len(want & have) / len(want)


def find_photo(product, http=requests):
    """Foto confiável para um produto do encarte. Devolve (escolhida, opcoes).

    - EAN digitado: o produto exato (escolhida).
    - Nome específico (2+ palavras, ex.: "Leite Integral Italac"): só se todas as palavras baterem (escolhida).
    - Nome genérico ("Leite"): NÃO escolhe sozinho (poderia ser leite condensado, em pó…): devolve opções para o
      cliente escolher. Nunca usa resultado que não corresponda ao produto."""
    if product.get("ean"):
        for cand in lookup_ean(product["ean"], http=http):
            return {**cand, "confianca": 1.0, "criterio": "EAN"}, []
    name = product.get("nome", "")
    matching = [c for c in search_name(name, http=http, limit=12) if match_score(name, c) >= 1.0]
    if not matching:
        return None, []
    if len(_tokens(name)) < 2:
        # Genérico: só sugere produtos cujo nome começa com o termo ("Leite …"), nunca "Doce de leite".
        first = sorted(_tokens(name))[0]
        starts = [c for c in matching if sorted(_tokens(c.get("nome", "").split(" ")[0]) or {""})[0] == first]
        return None, starts[:3]
    best = min(matching, key=lambda c: len(_tokens(c.get("nome", "")) - _tokens(name)))
    return {**best, "confianca": 1.0, "criterio": "nome"}, []
