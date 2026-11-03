"""Correspondência de produto: tipo, marca, variante e apresentação (sem dependências; roda também na HostGator, Python 3.9).

Uma foto só é escolhida sozinha quando o resultado é COMPROVADAMENTE o produto pedido:
  - mesmo tipo ("Leite …", nunca "Creme de leite" / "Doce de leite");
  - mesma marca, mesma variante e mesma apresentação, todas informadas pelo cliente;
  - nenhuma variante a mais no resultado (pediu "Leite Italac integral": "semidesnatado" ou "zero lactose" não servem).
Quando algo não pode ser confirmado (ex.: o cliente não disse a apresentação), o resultado vira OPÇÃO para o cliente
escolher; quando algo contradiz o pedido, o resultado é descartado.
"""
import re
import unicodedata

# Variantes mutuamente exclusivas (dentro do mesmo grupo).
VARIANT_GROUPS = [
    ["integral", "desnatado", "semidesnatado"],
    ["tradicional", "extra forte", "forte", "suave", "descafeinado", "gourmet", "intenso"],
    ["branco", "parboilizado", "arboreo", "agulhinha"],
    ["carioca", "preto", "fradinho", "vermelho"],
    ["refinado", "cristal", "demerara", "mascavo", "organico"],
    ["zero", "diet", "light", "original"],
    ["soja", "girassol", "milho", "canola", "oliva"],
    ["neutro", "coco", "lavanda", "erva doce", "aloe vera", "camomila"],
]
# Modificadores que mudam o produto: se o resultado tem e o pedido não, NÃO é o mesmo produto.
MODIFIERS = ["zero lactose", "sem lactose", "condensado", "creme de", "doce de", "em po", "achocolatado",
             "bebida lactea", "fermentado", "sabor", "kit", "combo", "fardo", "refil", "infantil", "mini", "sache",
             "capsula", "capsulas", "uht"]
NEUTRAL_MODIFIERS = {"uht"}
# Marcas que, no varejo, SÃO outro tipo de produto: "Leite Moça" é leite condensado, "Ninho" é leite em pó.
BRAND_IMPLIES = {"moca": "condensado", "ninho": "em po", "nescau": "achocolatado", "toddy": "achocolatado",
                 "itambezinho": "fermentado", "danoninho": "fermentado", "chamyto": "fermentado"}  # aparece em quase todo leite de caixinha: não conta como diferença
LEAD_NOISE = {"kit", "combo", "pack", "caixa", "fardo", "promocao", "oferta"}
UNIT_RE = re.compile(r"(?<![\w,.])(\d+(?:[.,]\d+)?)\s*(kg|quilos?|g|gr|gramas?|l|lt|lts|litros?|ml|un|und|unid|unidades?|"
                     r"comprimidos?|caps|capsulas?|m|metros?|folhas?|rolos?)\b")
PACK_RE = re.compile(r"\b(?:c/|com|cx|caixa com|pack|leve)\s*(\d{1,3})\b|\b(\d{1,3})\s*x\s*\d")
STOP = {"de", "da", "do", "das", "dos", "com", "para", "pra", "e", "em", "a", "o", "as", "os", "tipo", "cx", "pct",
        "pacote", "garrafa", "lata", "pote", "frasco", "unidade", "embalagem"}


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")


def _words(text):
    return re.findall(r"[a-z0-9]+", fold(text))


def presentation(text):
    """'1L', '1 litro', '1000ml' -> ('ml', 1000.0); '5kg' -> ('g', 5000.0); sem apresentação -> None."""
    m = UNIT_RE.search(fold(text))
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    if unit.startswith(("kg", "quilo")):
        return ("g", value * 1000)
    if unit in ("g", "gr") or unit.startswith("grama"):
        return ("g", value)
    if unit in ("l", "lt", "lts") or unit.startswith("litro"):
        return ("ml", value * 1000)
    if unit == "ml":
        return ("ml", value)
    if unit in ("m",) or unit.startswith("metro"):
        return ("m", value)
    return ("un", value)


def _has(text_folded, phrase):
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text_folded) is not None


def variants(text):
    """Variantes citadas: {(grupo, variante)}. 'semidesnatado' não conta como 'desnatado'."""
    t = fold(text)
    found = set()
    for gi, group in enumerate(VARIANT_GROUPS):
        for v in sorted(group, key=len, reverse=True):  # "extra forte" antes de "forte"
            if _has(t, v):
                found.add((gi, v))
                t = re.sub(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", " ", t)
    return found


def modifiers(text):
    t = fold(text)
    found = {m for m in MODIFIERS if _has(t, m)}
    found |= {implied for brand, implied in BRAND_IMPLIES.items() if _has(t, brand)}
    return found


def parse(name, brand_hint=""):
    """Pedido do cliente -> {'tipo', 'marca', 'variantes', 'apresentacao', 'palavras'}.

    Marca: palavras com inicial maiúscula depois da primeira (\"Leite integral Italac\" -> Italac), que não sejam
    variante/unidade; ou a marca informada à parte."""
    raw = " ".join((name or "").split())
    words = raw.split()
    var = variants(raw)
    var_words = {w for _, v in var for w in v.split()}
    brand = []
    for w in words[1:]:
        fw = fold(w).strip(".,;:")
        if (w[:1].isupper() or (raw.isupper() and len(w) > 2)) and fw not in var_words and fw not in STOP \
                and not UNIT_RE.fullmatch(fw) and not re.fullmatch(r"\d+\w*", fw):
            brand.append(fw)
    if brand_hint:
        brand = [w for w in _words(brand_hint) if w not in STOP] or brand
    tipo = next((w for w in _words(raw) if w not in STOP and not w.isdigit()), "")
    return {"tipo": tipo, "marca": brand, "variantes": var, "modificadores": modifiers(raw),
            "apresentacao": presentation(raw), "palavras": {w for w in _words(raw) if len(w) >= 3 and w not in STOP}}


def _first_meaningful(text):
    for w in _words(text):
        if w in STOP or w.isdigit() or w in LEAD_NOISE:
            if w in LEAD_NOISE:
                return w
            continue
        return w
    return ""


def evaluate(request_name, title, brand="", quantity="", ean_request="", ean_candidate=""):
    """Compara o pedido com um resultado (catálogo, base aberta ou web).

    Devolve (nivel, motivo): nivel = "exato" (pode escolher sozinho), "opcao" (mostrar ao cliente) ou
    "descartar" (não é o produto)."""
    if ean_request and ean_candidate:
        return ("exato", "código de barras") if re.sub(r"\D", "", ean_request) == re.sub(r"\D", "", ean_candidate) \
            else ("descartar", "outro código de barras")
    want = parse(request_name)
    full = f"{title} {brand} {quantity}"
    have_words = set(_words(full))
    if not want["tipo"]:
        return "descartar", "pedido vazio"
    first = _first_meaningful(title)
    if first != want["tipo"]:
        return "descartar", f"outro tipo de produto ({first or '?'})"
    missing = {w for w in want["palavras"] if w not in have_words and not any(h.startswith(w) for h in have_words)}
    if missing:
        return "descartar", "não tem: " + ", ".join(sorted(missing))
    have_var = variants(full)
    want_groups = {g for g, _ in want["variantes"]}
    for g, v in have_var:
        if g in want_groups and (g, v) not in want["variantes"]:
            return "descartar", f"variante diferente ({v})"
    extra_mod = modifiers(full) - want["modificadores"] - NEUTRAL_MODIFIERS
    if extra_mod:
        return "descartar", "é outro produto (" + ", ".join(sorted(extra_mod)) + ")"
    have_pres = presentation(f"{title} {quantity}")
    if want["apresentacao"] and have_pres and (want["apresentacao"][0] != have_pres[0] or
                                               abs(want["apresentacao"][1] - have_pres[1]) > 0.5):
        return "descartar", "outra apresentação"
    # Daqui em diante é o produto; só é "exato" se tudo que define a embalagem foi informado E confere.
    unconfirmed = []
    if not want["marca"]:
        unconfirmed.append("marca")
    if {g for g, _ in have_var} - want_groups:
        unconfirmed.append("variante")
    if not want["apresentacao"]:
        unconfirmed.append("apresentação")
    elif not have_pres:
        unconfirmed.append("apresentação do resultado")
    if unconfirmed:
        return "opcao", "confirmar " + ", ".join(unconfirmed)
    return "exato", "marca, variante e apresentação conferem"


def label(title, brand="", quantity=""):
    """Descrição curta para o cliente escolher: nome + apresentação (se não estiver no nome)."""
    text = " ".join((title or "").split())[:70]
    if quantity and not presentation(text) and presentation(quantity):
        text += f" {quantity.strip()}"
    if brand and fold(brand.split(",")[0]) not in fold(text):
        text += f" ({brand.split(',')[0].strip()})"
    return text
