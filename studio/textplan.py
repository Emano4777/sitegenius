"""Etapa A: entende os textos que o usuário quer na imagem (conteúdo exato, papel, posição e estilo).

O modelo de imagem nunca desenha letras. Este módulo extrai do pedido, em linguagem natural, o que deve ser
escrito, e studio/typography.py aplica esses textos sobre a arte com fontes reais.

Regras de fidelidade:
- O conteúdo de cada texto é sempre um trecho literal do pedido do usuário (grafia, acentos, pontuação e números).
- O modelo de linguagem pode sugerir textos, papéis e posições, mas um texto sugerido só é aceito se aparecer
  literalmente no pedido. Nada é inventado: preços, nomes e chamadas vêm só do que o usuário digitou.
- Maiúsculas/minúsculas são preservadas: nenhuma fonte "só maiúsculas" é usada em texto com minúsculas.
"""
import re
import unicodedata

ROLES = ("titulo", "subtitulo", "descricao", "preco_de", "preco", "cta", "marca", "contato", "texto")
POSITIONS = ("auto", "topo", "centro", "rodape", "esquerda", "direita", "topo_esquerda", "topo_direita",
             "base_esquerda", "base_direita", "abaixo_titulo")
TYPE_STYLES = ("moderno", "minimalista", "elegante", "luxo", "futurista", "tecnologico", "impacto", "divertido",
               "manuscrito")
EFFECTS = ("auto", "nenhum", "sombra", "brilho", "contorno", "gradiente", "metalico")
MAX_BLOCKS = 8
MAX_TEXT = 140

# Ordem de leitura dentro de um grupo (hierarquia visual).
ROLE_ORDER = {"marca": 0, "titulo": 1, "subtitulo": 2, "texto": 3, "descricao": 4, "preco_de": 5, "preco": 6,
              "cta": 7, "contato": 8}


def _fold(text):
    """Minúsculas e sem acentos, mantendo o mesmo comprimento (para casar posições com o texto original)."""
    out = []
    for ch in str(text or ""):
        base = unicodedata.normalize("NFD", ch)[0]
        out.append(base.lower() if len(base.lower()) == 1 else ch)
    return "".join(out)


def _nfc(text):
    return unicodedata.normalize("NFC", " ".join(str(text or "").split()))


# ----------------------------------------------------------------------------------- posições

_POS_RULES = [
    ("abaixo_titulo", r"(logo )?(abaixo|embaixo|debaixo|sob) d[oa] (t[ií]tulo|nome|marca|logo|texto principal)"),
    ("topo_direita", r"canto superior direito|superior direit|(topo|alto|cima)( [aà]| na| no)? direit|direit[ao] (do|no) topo"),
    ("topo_esquerda", r"canto superior esquerdo|superior esquerd|(topo|alto|cima)( [aà]| na| no)? esquerd|esquerd[ao] (do|no) topo"),
    ("base_direita", r"canto inferior direito|inferior direit|(rodap[eé]|baixo|base)( [aà]| na| no)? direit"),
    ("base_esquerda", r"canto inferior esquerdo|inferior esquerd|(rodap[eé]|baixo|base)( [aà]| na| no)? esquerd"),
    ("rodape", r"rodap[eé]|parte (de baixo|inferior)|\bembaixo\b|\bna base\b|\bem baixo\b|\bno p[eé] da"),
    ("topo", r"\bno topo\b|\btopo\b|parte (de cima|superior)|\bno alto\b|\bem cima\b|cabe[cç]alho"),
    ("centro", r"\bcentr|\bno meio\b|\bao meio\b"),
    ("esquerda", r"(lado|canto|parte)? ?esquerd"),
    ("direita", r"(lado|canto|parte)? ?direit"),
]


def detect_position(fragment):
    t = _fold(fragment)
    for key, pattern in _POS_RULES:
        if re.search(_fold(pattern), t):
            return key
    return "auto"


_POS_PHRASE = re.compile(
    r"\s*\b(no|na|ao|em|do|da|bem|logo|alinhad[oa]s?( [aà])?)?\s*(canto superior direito|canto superior esquerdo|"
    r"canto inferior direito|canto inferior esquerdo|topo|rodap[eé]|centro|meio|parte de cima|parte de baixo|"
    r"parte superior|parte inferior|lado esquerdo|lado direito|esquerda|direita|abaixo d[oa] t[ií]tulo|embaixo)\b.*$",
    re.IGNORECASE,
)


# ----------------------------------------------------------------------------------- papéis

_ROLE_RULES = [
    ("subtitulo", r"subt[ií]tulo|slogan|frase|legenda|linha de apoio|tagline"),
    ("titulo", r"t[ií]tulo|headline|chamada principal|manchete"),
    ("cta", r"bot[aã]o|chamada para a[cç][aã]o|\bcta\b|chamada"),
    ("descricao", r"descri[cç][aã]o|texto menor|detalhes|informa[cç][aã]o|benef[ií]cio"),
    ("contato", r"telefone|whats|contato|endere[cç]o"),
    ("marca", r"\bnome\b|marca|empresa|loja|logo|assinatura"),
]

_PRICE_RE = re.compile(r"(?:R\$\s?)(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)|\b(\d{1,3}(?:\.\d{3})*,\d{2})\b(?:\s*reais)?"
                       r"|\b(\d+(?:,\d{2})?)\s*reais\b", re.IGNORECASE)


_PRICE_BIG = re.compile(r"(?:R\$\s?)?\b(\d{1,3}(?:[.,]\d{1,3})?\s*(?:mil|milh[aã]o|milh[oõ]es))\b(?:\s*(?:de\s+)?reais)?",
                        re.IGNORECASE)


def detect_role(fragment):
    t = _fold(fragment)
    for key, pattern in _ROLE_RULES:
        if re.search(_fold(pattern), t):
            return key
    return "texto"


def format_price(raw):
    """Formata como no Brasil sem mudar os algarismos digitados: 199,90 -> R$ 199,90; 199 -> R$ 199."""
    return "R$ " + raw.strip()


# ----------------------------------------------------------------------------------- estilo

_TYPE_STYLE_RULES = [
    ("futurista", r"futur|cyber|neon|sci-?fi|hologr|gamer|espacial|galax"),
    ("tecnologico", r"tecnolog|tech\b|digital|software|startup|inova|intelig[eê]ncia|circuit"),
    ("luxo", r"lux|premium|exclusiv|joia|joalher"),
    ("elegante", r"elegant|sofistic|chique|casamento|noiva|requint|classic|cl[aá]ssic|vinho|spa\b"),
    ("minimalista", r"minimal|clean|simples|discret"),
    ("impacto", r"promo|oferta|liquida|black friday|desconto|imperd|queima|super ?pre[cç]o|an[uú]ncio|meta ads|venda"),
    ("divertido", r"infantil|crian[cç]a|divertid|festa|anivers|colorid|brincad"),
    ("manuscrito", r"manuscrit|caligraf|assinatura|escrita a m[aã]o|cursiv"),
    ("moderno", r"modern|contempor|arrojad"),
]
_STYLE_FROM_CREATIVE = {"minimalista": "minimalista", "logo": "moderno", "cinema": "elegante", "imovel": "elegante",
                        "produto": "moderno", "3d": "moderno", "anime": "divertido", "ilustracao": "divertido",
                        "aquarela": "elegante"}

_EFFECT_RULES = [
    ("metalico", r"met[aá]lic|cromad|dourad|prateado|ouro|gold|chrome"),
    ("brilho", r"brilh|neon|glow|luminos|reluz"),
    ("contorno", r"contorno|borda|outline|delineado"),
    ("gradiente", r"gradiente|degrad"),
    ("sombra", r"sombra|shadow|relevo"),
]

COLOR_WORDS = {
    "branc": (255, 255, 255), "pret": (20, 20, 24), "amarel": (255, 204, 0), "vermelh": (226, 35, 42),
    "azul": (40, 130, 255), "verde": (34, 170, 90), "rosa": (255, 92, 168), "roxo": (140, 82, 255),
    "lilas": (190, 150, 255), "laranja": (255, 128, 0), "dourad": (212, 175, 55), "prat": (205, 210, 216),
    "cinza": (160, 164, 170), "vinho": (128, 20, 48), "turquesa": (0, 200, 200), "ciano": (0, 210, 255),
}
_COLOR_CONTEXT = re.compile(r"(texto|letra|fonte|escrit|t[ií]tulo|nome|tipografia)s?\s+(em\s+|na cor\s+|de cor\s+|cor\s+)?"
                            r"(?P<cor>[a-z]+)", re.IGNORECASE)


def detect_type_style(text, creative_style=None):
    t = _fold(text)
    for key, pattern in _TYPE_STYLE_RULES:
        if re.search(_fold(pattern), t):
            return key
    return _STYLE_FROM_CREATIVE.get(creative_style or "", "moderno")


def detect_effect(text):
    t = _fold(text)
    # Só conta como efeito de texto quando fala do texto (ex.: "letras douradas", "texto com brilho").
    if not re.search(r"(texto|letra|fonte|escrit|t[ií]tulo|nome|tipografia)", t):
        return "auto"
    for key, pattern in _EFFECT_RULES:
        if re.search(_fold(pattern), t):
            return key
    return "auto"


def detect_color(text):
    for match in _COLOR_CONTEXT.finditer(_fold(text)):
        word = match.group("cor")
        for prefix, rgb in COLOR_WORDS.items():
            if word.startswith(prefix):
                return list(rgb)
    return None


_SURFACE = re.compile(r"\b(na|no|numa|num|sobre a|sobre o)\s+(parede|fachada|letreiro|placa|camiseta|camisa|caneca|vitrine|"
                      r"muro|outdoor|embalagem|caixa|garrafa|r[oó]tulo|lata|copo|ve[ií]culo|carro|bon[eé]|tela|quadro)\b",
                      re.IGNORECASE)


def detect_surface(text):
    match = _SURFACE.search(text or "")
    return match.group(2).lower() if match else None


# ----------------------------------------------------------------------------------- extração

_QUOTES = re.compile(r"[\"“”«»]([^\"“”«»\n]{1,140})[\"“”«»]|(?<![\w])'([^'\n]{1,140})'(?![\w])")
_LABELED = re.compile(
    r"(?P<label>t[ií]tulo|subt[ií]tulo|slogan|frase|chamada|bot[aã]o|texto|nome|marca|dizeres|legenda|cta|descri[cç][aã]o|"
    r"contato|telefone|whatsapp|site|instagram)"
    r"\s*(?:principal\s*)?[:=]\s*(?P<value>[^\n;]+?)(?=\s*(?:[;\n]|,\s*(?:(?:t[ií]tulo|subt[ií]tulo|slogan|frase|chamada|"
    r"bot[aã]o|texto|nome|marca|pre[cç]o|legenda|cta|descri[cç][aã]o|contato|telefone|whatsapp|site|instagram)\b|"
    r"(?:de\s+|por\s+)?R\$)|\.\s|\.$|$))",
    re.IGNORECASE,
)
_NAMED = re.compile(
    r"(?P<label>com o nome|com a marca|com o texto|com a frase|com os dizeres|com o t[ií]tulo|escrit[oa]|nome da empresa|"
    r"nome da loja|marca|minha empresa|nossa empresa|da empresa|empresa|minha loja|nossa loja|da loja|loja|"
    r"meu neg[oó]cio|meu restaurante|minha cl[ií]nica|meu sal[aã]o|minha academia|minha marca)\s+(?:de\s+|chamad[ao]\s+)?(?P<value>[A-ZÀ-Ý0-9][\wÀ-ÿ&'’.+-]*(?:\s+(?:(?:de|da|do|das|dos|e|&|-)\s+)?"
    r"[A-ZÀ-Ý0-9][\wÀ-ÿ&'’.+-]*)*)"
)
_NAME_RUN = r"[A-ZÀ-Ý0-9][\wÀ-ÿ&'’.+-]*(?:\s+(?:(?:de|da|do|das|dos|e|&|-)\s+)?[A-ZÀ-Ý0-9][\wÀ-ÿ&'’.+-]*)*"
_NAMED_BEFORE = re.compile(r"(?P<value>" + _NAME_RUN + r")\s+escrit[oa]s?\b")
# "coloca aceita financiamento", "escreve Sabor de Casa no topo": texto literal depois do verbo.
_PUT = re.compile(r"\b(?:coloca|coloque|p[oõ]e|ponha|escreve|escreva|adiciona|adicione|inclui|inclua|bota|bote)\s+"
                  r"(?:tamb[eé]m\s+)?(?:o texto\s+|a frase\s+|escrito\s+)?(?P<value>[^,.;\n]+)", re.IGNORECASE)
_GENERIC_START = re.compile(r"^(um|uma|o|a|os|as|meu|minha|meus|minhas|seu|sua|algum|alguma|isso|isto|ele|ela|mais|menos)\b",
                            re.IGNORECASE)
_GENERIC_WORDS = re.compile(r"\b(frase|texto|t[ií]tulo|subt[ií]tulo|slogan|pre[cç]o|nome|logo|logotipo|imagem|foto|fundo|"
                            r"cor|cores|luz|pessoa|produto|chamada|mensagem|bot[aã]o)\b", re.IGNORECASE)
_CONDITION = re.compile(r"aceita|financ|parcel|entrega|frete|gr[aá]tis|garantia|desconto|[aà] vista|sem juros|"
                        r"pronta entrega|lan[cç]amento|imperd[ií]vel|[uú]ltimas unidades|s[oó] hoje", re.IGNORECASE)
# Pedido de texto sem o texto: "coloca uma frase de boas-vindas na parte superior".
_TEXT_REQUEST = re.compile(r"\b(?:uma|um)\s+(?P<tipo>frase|slogan|chamada|mensagem|texto|t[ií]tulo)\b"
                           r"(?:\s+(?:de|da|do|com|para)\s+(?P<tema>[\wÀ-ÿ-]+(?:\s+[\wÀ-ÿ-]+)?))?", re.IGNORECASE)
SUGGESTIONS = {"boas-vindas": "Seja bem-vindo!", "boas vindas": "Seja bem-vindo!", "bem-vindo": "Seja bem-vindo!",
               "agradecimento": "Obrigado pela preferência!", "natal": "Feliz Natal!", "ano novo": "Feliz Ano Novo!",
               "pascoa": "Feliz Páscoa!", "páscoa": "Feliz Páscoa!"}
_CTA_START = re.compile(r"^(compre|pe[cç]a|ligue|saiba|agende|garanta|aproveite|chame|fale|reserve|visite|confira|"
                        r"clique|baixe|cadastre|inscreva|matricule|experimente|venha|acesse|encomende|solicite)\b", re.IGNORECASE)
_LABELED_FREE = re.compile(
    r"\b(?P<label>t[ií]tulo|subt[ií]tulo|slogan|frase|chamada|bot[aã]o)\s+(?P<value>[A-ZÀ-Ý0-9][^,;\n]*?)"
    r"(?=\s*(?:[,;\n]|\.\s|\.$|$|\s+e\s+(?:coloca|coloque|o\s|a\s|um\s|uma\s|com\s|no\s|na\s|subt|t[ií]t|pre[cç]o|bot)))")
_BRAND_POS = re.compile(r"\b(?:meu|o|nosso|seu)\s+nome\s+(?:da empresa\s+|da loja\s+)?(?P<rest>.{0,40})", re.IGNORECASE)
_EMPHASIS = re.compile(r"pre[cç]o\s+(?:em\s+)?(?:destaque|grande|bem grande|chamativo)|destaque (?:no|para o) pre[cç]o",
                       re.IGNORECASE)

_DELIMS = re.compile(r"[,;\n]|\.\s|\s+e\s+(?=(o|a|um|uma)?\s*(t[ií]tulo|subt[ií]tulo|pre[cç]o|slogan|frase|bot[aã]o|chamada|nome)\b)",
                     re.IGNORECASE)


def _clause(text, start, end):
    """Trecho da frase que contém o texto (entre vírgulas, ponto e vírgula, quebras ou outro texto)."""
    left = max([m.end() for m in _DELIMS.finditer(text, 0, start)] + [0])
    right_match = _DELIMS.search(text, end)
    right = right_match.start() if right_match else len(text)
    return text[left:start], text[end:right]


def _block(texto, papel, posicao, start, source):
    return {"texto": texto, "papel": papel, "posicao": posicao, "inicio": start, "origem": source}


def extract_blocks(message):
    """Textos pedidos, na ordem em que aparecem. Cada um é literal no pedido."""
    message = str(message or "")
    taken, blocks = [], []

    def free(a, b):
        return all(b <= x or a >= y for x, y in taken)

    for match in _QUOTES.finditer(message):
        value = match.group(1) or match.group(2)
        start = match.start(1) if match.group(1) else match.start(2)
        value = _nfc(value)
        if not value or not re.search(r"\w", value):
            continue
        before, after = _clause(message, match.start(), match.end())
        after = re.split(r"[\"“”«»]", after, maxsplit=1)[0]
        before = re.split(r"[\"“”«»]", before)[-1][-28:]
        taken.append((match.start(), match.end()))
        role = detect_role(before)
        if role == "texto" and _PRICE_RE.fullmatch(value.strip()):
            role = "preco"
        position = detect_position(after[:80])
        blocks.append(_block(value, role, position if position != "auto" else detect_position(before), start, "aspas"))

    for match in _LABELED.finditer(message):
        if not free(match.start("value"), match.end("value")):
            continue
        value = _POS_PHRASE.sub("", match.group("value")).strip(" .:-")
        value = _nfc(value)
        if not value or _PRICE_RE.fullmatch(value) or len(value) > MAX_TEXT:
            continue
        taken.append((match.start(), match.end()))
        _, after = _clause(message, match.end(), match.end())
        blocks.append(_block(value, detect_role(match.group("label")), detect_position(match.group(0) + " " + after[:80]),
                             match.start("value"), "rotulo"))

    for match in _LABELED_FREE.finditer(message):
        if not free(match.start("value"), match.end("value")):
            continue
        value = _nfc(_POS_PHRASE.sub("", match.group("value")).strip(" .:-"))
        if not value or len(value) > MAX_TEXT or _PRICE_RE.fullmatch(value):
            continue
        taken.append((match.start(), match.end()))
        blocks.append(_block(value, detect_role(match.group("label")), detect_position(match.group(0)),
                             match.start("value"), "rotulo"))

    for match in _NAMED.finditer(message):
        if not free(match.start("value"), match.end("value")):
            continue
        value = _nfc(_POS_PHRASE.sub("", match.group("value")).strip(" .:-"))
        if len(value) < 2:
            continue
        taken.append((match.start(), match.end()))
        before, after = _clause(message, match.start(), match.end())
        role = "marca" if re.search(r"nome|marca|empresa|loja", _fold(match.group("label"))) else detect_role(match.group("label"))
        blocks.append(_block(value, role, detect_position(before[-40:] + " " + after[:80]), match.start("value"), "nome"))

    for match in _NAMED_BEFORE.finditer(message):
        value = _nfc(match.group("value"))
        if not free(match.start("value"), match.end("value")) or len(value) < 2:
            continue
        # Ignora palavra comum no início da frase ("Crie", "Faça"…): o nome precisa de 2+ letras maiúsculas ou ser curto.
        words = value.split()
        while words and words[0].lower() in ("crie", "cria", "faça", "faz", "quero", "gere", "uma", "um") :
            words = words[1:]
        value = " ".join(words)
        if len(value) < 2:
            continue
        taken.append((match.start(), match.end()))
        _, after = _clause(message, match.end(), match.end())
        blocks.append(_block(value, "marca", detect_position(after[:60]), match.start("value"), "nome"))

    for match in _PUT.finditer(message):
        raw = match.group("value")
        value = _nfc(_POS_PHRASE.sub("", re.sub(r"\s+em destaque.*$", "", raw)).strip(" .:-"))
        if (not value or len(value) > MAX_TEXT or not free(match.start("value"), match.end("value"))
                or _GENERIC_START.search(value) or _GENERIC_WORDS.search(value) or _PRICE_RE.search(value)
                or _PRICE_BIG.search(value)):
            continue
        taken.append((match.start("value"), match.start("value") + len(value)))
        role = "cta" if _CTA_START.search(value) else ("subtitulo" if _CONDITION.search(value) else "texto")
        blocks.append(_block(value, role, detect_position(raw[len(value):] + " " + _clause(message, match.end(), match.end())[1][:40]),
                             match.start("value"), "verbo"))

    for match in _PRICE_BIG.finditer(message):
        if not free(match.start(), match.end()):
            continue
        taken.append((match.start(), match.end()))
        _, after = _clause(message, match.start(), match.end())
        blocks.append(_block(format_price(_nfc(match.group(1))), "preco", detect_position(after[:80]), match.start(), "preco"))

    # Preços: só os digitados. "de R$ 299 por R$ 199" -> preço antigo riscado + preço atual.
    prices = []
    for match in _PRICE_RE.finditer(message):
        if not free(match.start(), match.end()):
            continue
        raw = match.group(1) or match.group(2) or match.group(3)
        before, after = _clause(message, match.start(), match.end())
        prices.append((match, raw, before, after))
    for i, (match, raw, before, after) in enumerate(prices):
        role = "preco"
        if re.search(r"\bde\s*$", _fold(message[max(0, match.start() - 4):match.start()])) and i + 1 < len(prices):
            role = "preco_de"
        position = detect_position(after[:80] or before[-40:])
        blocks.append(_block(format_price(raw), role, position, match.start(), "preco"))

    blocks.sort(key=lambda b: b["inicio"])
    return blocks[:MAX_BLOCKS]


def _present(text, message):
    """O texto aparece literalmente no pedido? (espaços normalizados; acentos e maiúsculas contam).

    Preço: os algarismos e a unidade digitados precisam estar no pedido ("R$ 540 mil" <- "540 mil reais")."""
    text, message = _nfc(text), _nfc(message)
    if text in message or text.replace("R$ ", "R$") in message.replace("R$ ", "R$"):
        return True
    if text.startswith("R$ "):
        return text[3:] in message
    return False


def merge_model_blocks(blocks, model_blocks, message):
    """Aceita sugestões do modelo só se o texto for literal no pedido. O modelo ajuda com papel e posição."""
    if not isinstance(model_blocks, list):
        return blocks
    by_text = {b["texto"]: b for b in blocks}
    for item in model_blocks[:MAX_BLOCKS]:
        if not isinstance(item, dict):
            continue
        text = _nfc(item.get("texto"))[:MAX_TEXT]
        if not text or not _present(text, message):
            continue
        role = item.get("papel") if item.get("papel") in ROLES else None
        position = item.get("posicao") if item.get("posicao") in POSITIONS else None
        if text in by_text:
            current = by_text[text]
            if role and current["papel"] == "texto":
                current["papel"] = role
            if position and current["posicao"] == "auto":
                current["posicao"] = position
            continue
        # Evita duplicar um trecho já coberto por outro bloco (ex.: o modelo extraiu só parte do título).
        if any(text in b["texto"] or b["texto"] in text for b in blocks):
            continue
        if len(blocks) >= MAX_BLOCKS:
            break
        new = _block(text, role or "texto", position or "auto", _nfc(message).find(text), "modelo")
        blocks.append(new)
        by_text[text] = new
    blocks.sort(key=lambda b: b["inicio"] if b["inicio"] >= 0 else 9999)
    return blocks


def assign_hierarchy(blocks):
    """Papéis que faltam: o primeiro texto curto vira título; os seguintes, subtítulo ou descrição."""
    has_title = any(b["papel"] in ("titulo", "marca") for b in blocks)
    for b in blocks:
        if b["papel"] != "texto":
            continue
        if re.search(r"(www\.|\.com|@\w|\(\d{2}\)|\d{4,5}-\d{4})", b["texto"]):
            b["papel"] = "contato"
        elif not has_title and len(b["texto"]) <= 60:
            b["papel"], has_title = "titulo", True
        elif len(b["texto"]) > 70:
            b["papel"] = "descricao"
        else:
            b["papel"] = "subtitulo"
    # Uma marca sozinha ou com subtítulo é o título da peça.
    if not any(b["papel"] == "titulo" for b in blocks):
        for b in blocks:
            if b["papel"] == "marca":
                b["papel"] = "titulo"
                break
    return blocks


def missing_texts(message, blocks):
    """Textos pedidos sem o conteúdo ("uma frase de boas-vindas"): nunca inventar; perguntar com uma sugestão."""
    out = []
    for match in _TEXT_REQUEST.finditer(message or ""):
        tipo = _fold(match.group("tipo"))
        papel = "titulo" if tipo == "titulo" else ("cta" if tipo == "chamada" else "subtitulo")
        if any(b["papel"] == papel or (papel == "subtitulo" and b["papel"] == "texto") for b in blocks):
            continue
        tema = re.sub(r"\s+(na|no|em|do|da|de|ao|para)$", "", (match.group("tema") or "").strip(), flags=re.IGNORECASE)
        _, after = _clause(message, match.end(), match.end())
        sugestao = next((v for k, v in SUGGESTIONS.items() if k in _fold(tema)), None)
        out.append({"tipo": match.group("tipo").lower(), "tema": tema, "papel": papel,
                    "posicao": detect_position(after[:60]), "sugestao": sugestao})
    return out[:2]


def plan_from_request(message, model_blocks=None, creative_style=None, model_style=None, model_effect=None):
    """Plano de texto validado ou None (pedido sem textos)."""
    blocks = merge_model_blocks(extract_blocks(message), model_blocks, message)
    pending = missing_texts(message, blocks)
    if not blocks and not pending:
        return None
    blocks = assign_hierarchy(blocks)
    # "meu nome no centro": posição do nome da marca.
    brand = next((b for b in blocks if b["papel"] in ("titulo", "marca") and b["posicao"] == "auto"), None)
    pos_match = _BRAND_POS.search(message or "")
    if brand and pos_match and detect_position(pos_match.group("rest")) != "auto":
        brand["posicao"] = detect_position(pos_match.group("rest"))
    context = message or ""
    for b in blocks:
        context = context.replace(b["texto"], " ")
    style = model_style if model_style in TYPE_STYLES else detect_type_style(context, creative_style)
    effect = detect_effect(message)
    if effect == "auto" and model_effect in EFFECTS:
        effect = model_effect
    return {
        "versao": 1,
        "blocos": [{"texto": b["texto"][:MAX_TEXT], "papel": b["papel"], "posicao": b["posicao"],
                    **({"destaque": True} if b["papel"] == "preco" and _EMPHASIS.search(message or "") else {})}
                   for b in blocks],
        "estilo": style,
        "efeito": effect,
        "cor": detect_color(message),
        "superficie": detect_surface(message),
        "destaque": "preco" if _EMPHASIS.search(message or "") else None,
        "pendentes": pending,
    }


def plan_texts(plan):
    return [b["texto"] for b in (plan or {}).get("blocos") or []]


def validate_plan(plan, sources):
    """Revalida um plano salvo: cada texto precisa estar literalmente em algum pedido do usuário."""
    if not isinstance(plan, dict) or not isinstance(plan.get("blocos"), list):
        return None
    blocks = []
    for b in plan["blocos"][:MAX_BLOCKS]:
        if not isinstance(b, dict):
            continue
        text = _nfc(b.get("texto"))[:MAX_TEXT]
        # "confirmado": texto aprovado pelo usuário numa pergunta (marcado só pelo servidor).
        if text and (b.get("confirmado") is True or any(_present(text, s) for s in sources)):
            blocks.append({"texto": text, "papel": b.get("papel") if b.get("papel") in ROLES else "texto",
                           "posicao": b.get("posicao") if b.get("posicao") in POSITIONS else "auto",
                           **({"confirmado": True} if b.get("confirmado") is True else {}),
                           **({"destaque": True} if b.get("destaque") is True else {}),
                           **({"escala": max(0.5, min(2.2, float(b["escala"])))}
                              if isinstance(b.get("escala"), (int, float)) else {})})
    if not blocks:
        return None
    color = plan.get("cor")
    color = [max(0, min(255, int(c))) for c in color][:3] if isinstance(color, list) and len(color) == 3 else None
    return {"versao": 1, "blocos": assign_hierarchy(blocks),
            "estilo": plan.get("estilo") if plan.get("estilo") in TYPE_STYLES else "moderno",
            "efeito": plan.get("efeito") if plan.get("efeito") in EFFECTS else "auto",
            "cor": color, "superficie": plan.get("superficie") if isinstance(plan.get("superficie"), str) else None,
            "destaque": "preco" if plan.get("destaque") == "preco" else None, "pendentes": []}


# ----------------------------------------------------------------------------------- zonas

def resolve_zones(plan, aspect_ratio):
    """Posição final de cada bloco (sem 'auto'), decidida ANTES da geração para a arte reservar o espaço."""
    blocks = [dict(b) for b in plan["blocos"]]
    landscape = aspect_ratio >= 1.3
    explicit = [b for b in blocks if b["posicao"] not in ("auto", "abaixo_titulo")]
    title = next((b for b in blocks if b["papel"] == "titulo"), None)

    def default_for(b):
        if len(blocks) == 1:
            return "esquerda" if landscape and len(b["texto"]) > 24 else ("centro" if b["papel"] in ("titulo", "marca") else "rodape")
        if landscape:
            return "esquerda"
        if b["papel"] in ("preco", "preco_de", "cta", "contato"):
            return "rodape"
        return "topo"

    title_zone = None
    if title:
        title_zone = title["posicao"] if title["posicao"] not in ("auto", "abaixo_titulo") else None
        if not title_zone:
            # Sem posição do título: segue a maioria dos textos com posição explícita do mesmo tipo.
            title_zone = default_for(title)
        title["posicao"] = title_zone
    for b in blocks:
        if b["posicao"] == "abaixo_titulo":
            b["posicao"] = title_zone or default_for(b)
        elif b["posicao"] == "auto":
            if b["papel"] in ("subtitulo", "descricao", "marca", "texto") and title_zone:
                b["posicao"] = title_zone
            else:
                b["posicao"] = default_for(b)
    del explicit
    return blocks


ZONE_HINTS = {
    "topo": "the upper third of the frame is calm, open negative space (clear sky, smooth wall or soft gradient)",
    "rodape": "the lower quarter of the frame is calm, uncluttered negative space",
    "centro": "the center of the frame is open and calm, with the visual elements arranged around the edges",
    "esquerda": "the main subject is placed on the right side and the left half is calm, open negative space",
    "direita": "the main subject is placed on the left side and the right half is calm, open negative space",
    "topo_direita": "the upper right corner is calm and uncluttered",
    "topo_esquerda": "the upper left corner is calm and uncluttered",
    "base_direita": "the lower right corner is calm and uncluttered",
    "base_esquerda": "the lower left corner is calm and uncluttered",
}


def composition_hint(blocks):
    zones = []
    for b in blocks:
        if b["posicao"] not in zones:
            zones.append(b["posicao"])
    return "; ".join(ZONE_HINTS[z] for z in zones if z in ZONE_HINTS)
