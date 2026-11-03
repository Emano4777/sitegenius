"""Catálogo local de fotos de produto (consultado ANTES das bases abertas e da internet).

Cada entrada guarda: EAN, nome, marca, variante, apresentação, a foto (asset), a origem (fonte/página/URL) e a
autorização de uso:
  - "licença aberta (CC BY-SA 3.0, Open Food Facts)"     -> pode ser compartilhada entre clientes (user_id 0);
  - "foto enviada pelo cliente"                          -> só do cliente;
  - "uso autorizado pelo cliente em dd/mm/aaaa (site)"   -> só do cliente (foto da internet escolhida por ele).
A escolha automática segue a mesma regra das outras fontes (productmatch): marca, variante e apresentação precisam
corresponder; senão a entrada vira OPÇÃO para o cliente escolher.
"""
import time

from . import productmatch
from .db import get_conn, new_id, now

GLOBAL = 0  # entradas compartilháveis (somente licença aberta)
MAX_SCAN = 400


def _fields(nome, marca="", apresentacao=""):
    parsed = productmatch.parse(nome, marca)
    var = ", ".join(sorted(v for _, v in parsed["variantes"]))
    pres = apresentacao or ""
    if not pres and parsed["apresentacao"]:
        unit, value = parsed["apresentacao"]
        pres = f"{value:g} {unit}"
    return var, pres


def add(user_id, *, nome, asset_id, marca="", ean="", apresentacao="", fonte="", pagina="", image_url="", licenca="",
        autorizacao="", compartilhar=False, origem=""):
    """Registra (ou atualiza) uma foto verificada. compartilhar=True só para licença aberta."""
    conn = get_conn()
    owner = GLOBAL if compartilhar else user_id
    variante, pres = _fields(nome, marca, apresentacao)
    existing = conn.one("SELECT id FROM sg_catalog WHERE user_id = ? AND asset_id = ?", (owner, asset_id))
    ts = now()
    if existing:
        conn.run("UPDATE sg_catalog SET usos = usos + 1, updated_at = ? WHERE id = ?", (ts, existing["id"]))
        return existing["id"]
    entry_id = new_id()
    conn.run(
        "INSERT INTO sg_catalog (id, user_id, ean, nome, marca, variante, apresentacao, asset_id, fonte, pagina, image_url, "
        "licenca, autorizacao, usos, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (entry_id, owner, (ean or "")[:14], nome[:120], (marca or "")[:80], variante[:80], pres[:40], asset_id,
         (fonte or "")[:80], (pagina or "")[:500], (image_url or "")[:500], (licenca or "")[:80], (autorizacao or "")[:200], ts, ts),
    )
    return entry_id


def lookup(user_id, product):
    """(exata, opcoes): entradas do cliente primeiro, depois as compartilhadas."""
    rows = get_conn().all(
        "SELECT * FROM sg_catalog WHERE user_id IN (?, ?) ORDER BY CASE WHEN user_id = ? THEN 0 ELSE 1 END, usos DESC, "
        "updated_at DESC LIMIT ?", (user_id, GLOBAL, user_id, MAX_SCAN))
    name = product.get("nome") or ""
    ean = product.get("ean") or ""
    exact, options = None, []
    for row in rows:
        # Mesmo para fotos do próprio cliente: só vale se o produto da foto corresponde ao pedido
        # (tipo, marca, variante e apresentação). Nome idêntico ao que ele mesmo cadastrou continua valendo.
        level, _ = productmatch.evaluate(name, row["nome"], row["marca"], row["apresentacao"], ean, row["ean"])
        if level != "descartar" and row["user_id"] == user_id and productmatch.fold(row["nome"]) == productmatch.fold(name) \
                and (row.get("autorizacao") or "").startswith("foto enviada"):
            level = "exato"
        if level == "exato" and exact is None:
            exact = row
        elif level == "opcao" and len(options) < 3:
            options.append(row)
    return exact, options


def authorization_today(site):
    return f"uso autorizado pelo cliente em {time.strftime('%d/%m/%Y')} (imagem de {site or 'site da internet'})"
