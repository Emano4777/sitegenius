"""Briefing estruturado do encarte.

Regra central: o sistema nunca inventa oferta. Preço só entra no briefing se
foi digitado pelo usuário (no chat ou no formulário). Quando a IA propõe um
preço que não aparece no texto do usuário, o preço é descartado e vira pendência.
"""
import re
import unicodedata

FORMATS = {
    "feed_4x5": {"largura": 1080, "altura": 1350, "nome": "Feed 4:5"},
    "quadrado": {"largura": 1080, "altura": 1080, "nome": "Quadrado 1:1"},
    "story": {"largura": 1080, "altura": 1920, "nome": "Story 9:16"},
    "a4": {"largura": 1240, "altura": 1754, "nome": "A4 retrato"},
}
DEFAULT_FORMAT = "feed_4x5"
MAX_PRODUCTS = 24

# O modelo de imagem só cria cenário: estas restrições são sempre anexadas.
SCENE_GUARDRAILS = (
    "background scene only, empty clean area in the center for product cards, "
    "no text, no letters, no numbers, no prices, no logos, no brand names, no packaged products"
)

THEMES = {
    "black friday": "black friday, dark elegant background with gold and neon accents",
    "natal": "christmas, festive red and green decorations, warm lights",
    "pascoa": "easter, pastel colors, spring flowers",
    "dia das maes": "mother's day, soft pink flowers, warm light",
    "dia dos pais": "father's day, modern navy and wood tones",
    "dia das criancas": "children's day, colorful playful confetti and balloons",
    "hortifruti": "fresh produce market, green leaves, wooden crates, natural light",
    "acougue": "butcher shop, rustic wood board, herbs, warm light",
    "farmacia": "clean pharmacy, soft blue and white, health and care mood",
    "padaria": "bakery, warm golden light, rustic wooden table, wheat",
    "bebidas": "refreshing drinks, ice cubes, water splashes, cool blue tones",
    "carnaval": "carnival, colorful streamers and confetti",
    "festa junina": "brazilian june festival, bunting flags, bonfire warm tones",
    "verao": "summer, sunny beach colors, bright light",
}

PRICE_RE = re.compile(r"(?<![\d.,])(?:r\$\s*)?(\d{1,5}(?:\.\d{3})*,\d{2}|\d{1,5}\.\d{2})(?![\d])", re.I)
EAN_RE = re.compile(r"(?<!\d)(\d{8}|\d{12,14})(?!\d)")
VALIDITY_RE = re.compile(
    r"(?:v[aá]lid[oa]s?|validade|at[eé])\s*(?:de|at[eé]|:)?\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:\s*(?:a|at[eé]|-)\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?)?)",
    re.I,
)
STORE_RE = re.compile(r"(?:loja|mercado|supermercado|farm[aá]cia|empresa)\s*[:\-]\s*([^\n;,]{2,60})", re.I)
UNIT_RE = re.compile(r"\b(kg|un|und|unid|unidade|pct|pacote|cx|caixa|lt|l|ml|g|dz|d[uú]zia|bandeja)\b\.?$", re.I)
SPLIT_RE = re.compile(r"[\n;•]+")
LEAD_NOISE_RE = re.compile(r"^(?:e|,|\.|-|quero|encarte|ofertas?|promo[cç][aã]o|produtos?)\b[\s,.:-]*", re.I)


def strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def price_to_cents(value):
    """'1.234,56' | '1234.56' | '9,99' | 9.99 -> centavos (int) ou None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        cents = round(float(value) * 100)
        return cents if 0 < cents < 10_000_000 else None
    text = str(value).strip().lower().replace("r$", "").replace(" ", "")
    if re.fullmatch(r"\d{1,5}(?:\.\d{3})*,\d{1,2}", text):
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,7}(?:\.\d{1,2})?", text):
        pass
    elif re.fullmatch(r"\d{1,7},\d{1,2}", text):
        text = text.replace(",", ".")
    else:
        return None
    cents = round(float(text) * 100)
    return cents if 0 < cents < 10_000_000 else None


def format_brl(cents):
    reais, centavos = divmod(int(cents), 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{centavos:02d}"


# Preço sem centavos em linguagem natural: "45 reais", "R$ 45", "por 10", "é 55", "de 15 para 10", "paga 45".
WORD_PRICE_RE = re.compile(
    r"(?:r\$\s*|\b(?:por|para|pra|paga|pago|pagar|pagando|[eé]|sai(?:\s+a)?|custa|custando|de|s[oó]|apenas|vale)\s+)"
    r"(\d{1,5}(?:[.,]\d{1,2})?)(?![\d.,]\d)|(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:reais|real)\b", re.I)


def grounded_prices(user_texts):
    """Todos os preços que o usuário realmente digitou, em centavos (com ou sem centavos)."""
    found = set()
    for text in user_texts:
        for match in PRICE_RE.finditer(text or ""):
            cents = price_to_cents(match.group(1))
            if cents:
                found.add(cents)
        for match in WORD_PRICE_RE.finditer(text or ""):
            cents = price_to_cents(match.group(1) or match.group(2))
            if cents:
                found.add(cents)
    return found


def typed_digits(user_texts):
    return " ".join(re.sub(r"\D", " ", t or "") for t in user_texts)


def empty_briefing():
    return {
        "tipo": "encarte",
        "formato": DEFAULT_FORMAT,
        "loja": "",
        "titulo": "Ofertas",
        "tema": "",
        "cenario": "",
        "validade": "",
        "observacoes": "",
        "produtos": [],
        "logo_asset_id": None,
        "referencias": [],
        "pendencias": [],
    }


def _clean_text(value, limit):
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _clean_id(value):
    text = str(value or "")
    return text if re.fullmatch(r"[0-9a-f\-]{36}", text) else None


# ---------------------------------------------------------------- interpretação por regras


def _segments(line):
    """'Arroz 24,90, Feijão 7,49' -> dois segmentos; 'Café de 19,90 por 15,99' -> um."""
    prices = list(PRICE_RE.finditer(line))
    if len(prices) < 2 or re.search(r"\bde\b.*\bpor\b", strip_accents(line.lower())):
        return [line]
    out, start = [], 0
    for match in prices:
        out.append(line[start : match.end()])
        start = match.end()
    if out and line[start:].strip(" ,.;"):
        # Texto após o último preço (ex.: unidade "/kg") fica no último segmento.
        out[-1] += line[start:]
    return out


def _parse_product_line(line):
    if ":" in line:
        line = line.rsplit(":", 1)[1]
    line = line.strip(" ,.")
    prices = [m for m in PRICE_RE.finditer(line)]
    if not prices:
        return None
    ean_match = EAN_RE.search(PRICE_RE.sub(" ", line))
    preco_de = None
    preco = prices[-1].group(1)
    lowered = strip_accents(line.lower())
    if len(prices) >= 2 and re.search(r"\bde\b.*\bpor\b", lowered):
        preco_de = prices[0].group(1)
    name = PRICE_RE.sub(" ", line)
    if ean_match:
        name = name.replace(ean_match.group(1), " ")
    name = re.sub(r"(?i)\b(ean|c[oó]d(igo)?|apenas|pre[cç]o|cada)\b[:.]?|r\$", " ", name)
    # "de"/"por"/"só" só saem no fim (sobra de "de 12,99 por 9,99"); "Suco de uva" fica.
    name = re.sub(r"(?i)(?:\s+(?:de|por|s[oó]))+\s*$", "", name.rstrip())
    # Separadores soltos saem; hífen dentro de nome (Coca-Cola) fica.
    name = re.sub(r"(?:^|\s)[\-–—:=|]+(?=\s|$)", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" ,.-")
    name = LEAD_NOISE_RE.sub("", name).strip(" ,.-")
    unidade = ""
    unit_match = UNIT_RE.search(name)
    if unit_match and len(name.split()) > 1 and name.lower().endswith(("/" + unit_match.group(1).lower(), " " + unit_match.group(1).lower())):
        unidade = unit_match.group(1).lower()
        name = name[: unit_match.start()].rstrip(" /")
    if len(name) < 2:
        return None
    return {
        "nome": name[:80],
        "ean": ean_match.group(1) if ean_match else "",
        "preco": preco,
        "preco_de": preco_de or "",
        "unidade": unidade,
        "asset_id": None,
    }


def interpret_rules(text, previous=None):
    """Atualiza o briefing a partir de uma mensagem, sem IA."""
    briefing = dict(previous or empty_briefing())
    briefing["produtos"] = [dict(p) for p in briefing.get("produtos", [])]
    lowered = strip_accents((text or "").lower())

    if re.search(r"\bstor(y|ies)\b|9\s*[:x]\s*16", lowered):
        briefing["formato"] = "story"
    elif re.search(r"\ba4\b|impress", lowered):
        briefing["formato"] = "a4"
    elif re.search(r"quadrad|1\s*[:x]\s*1", lowered):
        briefing["formato"] = "quadrado"
    elif re.search(r"\bfeed\b|4\s*[:x]\s*5", lowered):
        briefing["formato"] = "feed_4x5"

    for key in THEMES:
        if key in lowered:
            briefing["tema"] = key
            break

    store = STORE_RE.search(text or "")
    if store:
        briefing["loja"] = _clean_text(store.group(1), 60)
    validity = VALIDITY_RE.search(text or "")
    if validity:
        briefing["validade"] = _clean_text(validity.group(1), 40)
    title = re.search(r"(?i)t[ií]tulo\s*[:\-]\s*([^\n;]{2,60})", text or "")
    if title:
        briefing["titulo"] = _clean_text(title.group(1), 60)
    scene = re.search(r"(?i)(?:cen[aá]rio|fundo)\s*[:\-]\s*([^\n;]{3,200})", text or "")
    if scene:
        briefing["cenario"] = _clean_text(scene.group(1), 200)

    by_name = {strip_accents(p["nome"].lower()): p for p in briefing["produtos"]}
    segments = [seg for line in SPLIT_RE.split(text or "") for seg in _segments(line.strip())]
    for segment in segments:
        product = _parse_product_line(segment)
        if not product:
            continue
        key = strip_accents(product["nome"].lower())
        if key in by_name:
            by_name[key].update({k: v for k, v in product.items() if v and k != "asset_id"})
        elif len(briefing["produtos"]) < MAX_PRODUCTS:
            briefing["produtos"].append(product)
            by_name[key] = product
    return briefing


# ---------------------------------------------------------------- validação


COMPOSITION_VALUES = {
    "arranjo": {"tabloide", "grade", "destaque", "diagonal", "zigue", "assimetrica", "livre"},
    "fundo": {"solido", "gradiente", "diagonal", "raios", "listras", "bolhas"},
    "preco": {"numeros", "selo", "explosao", "circulo", "etiqueta"},
    "cabecalho": {"reto", "diagonal", "onda"},
    "produto_forma": {"livre", "circulo"},
}


def _clean_composition(value):
    """Escolhas de composição pedidas pelo cliente (só valores conhecidos)."""
    value = value if isinstance(value, dict) else {}
    out = {k: value[k] for k, allowed in COMPOSITION_VALUES.items() if value.get(k) in allowed}
    try:
        if value.get("angulo") is not None:
            out["angulo"] = max(-25, min(25, int(value["angulo"])))
    except (TypeError, ValueError):
        pass
    if value.get("cartao") is False:
        out["cartao"] = None
    return out


def normalize_briefing(data, user_texts=None, owned_asset_ids=None, trust_prices=False):
    """Valida um briefing vindo do cliente ou da IA.

    trust_prices=True: preços digitados pelo usuário no formulário de confirmação.
    trust_prices=False: preços precisam aparecer no texto do usuário (user_texts).
    """
    data = data if isinstance(data, dict) else {}
    owned = set(owned_asset_ids or [])
    grounded = grounded_prices(user_texts or [])
    out = empty_briefing()

    fmt = data.get("formato")
    out["formato"] = fmt if fmt in FORMATS else DEFAULT_FORMAT
    out["tipo"] = "encarte"
    out["loja"] = _clean_text(data.get("loja"), 60)
    out["titulo"] = _clean_text(data.get("titulo"), 60) or "Ofertas"
    out["tema"] = _clean_text(data.get("tema"), 60)
    out["cenario"] = _clean_text(data.get("cenario"), 300)
    out["validade"] = _clean_text(data.get("validade"), 40)
    out["observacoes"] = _clean_text(data.get("observacoes"), 300)
    logo = _clean_id(data.get("logo_asset_id"))
    out["logo_asset_id"] = logo if logo in owned else None
    out["referencias"] = [a for a in (_clean_id(x) for x in (data.get("referencias") or [])[:6]) if a in owned]

    pendencias = []
    for raw in (data.get("produtos") or [])[:MAX_PRODUCTS]:
        if not isinstance(raw, dict):
            continue
        nome = _clean_text(raw.get("nome"), 80)
        if not nome:
            continue
        ean = re.sub(r"\D", "", str(raw.get("ean") or ""))
        ean = ean if len(ean) in (8, 12, 13, 14) else ""
        if ean and not trust_prices and ean not in typed_digits(user_texts or []).split():
            ean = ""  # código de barras só se o cliente digitou (ou escolheu no painel): nunca inventado
        cents = price_to_cents(raw.get("preco"))
        de_cents = price_to_cents(raw.get("preco_de"))
        if not trust_prices:
            if cents and cents not in grounded:
                cents = None
            if de_cents and de_cents not in grounded:
                de_cents = None
        if de_cents and not cents:
            cents, de_cents = de_cents, None  # "preço antigo" sem preço atual: é o preço (o modelo trocou o campo)
        if de_cents and cents and de_cents <= cents:
            de_cents = None
        asset = _clean_id(raw.get("asset_id"))
        produto = {
            "nome": nome,
            "ean": ean,
            "preco": format_brl(cents) if cents else "",
            "preco_de": format_brl(de_cents) if de_cents else "",
            "unidade": _clean_text(raw.get("unidade"), 12),
            "oferta": _grounded_offer(raw.get("oferta"), grounded, trust_prices),
            "asset_id": asset if asset in owned else None,
        }
        if not cents:
            pendencias.append(f"Informe o preço de “{nome}”.")
        if not produto["asset_id"]:
            pendencias.append(f"Escolha ou envie a foto de “{nome}”.")
        out["produtos"].append(produto)

    if not out["produtos"]:
        pendencias.insert(0, "Liste os produtos com preço (ex.: “Arroz 5kg 24,90”).")
    names = {p["nome"] for p in out["produtos"]}
    # Layout escolhido pela conversa (estilo, cores, destaque, prévias): validado, nunca inventado.
    lay = data.get("layout") if isinstance(data.get("layout"), dict) else {}
    modelos = ("jornal", "farmacia", "premium", "relampago", "catalogo", "referencia", "criativo")
    cores = []
    for c in (lay.get("cores") or [])[:2]:
        if isinstance(c, (list, tuple)) and len(c) == 3:
            cores.append([max(0, min(255, int(v))) for v in c])
    out["layout"] = {"modelo": lay.get("modelo") if lay.get("modelo") in modelos else None, "cores": cores,
                     "destaque": lay.get("destaque") if lay.get("destaque") in names else None,
                     "fundo_ia": lay.get("fundo_ia") is True,
                     "previas": [m for m in (lay.get("previas") or []) if isinstance(m, str) and
                                 (m in modelos or re.fullmatch(r"criativo:\d{1,6}", m))][:5],
                     "instrucoes": _clean_text(lay.get("instrucoes"), 300),
                     "composicao": _clean_composition(lay.get("composicao")),
                     "semente": int(lay.get("semente")) if str(lay.get("semente") or "").isdigit() else 1,
                     "gerar_apos_analise": lay.get("gerar_apos_analise") is True}
    ref = lay.get("referencia") if isinstance(lay.get("referencia"), dict) else None
    if ref and _clean_id(ref.get("asset_id")) and (not owned_asset_ids or _clean_id(ref.get("asset_id")) in owned
                                                   or ref.get("estado") == "pronta"):
        from .refstyle import sanitize  # noqa: PLC0415

        spec = sanitize(ref.get("spec")) if ref.get("spec") else None
        estado = ref.get("estado") if ref.get("estado") in ("analisando", "pronta", "falhou") else "analisando"
        specs = [x for x in (sanitize(v) for v in (ref.get("specs") or [])[:3]) if x]
        ids = [a for a in (_clean_id(v) for v in (ref.get("asset_ids") or [])[:3]) if a]
        papeis = ref.get("papeis") if isinstance(ref.get("papeis"), dict) else {}
        out["layout"]["referencia"] = {"asset_id": _clean_id(ref["asset_id"]), "estado": "pronta" if spec else estado,
                                       "spec": spec, "resumo": _clean_text(ref.get("resumo"), 300),
                                       "specs": specs, "asset_ids": ids or [_clean_id(ref["asset_id"])],
                                       "papeis": {k: int(papeis[k]) for k in ("estrutura", "cores")
                                                  if isinstance(papeis.get(k), int) and 0 <= papeis[k] < 3}}
    if out["layout"]["modelo"] == "referencia" and not (out["layout"].get("referencia") or {}).get("spec"):
        out["layout"]["modelo"] = None if not out["layout"].get("referencia") else "referencia"
    # Valores que precisam de confirmação do cliente (leitura incerta): só enquanto o campo estiver vazio.
    by_name = {p["nome"]: p for p in out["produtos"]}
    out["confirmar"] = [{"produto": c["produto"], "campo": c["campo"], "lido": _clean_text(c.get("lido"), 20)}
                        for c in (data.get("confirmar") or []) if isinstance(c, dict) and c.get("campo") in ("preco", "preco_de")
                        and c.get("produto") in by_name and not by_name[c["produto"]][c["campo"]]][:12]
    for c in out["confirmar"]:
        pendencias.append(f"Confirme {'o preço' if c['campo'] == 'preco' else 'o preço antigo'} de “{c['produto']}”"
                          + (f" (li {c['lido']})." if c["lido"] else "."))
    out["gerado"] = data.get("gerado") is True
    # Opções de foto aguardando a escolha do cliente (só de produtos do encarte e imagens dele).
    opcoes = {}
    for name, opts in (data.get("opcoes_foto") or {}).items() if isinstance(data.get("opcoes_foto"), dict) else []:
        if name in names and isinstance(opts, list):
            keep = [{k: (_clean_text(o.get(k), 400) if isinstance(o.get(k), str) else o.get(k)) for k in
                     ("asset_id", "nome", "fonte", "pagina", "licenca", "autorizacao", "marca", "ean", "apresentacao",
                      "web", "catalogo") if o.get(k) not in (None, "")}
                    for o in opts[:3] if isinstance(o, dict) and _clean_id(o.get("asset_id")) and
                    (not owned_asset_ids or _clean_id(o.get("asset_id")) in owned)]
            if keep and not any(p["nome"] == name and p["asset_id"] for p in out["produtos"]):
                opcoes[name] = keep
    out["opcoes_foto"] = opcoes
    out["encarte_id"] = _clean_id(data.get("encarte_id")) or None
    out["pendencias"] = pendencias
    return out


def _grounded_offer(text, grounded, trusted):
    """Oferta curta ("Leve 2 por R$ 45,00"): todo valor em reais precisa ter sido dito pelo cliente."""
    text = _clean_text(text, 40)
    if not text:
        return ""
    for m in re.finditer(r"(r\$\s*)?(\d{1,5}(?:[.,]\d{1,2})?)(\s*reais)?", text, re.IGNORECASE):
        if not (m.group(1) or m.group(3) or "," in m.group(2)):
            continue  # "Leve 2", "3 unidades": quantidade, não preço
        cents = price_to_cents(m.group(2))
        if cents and not trusted and cents not in grounded:
            return ""
    return text


def ready_for_render(briefing):
    """Bloqueia a geração enquanto houver produto sem preço confirmado."""
    if not briefing["produtos"]:
        return False, "Nenhum produto no briefing."
    missing = [p["nome"] for p in briefing["produtos"] if not p["preco"]]
    if missing:
        return False, "Sem preço: " + ", ".join(missing[:5])
    unconfirmed = [p["nome"] for p in briefing["produtos"] if any(
        c["produto"] == p["nome"] for c in briefing.get("confirmar") or [])]
    if unconfirmed:
        return False, "Preço a confirmar: " + ", ".join(unconfirmed[:5])
    return True, ""


def scene_prompt(briefing):
    """Prompt do cenário (inglês funciona melhor no Z-Image). Sem produtos e sem texto."""
    parts = []
    if briefing.get("cenario"):
        parts.append(briefing["cenario"])
    theme = THEMES.get(strip_accents((briefing.get("tema") or "").lower()))
    if theme:
        parts.append(theme)
    elif briefing.get("tema"):
        parts.append(briefing["tema"])
    if not parts:
        parts.append("bright modern supermarket promotional backdrop, soft gradient, subtle bokeh lights")
    parts.append("high quality advertising photography, vertical composition")
    parts.append(SCENE_GUARDRAILS)
    return ", ".join(parts)


def assistant_summary(briefing, mode_note=""):
    lines = []
    fmt = FORMATS[briefing["formato"]]["nome"]
    lines.append(f"Formato: {fmt}" + (f" · Tema: {briefing['tema']}" if briefing["tema"] else ""))
    if briefing["loja"]:
        lines.append(f"Loja: {briefing['loja']}")
    if briefing["produtos"]:
        lines.append(f"{len(briefing['produtos'])} produto(s) no encarte:")
        for p in briefing["produtos"][:12]:
            price = p["preco"] or "sem preço"
            de = f" (de {p['preco_de']})" if p["preco_de"] else ""
            lines.append(f"• {p['nome']} — {price}{de}")
    if briefing["validade"]:
        lines.append(f"Validade: {briefing['validade']}")
    if briefing["pendencias"]:
        lines.append("Falta:")
        lines.extend(f"– {x}" for x in briefing["pendencias"][:6])
    else:
        lines.append("Tudo certo. Revise o painel de briefing e clique em “Gerar encarte”.")
    if mode_note:
        lines.append(mode_note)
    return "\n".join(lines)

