"""Etapas C e D: aplica os textos reais sobre a arte e valida o resultado antes da entrega.

A arte chega sem letras (o modelo de imagem só faz cenário, elementos e luz). Aqui cada texto do plano
(studio/textplan.py) é desenhado com fontes de verdade (OFL, em studio/fonts):
- posição pedida pelo usuário (ou escolhida antes da geração, para a arte já reservar o espaço);
- fonte, peso e efeito conforme a identidade visual (moderno, elegante, futurista…);
- tamanho, quebra de linha e alinhamento automáticos; contraste garantido por sombra, halo, véu ou painel.

Validação (sem OCR: compara com os dados do pedido e mede os pixels):
- todos os textos presentes e idênticos ao pedido (o texto desenhado é reconstruído a partir das linhas);
- todos os caracteres existem na fonte (sem "quadradinhos");
- nada cortado ou fora da área segura; sem sobreposição entre grupos;
- contraste medido nos pixels reais atrás de cada linha (WCAG: 4,5:1, ou 3:1 em texto grande);
- tamanho mínimo legível. Problemas são corrigidos automaticamente (reduz, move, reforça o fundo).
"""
import io
import os
import re

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from . import textplan

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FAMILIES = {
    "poppins": {"files": {400: "Poppins-Regular.ttf", 600: "Poppins-SemiBold.ttf", 700: "Poppins-Bold.ttf",
                          800: "Poppins-ExtraBold.ttf"}},
    "montserrat": {"vf": "Montserrat-VF.ttf", "range": (100, 900)},
    "playfair": {"vf": "PlayfairDisplay-VF.ttf", "range": (400, 900)},
    "cinzel": {"vf": "Cinzel-VF.ttf", "range": (400, 900), "caps": True},
    "orbitron": {"vf": "Orbitron-VF.ttf", "range": (400, 900)},
    "anton": {"files": {400: "Anton-Regular.ttf"}},
    "bebas": {"files": {400: "BebasNeue-Regular.ttf"}, "caps": True},
    "archivo": {"files": {900: "ArchivoBlack-Regular.ttf"}},
    "dmserif": {"files": {400: "DMSerifDisplay-Regular.ttf"}},
    "greatvibes": {"files": {400: "GreatVibes-Regular.ttf"}},
}
FALLBACK = ("poppins", 700)

GOLD = [(122, 88, 24), (247, 226, 150), (196, 152, 52), (255, 243, 196), (150, 110, 34)]
SILVER = [(96, 98, 104), (246, 247, 250), (160, 164, 172), (255, 255, 255), (112, 116, 124)]

# Identidade tipográfica. caps_title: fonte só de maiúsculas, usada SÓ se o texto já estiver em maiúsculas.
THEMES = {
    "moderno": {"title": ("poppins", 800), "sub": ("poppins", 600), "body": ("poppins", 400), "cta": ("poppins", 700),
                "price": ("poppins", 800), "effect": "sombra", "accent": (255, 196, 0), "tracking_sub": 0.0},
    "minimalista": {"title": ("montserrat", 600), "sub": ("montserrat", 400), "body": ("montserrat", 400),
                    "cta": ("montserrat", 600), "price": ("montserrat", 700), "effect": "nenhum",
                    "accent": (255, 255, 255), "tracking_sub": 0.08, "tracking_title": 0.02},
    "elegante": {"title": ("playfair", 700), "sub": ("montserrat", 500), "body": ("montserrat", 400),
                 "cta": ("montserrat", 600), "price": ("playfair", 800), "effect": "sombra", "accent": (214, 180, 96),
                 "tracking_sub": 0.12, "ink": (250, 246, 238)},
    "luxo": {"title": ("playfair", 800), "caps_title": ("cinzel", 700), "sub": ("montserrat", 500),
             "body": ("montserrat", 400), "cta": ("montserrat", 600), "price": ("playfair", 800), "effect": "metalico",
             "accent": (214, 180, 96), "tracking_sub": 0.16},
    "futurista": {"title": ("orbitron", 800), "sub": ("montserrat", 500), "body": ("montserrat", 400),
                  "cta": ("orbitron", 700), "price": ("orbitron", 800), "effect": "brilho", "accent": (0, 214, 255),
                  "glow": (0, 190, 255), "tracking_sub": 0.12, "tracking_title": 0.03},
    "tecnologico": {"title": ("montserrat", 800), "sub": ("montserrat", 500), "body": ("montserrat", 400),
                    "cta": ("montserrat", 700), "price": ("montserrat", 800), "effect": "brilho",
                    "accent": (64, 156, 255), "glow": (40, 130, 255), "tracking_sub": 0.06},
    "impacto": {"title": ("anton", 400), "caps_title": ("bebas", 400), "sub": ("poppins", 700),
                "body": ("poppins", 500), "cta": ("poppins", 800), "price": ("poppins", 800), "effect": "sombra",
                "accent": (255, 204, 0), "price_bg": (226, 27, 35), "tracking_sub": 0.0, "tracking_title": 0.01},
    "divertido": {"title": ("archivo", 900), "sub": ("poppins", 700), "body": ("poppins", 500), "cta": ("poppins", 800),
                  "price": ("archivo", 900), "effect": "contorno", "accent": (255, 92, 168), "tracking_sub": 0.0},
    "manuscrito": {"title": ("greatvibes", 400), "sub": ("montserrat", 500), "body": ("montserrat", 400),
                   "cta": ("montserrat", 600), "price": ("playfair", 800), "effect": "sombra", "accent": (214, 180, 96),
                   "tracking_sub": 0.14},
}

# Tamanho de cada papel em relação ao título; e o tipo de fonte usado.
ROLE_SCALE = {"titulo": 1.0, "marca": 0.5, "subtitulo": 0.42, "texto": 0.42, "descricao": 0.3, "preco_de": 0.32,
              "preco": 0.78, "cta": 0.32, "contato": 0.26}
ROLE_FONT = {"titulo": "title", "marca": "sub", "subtitulo": "sub", "texto": "sub", "descricao": "body",
             "preco_de": "body", "preco": "price", "cta": "cta", "contato": "body"}
ROLE_MAX_LINES = {"titulo": 3, "marca": 2, "subtitulo": 3, "texto": 3, "descricao": 5, "preco_de": 1, "preco": 1,
                  "cta": 1, "contato": 2}
ALIGN = {"esquerda": "left", "topo_esquerda": "left", "base_esquerda": "left",
         "direita": "right", "topo_direita": "right", "base_direita": "right"}
TREATMENTS = ("nenhum", "sombra", "halo", "veu", "veu_forte", "painel")

_font_cache, _glyph_cache = {}, {}


# ----------------------------------------------------------------------------------- fontes

def get_font(family, weight, size):
    size = max(6, int(round(size)))
    key = (family, weight, size)
    if key in _font_cache:
        return _font_cache[key]
    spec = FAMILIES.get(family) or FAMILIES[FALLBACK[0]]
    if "vf" in spec:
        font = ImageFont.truetype(os.path.join(FONT_DIR, spec["vf"]), size)
        low, high = spec["range"]
        try:
            font.set_variation_by_axes([max(low, min(high, weight))])
        except (OSError, AttributeError):  # FreeType sem variações: fica o peso padrão
            pass
    else:
        best = min(spec["files"], key=lambda w: abs(w - weight))
        font = ImageFont.truetype(os.path.join(FONT_DIR, spec["files"][best]), size)
    _font_cache[key] = font
    return font


def _glyph_bitmap(font, ch):
    img = Image.new("L", (font.size * 2, font.size * 2))
    ImageDraw.Draw(img).text((font.size // 2, font.size // 2), ch, font=font, fill=255)
    return img.tobytes()


def missing_glyphs(family, weight, text):
    """Caracteres sem desenho na fonte (seriam 'quadradinhos')."""
    font = get_font(family, weight, 32)
    key = (family, weight)
    notdef = _glyph_cache.setdefault((key, None), _glyph_bitmap(font, "￿"))
    missing = []
    for ch in set(text):
        if ch.isspace():
            continue
        ck = (key, ch)
        if ck not in _glyph_cache:
            _glyph_cache[ck] = _glyph_bitmap(font, ch) == notdef
        if _glyph_cache[ck]:
            missing.append(ch)
    return missing


def choose_font(theme, role, text):
    slot = ROLE_FONT.get(role, "sub")
    family, weight = theme.get(slot) or theme["sub"]
    if slot == "title" and theme.get("caps_title") and text == text.upper() and any(c.isalpha() for c in text):
        family, weight = theme["caps_title"]
    if FAMILIES.get(family, {}).get("caps") and text != text.upper():
        family, weight = FALLBACK  # fonte só de maiúsculas mudaria a grafia
    if family == "greatvibes" and len(text) > 28:
        family, weight = theme["sub"]  # caligráfica só em títulos curtos (legibilidade)
    if missing_glyphs(family, weight, text):
        family, weight = FALLBACK
    return family, weight


# ----------------------------------------------------------------------------------- análise da arte

class Analysis:
    """Mapa reduzido de luminância e bordas para escolher onde o texto fica e medir o fundo."""

    def __init__(self, img):
        self.size = img.size
        scale = 160 / max(img.size)
        small = img.convert("L").resize((max(8, int(img.width * scale)), max(8, int(img.height * scale))), Image.BILINEAR)
        self.scale = scale
        self.lum = small
        self.edges = small.filter(ImageFilter.FIND_EDGES)

    def _box(self, rect):
        x0, y0, x1, y1 = rect
        s = self.scale
        w, h = self.lum.size
        box = (max(0, int(x0 * s)), max(0, int(y0 * s)), min(w, max(int(x0 * s) + 1, int(x1 * s))),
               min(h, max(int(y0 * s) + 1, int(y1 * s))))
        return box

    def region(self, rect):
        box = self._box(rect)
        lum = self.lum.crop(box)
        stat = ImageStat.Stat(lum)
        hist = lum.histogram()
        return {"media": stat.mean[0] / 255, "desvio": stat.stddev[0] / 255,
                "p10": _percentile(hist, 0.10) / 255, "p90": _percentile(hist, 0.90) / 255,
                "bordas": ImageStat.Stat(self.edges.crop(box)).mean[0] / 255}


def _percentile(hist, q):
    total = sum(hist)
    if not total:
        return 0
    target, acc = total * q, 0
    for value, count in enumerate(hist):
        acc += count
        if acc >= target:
            return value
    return 255


def _linear(v):
    c = v / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def contrast_ratio(a, b):
    """WCAG entre duas luminâncias de 0-255 (canal L)."""
    la, lb = _linear(a), _linear(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _luma(rgb):
    r, g, b = rgb[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


# ----------------------------------------------------------------------------------- medição e quebra

def text_width(font, text, tracking=0.0):
    if not text:
        return 0
    if not tracking:
        return font.getlength(text)
    return sum(font.getlength(c) for c in text) + tracking * font.size * (len(text) - 1)


_GLUE_NEXT = {"nº", "n°", "Nº", "N°", "R$", "US$", "€", "nº.", "Av.", "Dr.", "Dra.", "Sr.", "Sra.", "—", "-", "–", "&"}


def _glue(words):
    """Palavras que não podem ficar sozinhas no fim da linha ("nº 1", "R$ 9,99", "— texto")."""
    out = []
    for word in words:
        if out and (out[-1].split(" ")[-1] in _GLUE_NEXT or word in ("%", "!", "?")):
            out[-1] = f"{out[-1]} {word}"
        else:
            out.append(word)
    return out


def wrap_balanced(font, text, max_width, max_lines, tracking=0.0):
    """Quebra só em espaços (nunca hifeniza ou corta palavra) e equilibra o comprimento das linhas."""
    words = _glue(text.split())
    if not words or any(text_width(font, w, tracking) > max_width for w in words):
        return None

    def greedy(limit):
        lines, current = [], ""
        for word in words:
            trial = f"{current} {word}".strip()
            if text_width(font, trial, tracking) <= limit or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    lines = greedy(max_width)
    if len(lines) > max_lines:
        return None
    if len(lines) == 1:
        return lines
    best, limit = lines, max_width
    while limit > max_width * 0.4:
        limit *= 0.94
        trial = greedy(limit)
        if len(trial) != len(lines) or any(text_width(font, line, tracking) > limit + 1 for line in trial):
            break
        best = trial
    return best


def _price_parts(text):
    """("R$", inteiro, complemento): centavos (",90", sobrescrito) ou unidade (" mil", " milhão", na base)."""
    match = re.fullmatch(r"R\$\s?(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{2}))?", text.strip())
    if match:
        return "R$", match.group(1), ("," + match.group(2)) if match.group(2) else ""
    match = re.fullmatch(r"R\$\s?(\d{1,3}(?:[.,]\d{1,3})?)\s?(mil|milh[aã]o|milh[oõ]es)", text.strip())
    if match:
        return "R$", match.group(1), " " + match.group(2)
    return None


# ----------------------------------------------------------------------------------- layout de um grupo

class Item:
    """Um bloco já medido: linhas, fonte e caixa (relativa ao grupo)."""

    def __init__(self, block, theme, base_size, width, min_px, color):
        self.block = block
        self.role = block["papel"]
        self.text = block["texto"]
        self.family, self.weight = choose_font(theme, self.role, self.text)
        slot = ROLE_FONT.get(self.role, "sub")
        self.tracking = theme.get("tracking_title", 0.0) if slot == "title" else (
            theme.get("tracking_sub", 0.0) if slot in ("sub", "body") and self.role != "descricao" else 0.0)
        self.size = max(min_px, base_size * ROLE_SCALE.get(self.role, 0.42) * float(block.get("escala", 1.0)))
        if self.role == "preco" and block.get("destaque"):
            self.size *= 1.25
        self.font = get_font(self.family, self.weight, self.size)
        self.color = color
        self.lines, self.width, self.height, self.ok = [], 0, 0, False
        self.kind = "texto"
        price = _price_parts(self.text) if self.role in ("preco", "preco_de") else None
        if self.role == "preco" and price:
            self.kind = "preco"
            self._measure_price(price, width)
        elif self.role == "cta":
            self.kind = "cta"
            self._measure_cta(width)
        else:
            self._measure_text(width)

    def _line_height(self):
        display = self.family in ("anton", "bebas", "orbitron", "archivo", "cinzel") or ROLE_FONT.get(self.role) == "title"
        return self.size * (1.08 if display else 1.28)

    def _measure_text(self, width):
        lines = wrap_balanced(self.font, self.text, width, ROLE_MAX_LINES.get(self.role, 3), self.tracking)
        if not lines:
            return
        self.lines = lines
        self.width = max(text_width(self.font, line, self.tracking) for line in lines)
        self.height = self._line_height() * (len(lines) - 1) + self.size * 1.18
        self.ok = True

    def _measure_price(self, parts, width):
        currency, whole, cents = parts
        big = self.font
        small = get_font(self.family, self.weight, self.size * 0.42)
        unit = cents.startswith(" ")
        tail = get_font(self.family, self.weight, self.size * 0.5) if unit else small
        self.parts = [(currency + " ", small, "base"), (whole, big, "base"), (cents, tail, "base" if unit else "topo")]
        text_w = sum(f.getlength(t) for t, f, _ in self.parts if t)
        self.pad_x, self.pad_y = self.size * 0.32, self.size * 0.2
        self.width = text_w + 2 * self.pad_x
        self.height = self.size * 0.95 + 2 * self.pad_y
        self.lines = [self.text]
        self.ok = self.width <= width

    def _measure_cta(self, width):
        self.pad_x, self.pad_y = self.size * 0.9, self.size * 0.5
        text_w = text_width(self.font, self.text, self.tracking)
        self.lines = [self.text]
        self.width = text_w + 2 * self.pad_x
        self.height = self.size * 1.18 + 2 * self.pad_y
        self.ok = self.width <= width


def _gap(prev, item):
    if prev is None:
        return 0
    if item.kind in ("preco", "cta") or prev.kind in ("preco", "cta"):
        return max(prev.size, item.size) * 0.45
    if prev.role in ("titulo",):
        return item.size * 0.55
    return item.size * 0.45


def layout_group(blocks, theme, zone, canvas_h, scale, min_px, color):
    """Maior tamanho de título em que todo o grupo cabe na zona. Devolve (itens, altura) ou (None, 0)."""
    x0, y0, x1, y1 = zone
    width, height = x1 - x0, y1 - y0
    has_title = any(b["papel"] == "titulo" for b in blocks)
    top = canvas_h * (0.15 if has_title else 0.11) * scale
    if len(blocks) == 1 and blocks[0]["papel"] in ("titulo", "marca"):
        top = canvas_h * 0.17 * scale
    low = max(min_px, canvas_h * 0.03)
    best = None
    for _ in range(18):  # busca binária no tamanho do título
        mid = (low + top) / 2
        items, total, prev = [], 0, None
        fits = True
        for b in blocks:
            item = Item(b, theme, mid, width, min_px, color)
            if not item.ok:
                fits = False
                break
            total += _gap(prev, item) + item.height
            items.append(item)
            prev = item
        if fits and total <= height:
            best = (items, total)
            low = mid
        else:
            top = mid
        if top - low < 1:
            break
    if best is None:
        items, total, prev = [], 0, None
        for b in blocks:
            item = Item(b, theme, low, width, min_px, color)
            if not item.ok:
                return None, 0
            total += _gap(prev, item) + item.height
            items.append(item)
            prev = item
        return items, total
    return best


# ----------------------------------------------------------------------------------- zonas na tela

def zone_rect(name, size, margin):
    w, h = size
    landscape = w / h >= 1.3
    top_h = 0.4 if landscape else 0.34
    rects = {
        "topo": (margin, margin, w - margin, h * top_h),
        "rodape": (margin, h * 0.68, w - margin, h - margin),
        "centro": (w * 0.08, h * 0.28, w * 0.92, h * 0.72),
        "esquerda": (margin, margin, w * 0.5, h - margin),
        "direita": (w * 0.5, margin, w - margin, h - margin),
        "topo_esquerda": (margin, margin, w * 0.55, h * 0.32),
        "topo_direita": (w * 0.45, margin, w - margin, h * 0.32),
        "base_esquerda": (margin, h * 0.7, w * 0.55, h - margin),
        "base_direita": (w * 0.45, h * 0.7, w - margin, h - margin),
    }
    return rects.get(name, rects["centro"])


def _split_shared(zones, size, margin):
    """Zona larga + canto do mesmo lado: a larga recua para o lado oposto, para não se sobreporem."""
    w = size[0]
    out = dict(zones)
    for wide, corners in (("topo", ("topo_direita", "topo_esquerda")), ("rodape", ("base_direita", "base_esquerda"))):
        if wide not in out:
            continue
        x0, y0, x1, y1 = out[wide]
        if corners[0] in out:
            x1 = w * 0.52
        if corners[1] in out:
            x0 = w * 0.48
        if x1 - x0 < w * 0.3:
            x0, x1 = margin, w - margin
        out[wide] = (x0, y0, x1, y1)
    return out


# ----------------------------------------------------------------------------------- desenho

def _gradient(size, stops, vertical=True):
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    length = h if vertical else w
    line = Image.new("RGB", (1, length) if vertical else (length, 1))
    px = line.load()
    n = len(stops) - 1
    for i in range(length):
        t = i / max(1, length - 1) * n
        k = min(n - 1, int(t))
        f = t - k
        a, b = stops[k], stops[k + 1]
        c = tuple(int(a[j] + (b[j] - a[j]) * f) for j in range(3))
        if vertical:
            px[0, i] = c
        else:
            px[i, 0] = c
    return line.resize((w, h), Image.BILINEAR)


def _draw_text_mask(mask, xy, text, font, tracking):
    draw = ImageDraw.Draw(mask)
    if not tracking:
        draw.text(xy, text, font=font, fill=255)
        return
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=255)
        x += font.getlength(ch) + tracking * font.size


class Renderer:
    def __init__(self, img, plan, attempt):
        self.img = img
        self.size = img.size
        self.plan = plan
        self.attempt = attempt
        self.theme = THEMES.get(plan.get("estilo"), THEMES["moderno"])
        self.effect = plan.get("efeito") if plan.get("efeito") not in (None, "auto") else self.theme["effect"]
        user_color = plan.get("cor")
        self.metal = None
        if self.effect == "metalico":
            silver = user_color and user_color[0] > 190 and abs(user_color[0] - user_color[2]) < 20
            self.metal = SILVER if silver else GOLD
        self.user_color = tuple(user_color) if user_color else None
        self.margin = min(self.size) * 0.06
        self.min_px = max(14, self.size[1] * 0.022)

    def text_color(self, region):
        if self.user_color:
            return self.user_color
        light = self.theme.get("ink", (255, 255, 255))
        dark = (22, 22, 28)
        # A cor que dá mais contraste com o pior trecho do fundo (evita painel quando o texto escuro já resolve).
        light_ratio = contrast_ratio(_luma(light), region["p90"] * 255)
        dark_ratio = contrast_ratio(_luma(dark), region["p10"] * 255)
        return light if light_ratio >= dark_ratio else dark


def _is_light(color):
    return _luma(color) > 140


def plan_layout(img, plan, attempt):
    """Mede e posiciona todos os grupos. attempt carrega ajustes das tentativas anteriores."""
    r = Renderer(img, plan, attempt)
    analysis = Analysis(img)
    blocks = textplan.resolve_zones(plan, img.width / img.height)
    order = sorted(range(len(blocks)), key=lambda i: textplan.ROLE_ORDER.get(blocks[i]["papel"], 5))
    groups = {}
    for i in order:
        groups.setdefault(attempt.get("zona", {}).get(blocks[i]["posicao"], blocks[i]["posicao"]), []).append(blocks[i])
    rects = _split_shared({z: zone_rect(z, img.size, r.margin) for z in groups}, img.size, r.margin)
    placed = []
    # Ordem de cima para baixo; uma zona que invade um grupo já posicionado desce para logo abaixo dele.
    zone_top = {z: rects[z][1] for z in groups}
    for zone, items_blocks in sorted(groups.items(), key=lambda kv: zone_top[kv[0]]):
        rect = rects[zone]
        gap = img.height * 0.025
        for other in placed:
            ox0, oy0, ox1, oy1 = other["rect"]
            if other["itens"] and rect[0] < ox1 and ox0 < rect[2] and rect[1] < oy1 + gap and oy0 < rect[3]:
                rect = (rect[0], oy1 + gap, rect[2], min(img.height - r.margin, max(rect[3], oy1 + gap + img.height * 0.12)))
        scale = attempt.get("escala", {}).get(zone, 1.0)
        region = analysis.region(rect)
        color = r.text_color(region)
        ref_h = min(img.height, img.width * 1.25)  # formatos altos: o título não cresce além do de um 4:5
        items, total = layout_group(items_blocks, r.theme, rect, ref_h, scale, r.min_px, color)
        if items is None:
            placed.append({"zona": zone, "itens": None, "rect": rect})
            continue
        x0, y0, x1, y1 = rect
        free_h = (y1 - y0) - total
        # Desliza dentro da zona para a faixa mais "calma" da arte (menos bordas = menos elementos importantes).
        anchor = {"rodape": 1.0, "base_direita": 1.0, "base_esquerda": 1.0, "centro": 0.5, "esquerda": 0.5,
                  "direita": 0.5}.get(zone, 0.0)
        best_y, best_score = y0 + free_h * anchor, None
        if free_h > 4:
            for k in range(11):
                cand = y0 + free_h * k / 10
                busy = analysis.region((x0, cand, x1, cand + total))
                score = busy["bordas"] * 3 + busy["desvio"] + abs(k / 10 - anchor) * 0.12
                if best_score is None or score < best_score:
                    best_y, best_score = cand, score
        align = ALIGN.get(zone, "center")
        cursor, prev, boxes = best_y, None, []
        for item in items:
            cursor += _gap(prev, item)
            if align == "left":
                x = x0
            elif align == "right":
                x = x1 - item.width
            else:
                x = x0 + ((x1 - x0) - item.width) / 2
            boxes.append((item, x, cursor))
            cursor += item.height
            prev = item
        width = max(i.width for i in items)
        gx0 = x0 if align == "left" else (x1 - width if align == "right" else x0 + ((x1 - x0) - width) / 2)
        group_rect = (gx0, best_y, gx0 + width, best_y + total)
        bg = analysis.region(group_rect)
        level = attempt.get("tratamento", {}).get(zone)
        if level is None:
            level = _auto_treatment(color, bg, r.effect)
        placed.append({"zona": zone, "itens": boxes, "rect": group_rect, "zona_rect": rect, "cor": color,
                       "tratamento": level, "alinhamento": align, "fundo": bg})
    return r, placed


def _auto_treatment(color, bg, effect):
    worst = bg["p90"] if _is_light(color) else bg["p10"]
    ratio = contrast_ratio(_luma(color), worst * 255)
    busy = bg["bordas"] > 0.09 or bg["desvio"] > 0.2
    if ratio >= 4.5 and not busy:
        return 0 if effect == "nenhum" else 1
    if ratio >= 3.0 and bg["bordas"] < 0.14:
        return 2
    return 3


def _scrim(canvas, group, color_light):
    """Véu suave atrás do grupo (degradê a partir da borda mais próxima) ou painel arredondado."""
    w, h = canvas.size
    x0, y0, x1, y1 = group["rect"]
    shade = (0, 0, 0) if color_light else (255, 255, 255)
    zone = group["zona"]
    level = group["tratamento"]
    pad = min(w, h) * 0.05
    layer = Image.new("L", canvas.size, 0)
    draw = ImageDraw.Draw(layer)
    if level >= 5:
        radius = min(w, h) * 0.025
        draw.rounded_rectangle((x0 - pad * 0.6, y0 - pad * 0.5, x1 + pad * 0.6, y1 + pad * 0.5), radius, fill=int(255 * 0.72))
        layer = layer.filter(ImageFilter.GaussianBlur(2))
    elif zone in ("topo", "rodape", "esquerda", "direita"):
        alpha = 0.62 if level == 3 else 0.84
        if zone == "topo":
            grad = _gradient((w, y1 + pad * 2), [(255,) * 3, (int(255 * 0.75),) * 3, (0,) * 3]).convert("L")
            layer.paste(grad.point(lambda v: int(v * alpha)), (0, 0))
        elif zone == "rodape":
            top = y0 - pad * 2
            grad = _gradient((w, h - top), [(0,) * 3, (int(255 * 0.75),) * 3, (255,) * 3]).convert("L")
            layer.paste(grad.point(lambda v: int(v * alpha)), (0, int(top)))
        elif zone == "esquerda":
            grad = _gradient((x1 + pad * 2, h), [(255,) * 3, (int(255 * 0.7),) * 3, (0,) * 3], vertical=False).convert("L")
            layer.paste(grad.point(lambda v: int(v * alpha)), (0, 0))
        else:
            left = x0 - pad * 2
            grad = _gradient((w - left, h), [(0,) * 3, (int(255 * 0.7),) * 3, (255,) * 3], vertical=False).convert("L")
            layer.paste(grad.point(lambda v: int(v * alpha)), (int(left), 0))
    else:
        # Centro e cantos: sombra difusa elíptica, sem bordas visíveis.
        draw.ellipse((x0 - pad * 2.2, y0 - pad * 1.6, x1 + pad * 2.2, y1 + pad * 1.6),
                     fill=int(255 * (0.62 if level == 3 else 0.84)))
        layer = layer.filter(ImageFilter.GaussianBlur(pad * 1.6))
    canvas.paste(Image.new("RGB", canvas.size, shade), (0, 0), layer)


def _line_masks(group, size):
    """Máscaras por linha: [(item, linha, mask, bbox)]."""
    out = []
    for item, x, y in group["itens"]:
        if item.kind in ("preco", "cta"):  # selos: desenhados e medidos à parte
            continue
        line_h = item._line_height()
        for n, line in enumerate(item.lines):
            mask = Image.new("L", size, 0)
            lw = text_width(item.font, line, item.tracking)
            if group["alinhamento"] == "left":
                lx = x
            elif group["alinhamento"] == "right":
                lx = x + item.width - lw
            else:
                lx = x + (item.width - lw) / 2
            _draw_text_mask(mask, (lx, y + n * line_h), line, item.font, item.tracking)
            out.append((item, line, mask, mask.getbbox()))
    return out


def _fill_for(r, item, group, mask_bbox):
    """Imagem de preenchimento do texto (cor sólida, degradê ou metal)."""
    w, h = r.size
    color = group["cor"]
    is_title = ROLE_FONT.get(item.role) == "title"
    if r.metal and is_title and mask_bbox:
        x0, y0, x1, y1 = mask_bbox
        fill = Image.new("RGB", (w, h), color)
        fill.paste(_gradient((x1 - x0, y1 - y0), r.metal), (x0, y0))
        return fill
    if r.effect == "gradiente" and is_title and mask_bbox:
        x0, y0, x1, y1 = mask_bbox
        accent = r.theme["accent"]
        fill = Image.new("RGB", (w, h), color)
        fill.paste(_gradient((x1 - x0, y1 - y0), [color, accent]), (x0, y0))
        return fill
    if item.role == "subtitulo" and r.theme.get("accent") and r.plan.get("estilo") in ("elegante", "luxo", "manuscrito") \
            and not r.user_color:
        return Image.new("RGB", (w, h), r.theme["accent"] if _is_light(color) else color)
    return Image.new("RGB", (w, h), color)


def render_groups(r, placed):
    """Desenha véus, sombras/brilhos e os textos. Devolve (imagem, fundo_sem_texto, linhas)."""
    canvas = r.img.copy()
    for group in placed:
        if group["itens"] and group["tratamento"] >= 3:
            _scrim(canvas, group, _is_light(group["cor"]))
    lines = []
    for group in placed:
        if group["itens"]:
            lines.extend((group, *entry) for entry in _line_masks(group, r.size))
    # Sombras e halos (escuros) fazem parte do fundo; o contraste é medido contra eles.
    for group, item, _, mask, bbox in lines:
        if not bbox:
            continue
        light = _is_light(group["cor"])
        shade = (0, 0, 0) if light else (255, 255, 255)
        level = group["tratamento"]
        if level >= 1:
            off = max(1, item.size * 0.035)
            shadow = ImageChops.offset(mask, int(off), int(off)).filter(ImageFilter.GaussianBlur(max(1, item.size * 0.05)))
            canvas.paste(Image.new("RGB", r.size, shade), (0, 0), shadow.point(lambda v: int(v * 0.55)))
        if level >= 2:
            halo = mask.filter(ImageFilter.GaussianBlur(max(2, item.size * 0.16)))
            canvas.paste(Image.new("RGB", r.size, shade), (0, 0), halo.point(lambda v: min(255, int(v * 1.6))))
    background = canvas.copy()
    for group, item, _, mask, bbox in lines:
        if not bbox:
            continue
        if r.effect == "brilho" and ROLE_FONT.get(item.role) in ("title", "price", "cta"):
            glow_color = r.theme.get("glow", r.theme["accent"])
            for radius, strength in ((item.size * 0.22, 1.0), (item.size * 0.07, 1.2)):
                glow = mask.filter(ImageFilter.GaussianBlur(max(1, radius)))
                canvas.paste(Image.new("RGB", r.size, glow_color), (0, 0), glow.point(lambda v, s=strength: min(255, int(v * s))))
        if r.effect == "contorno" or (r.metal and ROLE_FONT.get(item.role) == "title"):
            width = max(1, int(item.size * (0.05 if r.effect == "contorno" else 0.018)))
            stroke = mask.filter(ImageFilter.MaxFilter(width * 2 + 1)) if width <= 6 else \
                mask.filter(ImageFilter.GaussianBlur(width)).point(lambda v: 255 if v > 20 else 0)
            dark = (20, 20, 24) if _is_light(group["cor"]) else (255, 255, 255)
            stroke_color = (90, 64, 18) if r.metal is GOLD else ((70, 72, 78) if r.metal else dark)
            canvas.paste(Image.new("RGB", r.size, stroke_color), (0, 0), stroke)
        canvas.paste(_fill_for(r, item, group, bbox), (0, 0), mask)
    # Preços e botões (desenhados como selos, com o texto exato).
    for group in placed:
        for item, x, y in group["itens"] or []:
            if item.kind == "preco":
                _draw_price_badge(canvas, r, item, x, y)
            elif item.kind == "cta":
                _draw_cta_pill(canvas, r, item, x, y, group, background)
    # Preço antigo riscado.
    for group, item, _, mask, bbox in lines:
        if item.role == "preco_de" and bbox:
            draw = ImageDraw.Draw(canvas)
            mid = (bbox[1] + bbox[3]) / 2
            draw.line([(bbox[0] - 2, mid), (bbox[2] + 2, mid)], fill=group["cor"], width=max(2, int(item.size * 0.08)))
    return canvas, background, lines


def _draw_price_badge(canvas, r, item, x, y):
    bg = r.theme.get("price_bg") or r.theme["accent"]
    fg = (255, 255, 255) if _luma(bg) < 150 else (20, 20, 24)
    w, h = item.width, item.height
    shadow = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle((x + 3, y + 6, x + w + 3, y + h + 6), h * 0.28, fill=110)
    canvas.paste(Image.new("RGB", canvas.size, (0, 0, 0)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(h * 0.12)))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((x, y, x + w, y + h), h * 0.28, fill=bg)
    big = item.parts[1][1]
    baseline = y + item.pad_y + item.size * 0.82
    cursor = x + item.pad_x
    cap = big.getbbox("0")
    for text, font, where in item.parts:
        if not text:
            continue
        if where == "topo":
            top = baseline - (cap[3] - cap[1])
            sb = font.getbbox("0")
            draw.text((cursor, top - sb[1] + 1), text, font=font, fill=fg)
        else:
            draw.text((cursor, baseline), text, font=font, fill=fg, anchor="ls")
        cursor += font.getlength(text)
    item.badge_colors = (fg, bg)


def _draw_cta_pill(canvas, r, item, x, y, group, background):
    accent = r.theme["accent"]
    fg = (20, 20, 24) if _luma(accent) > 150 else (255, 255, 255)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((x, y, x + item.width, y + item.height), item.height / 2, fill=accent)
    mask = Image.new("L", canvas.size, 0)
    tw = text_width(item.font, item.text, item.tracking)
    _draw_text_mask(mask, (x + (item.width - tw) / 2, y + item.pad_y), item.text, item.font, item.tracking)
    canvas.paste(Image.new("RGB", canvas.size, fg), (0, 0), mask)
    item.badge_colors = (fg, accent)


# ----------------------------------------------------------------------------------- validação

def _norm(text):
    return " ".join(str(text).split())


def validate(r, placed, canvas, background, lines, plan):
    checks, problems = [], {}
    expected = [b["texto"] for b in plan["blocos"]]
    drawn = []
    for group in placed:
        for item, _, _ in group["itens"] or []:
            if item.kind == "preco":
                drawn.append("".join(t for t, _, _ in item.parts).replace("R$ ", "R$ ").strip())
            else:
                drawn.append(" ".join(item.lines))
    missing_groups = [g["zona"] for g in placed if not g["itens"]]
    for zone in missing_groups:
        problems.setdefault(zone, set()).add("nao_coube")
    # 1) todos os textos, exatamente como pedidos.
    exact = sorted(map(_norm, drawn)) == sorted(map(_norm, expected))
    checks.append({"nome": "textos_completos_e_identicos", "ok": exact and not missing_groups,
                   "esperado": expected, "desenhado": drawn})
    # 2) caracteres existentes na fonte.
    glyph_problems = []
    for group in placed:
        for item, _, _ in group["itens"] or []:
            miss = missing_glyphs(item.family, item.weight, item.text)
            if miss:
                glyph_problems.append({"texto": item.text, "faltando": "".join(miss)})
    checks.append({"nome": "caracteres", "ok": not glyph_problems, "detalhe": glyph_problems})
    # 3) dentro da área visível (com folga) e 4) sem sobreposição.
    w, h = r.size
    safe = r.margin * 0.4
    outside = []
    for group, item, line, mask, bbox in lines:
        if not bbox or bbox[0] < safe or bbox[1] < safe or bbox[2] > w - safe or bbox[3] > h - safe:
            outside.append(line)
            problems.setdefault(group["zona"], set()).add("fora")
    for group in placed:
        for item, x, y in group["itens"] or []:
            if item.kind in ("preco", "cta") and (x < safe or y < safe or x + item.width > w - safe or y + item.height > h - safe):
                outside.append(item.text)
                problems.setdefault(group["zona"], set()).add("fora")
    checks.append({"nome": "dentro_da_area", "ok": not outside, "detalhe": outside})
    overlaps = []
    rects = [(g["zona"], g["rect"]) for g in placed if g["itens"]]
    gap = min(w, h) * 0.01
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i][1], rects[j][1]
            if a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap:
                overlaps.append([rects[i][0], rects[j][0]])
                problems.setdefault(rects[j][0], set()).add("sobreposicao")
    checks.append({"nome": "sem_sobreposicao", "ok": not overlaps, "detalhe": overlaps})
    # 5) contraste medido nos pixels reais.
    worst = []
    lum_bg, lum_fg = background.convert("L"), canvas.convert("L")
    for group, item, line, mask, bbox in lines:
        if not bbox:
            continue
        near = mask.filter(ImageFilter.GaussianBlur(max(2, item.size * 0.1))).point(lambda v: 255 if v > 8 else 0)
        box = tuple(max(0, int(v)) for v in (bbox[0] - item.size * 0.3, bbox[1] - item.size * 0.3,
                                               bbox[2] + item.size * 0.3, bbox[3] + item.size * 0.3))
        bg_hist = lum_bg.crop(box).histogram(near.crop(box))
        fg_hist = lum_fg.crop(box).histogram(mask.crop(box).point(lambda v: 255 if v > 200 else 0))
        light = _is_light(group["cor"])
        fg = _percentile(fg_hist, 0.2 if light else 0.8)
        bgv = _percentile(bg_hist, 0.85 if light else 0.15)
        ratio = contrast_ratio(fg, bgv)
        large = item.size >= h * 0.045
        need = 3.0 if large else 4.5
        worst.append({"texto": line, "contraste": round(ratio, 2), "minimo": need})
        if ratio < need:
            problems.setdefault(group["zona"], set()).add("contraste")
    for group in placed:
        for item, _, _ in group["itens"] or []:
            if item.kind in ("preco", "cta"):
                fg, bg = item.badge_colors
                ratio = contrast_ratio(_luma(fg), _luma(bg))
                worst.append({"texto": item.text, "contraste": round(ratio, 2), "minimo": 3.0})
    low = [x for x in worst if x["contraste"] < x["minimo"]]
    checks.append({"nome": "contraste", "ok": not low, "detalhe": worst})
    # 6) tamanho legível.
    small = []
    for group in placed:
        for item, _, _ in group["itens"] or []:
            if item.size < r.min_px - 0.5:
                small.append(item.text)
    checks.append({"nome": "legibilidade", "ok": not small, "detalhe": small, "minimo_px": round(r.min_px)})
    return checks, problems


# ----------------------------------------------------------------------------------- entrada

def delivery_size(width, height):
    """Tamanho de entrega: lado menor ≥ 1080 px (padrão de redes sociais), lado maior ≤ 1920 px."""
    scale = max(1.0, 1080 / min(width, height))
    scale = min(scale, 1920 / max(width, height)) if max(width, height) * scale > 1920 else scale
    return int(round(width * scale)), int(round(height * scale))


def _layout_loop(img, plan, max_attempts, attempt=None, fixes=None):
    """Layout + validação interna com correções automáticas. Devolve (canvas, relatorio, attempt, placed)."""
    attempt = attempt or {"escala": {}, "tratamento": {}, "zona": {}}
    fixes = fixes if fixes is not None else []
    canvas, report, placed = img, None, []
    for n in range(max_attempts):
        r, placed = plan_layout(img, plan, attempt)
        canvas, background, lines = render_groups(r, placed)
        checks, problems = validate(r, placed, canvas, background, lines, plan)
        report = {"ok": all(c["ok"] for c in checks), "tentativas": n + 1, "verificacoes": checks, "correcoes": fixes,
                  "tamanho": list(img.size), "estilo": plan.get("estilo"), "efeito": r.effect,
                  "grupos": [{"zona": g["zona"], "tratamento": TREATMENTS[min(5, g.get("tratamento", 0))],
                              "fontes": [f"{i.family}:{i.weight}:{int(i.size)}px" for i, _, _ in g["itens"] or []]}
                             for g in placed],
                  "tamanhos": {i.text: round(i.size) for g in placed for i, _, _ in g["itens"] or []}}
        if report["ok"] or not problems:
            break
        for zone, kinds in problems.items():
            group = next((g for g in placed if g["zona"] == zone), None)
            if "nao_coube" in kinds or "fora" in kinds:
                attempt["escala"][zone] = attempt["escala"].get(zone, 1.0) * 0.85
                fixes.append(f"{zone}: texto reduzido para caber")
            if "sobreposicao" in kinds:
                attempt["escala"][zone] = attempt["escala"].get(zone, 1.0) * 0.85
                fixes.append(f"{zone}: reduzido para não sobrepor")
            if "contraste" in kinds and group:
                level = min(5, max(group.get("tratamento", 0), 1) + 1)
                attempt["tratamento"][zone] = level
                fixes.append(f"{zone}: reforço de contraste ({TREATMENTS[level]})")
    return canvas, report, attempt, placed


def _apply_surface(img, plan):
    """Texto principal na superfície pedida (parede, placa, camiseta…). Devolve (img, plano_restante, info)."""
    from . import surfaces  # noqa: PLC0415 — numpy só é carregado quando há superfície

    target = next((b for b in plan["blocos"] if b["papel"] in ("titulo", "marca")), plan["blocos"][0])
    theme = THEMES.get(plan.get("estilo"), THEMES["moderno"])
    family, weight = choose_font(theme, "titulo", target["texto"])
    result, info = surfaces.apply(img, plan["superficie"], target["texto"], lambda size: get_font(family, weight, size),
                                  plan.get("cor"))
    if result is None:
        # Sem confiança na superfície: texto em destaque no topo, sem cobrir o objeto principal
        # (nunca uma perspectiva inventada).
        blocks = [dict(b, posicao="topo") if b is target and b["posicao"] in ("auto", "centro") else b for b in plan["blocos"]]
        return img, {**plan, "blocos": blocks}, {"modo": "destaque", "motivo": info, "texto": target["texto"]}
    info = {**info, "texto": target["texto"], "fonte": f"{family}:{weight}",
            "identico": " ".join(info["linhas"]) == " ".join(target["texto"].split()),
            "caracteres_ok": not missing_glyphs(family, weight, target["texto"])}
    rest = {**plan, "blocos": [b for b in plan["blocos"] if b is not target]}
    return result, rest, info


def compose(art_bytes, plan, max_attempts=6, use_ocr=True):
    """Aplica o plano sobre a arte. Devolve (jpeg, relatorio)."""
    art = ImageOps.exif_transpose(Image.open(io.BytesIO(art_bytes))).convert("RGB")
    target = delivery_size(*art.size)
    img = art.resize(target, Image.LANCZOS) if target != art.size else art
    surface = None
    layout_plan = plan
    if plan.get("superficie") and plan.get("blocos"):
        img, layout_plan, surface = _apply_surface(img, plan)
    if layout_plan["blocos"]:
        canvas, report, attempt, placed = _layout_loop(img, layout_plan, max_attempts)
    else:
        canvas, attempt, placed = img, None, []
        report = {"ok": True, "tentativas": 1, "verificacoes": [], "correcoes": [], "tamanho": list(img.size),
                  "estilo": plan.get("estilo"), "grupos": []}
    if surface:
        report["superficie"] = surface
        if surface["modo"] != "destaque":
            ok = surface["identico"] and surface["caracteres_ok"]
            report["verificacoes"].append({"nome": "texto_na_superficie", "ok": ok, "detalhe": surface.get("linhas")})
            report["ok"] = report["ok"] and ok
    # Segunda camada: leitura visual (OCR local). Um reforço automático de contraste se algo não foi lido.
    if use_ocr:
        from . import ocr  # noqa: PLC0415

        expected = [b["texto"] for b in plan["blocos"]]
        result = ocr.validate(canvas, expected)
        if result is not None and attempt is not None and any(t["status"] != "ok" for t in result["textos"]):
            layout_texts = {b["texto"] for b in layout_plan["blocos"]}
            if any(t["status"] != "ok" and t["texto"] in layout_texts for t in result["textos"]):
                for group in placed:
                    if group.get("itens"):
                        attempt["tratamento"][group["zona"]] = min(5, max(group.get("tratamento", 0), 1) + 1)
                report["correcoes"].append("leitura visual: reforço de contraste")
                canvas, report2, attempt, placed = _layout_loop(img, layout_plan, 2, attempt, report["correcoes"])
                report2["superficie"] = report.get("superficie")
                report = report2
                again = ocr.validate(canvas, expected)
                result = again or result
        report["ocr"] = result if result is not None else {"disponivel": False}
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=92, optimize=True)
    return out.getvalue(), report
