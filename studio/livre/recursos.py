"""Recursos visuais gratuitos: fontes (Google Fonts, OFL/Apache) e ícones (Lucide, ISC). Origem e licença em
LICENCAS.md. Cada fonte tem uma personalidade, que o diretor criativo usa para escolher pelo tema."""
import base64
import functools
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO_FONTS = os.path.join(os.path.dirname(HERE), "fonts")
EXTRA_FONTS = os.path.join(HERE, "fontes")
ICON_DIR = os.path.join(HERE, "icones")

# chave: (arquivo, família CSS, personalidade, caixa-alta)
FONTS = {
    "anton": ("Anton-Regular.ttf", "Anton", "condensada pesada, varejo clássico", True),
    "bebas": ("BebasNeue-Regular.ttf", "Bebas Neue", "condensada limpa, moderna", True),
    "archivo": ("ArchivoBlack-Regular.ttf", "Archivo Black", "grotesca muito pesada", False),
    "montserrat": ("Montserrat-VF.ttf", "Montserrat", "geométrica neutra, corporativa", False),
    "playfair": ("PlayfairDisplay-VF.ttf", "Playfair Display", "serifa elegante, editorial", False),
    "orbitron": ("Orbitron-VF.ttf", "Orbitron", "futurista, tecnologia", True),
    "cinzel": ("Cinzel-VF.ttf", "Cinzel", "romana clássica, luxo, sofisticado", True),
    "dmserif": ("DMSerifDisplay-Regular.ttf", "DM Serif Display", "serifa de revista, premium, imóveis", False),
    "greatvibes": ("GreatVibes-Regular.ttf", "Great Vibes", "caligrafia fina, convite, romântico", False),
    "poppins": ("Poppins-ExtraBold.ttf", "PoppinsX", "sem serifa forte e amigável, saúde, serviços", False),
    "bangers": ("Bangers-Regular.ttf", "Bangers", "quadrinhos, explosiva, promoção relâmpago", True),
    "blackops": ("BlackOpsOne-Regular.ttf", "Black Ops One", "estêncil, agressiva, Black Friday", True),
    "bungee": ("Bungee-Regular.ttf", "Bungee", "letreiro urbano em bloco, impacto", True),
    "lobster": ("Lobster-Regular.ttf", "Lobster", "script retrô gorda, simpática", False),
    "pacifico": ("Pacifico-Regular.ttf", "Pacifico", "pincel casual, verão, leve", False),
    "righteous": ("Righteous-Regular.ttf", "Righteous", "retrô arredondada", False),
    "monoton": ("Monoton-Regular.ttf", "Monoton", "tubos de neon (só títulos curtos)", True),
    "abril": ("AbrilFatface-Regular.ttf", "Abril Fatface", "didone dramática, moda", False),
    "marker": ("PermanentMarker-Regular.ttf", "Permanent Marker", "pincel atômico, escrita à mão, feira", False),
    "russo": ("RussoOne-Regular.ttf", "Russo One", "esportiva robusta, tecnologia", False),
    "shrikhand": ("Shrikhand-Regular.ttf", "Shrikhand", "itálica gorda festiva, doce", False),
    "titan": ("TitanOne-Regular.ttf", "Titan One", "arredondada gorda, infantil, divertida", False),
    "kanit": ("Kanit-BlackItalic.ttf", "Kanit", "itálica pesada veloz, urgência", True),
    "satisfy": ("Satisfy-Regular.ttf", "Satisfy", "script elegante leve, assinatura", False),
    "berkshire": ("BerkshireSwash-Regular.ttf", "Berkshire Swash", "script vintage com floreio, Natal tradicional", False),
    "luckiest": ("LuckiestGuy-Regular.ttf", "Luckiest Guy", "desenho animado, chamativa", True),
}
# fontes boas para números grandes (preço) e para textos pequenos (nomes, informações)
PRICE_FONTS = ("anton", "bebas", "bungee", "russo", "titan", "luckiest", "kanit", "archivo", "blackops", "bangers", "poppins")
BODY_FONTS = ("poppins", "montserrat", "archivo", "russo", "dmserif", "playfair")
# tom da campanha (decidido pelo diretor a partir do briefing) -> fontes coerentes. Uma fonte serve a vários tons;
# nenhum segmento tem fonte fixa: o tom vem da interpretação do pedido e a variedade vem da rotação entre as coerentes.
TONES = {
    "impacto": ("anton", "bebas", "archivo", "bungee", "blackops", "bangers", "kanit", "luckiest", "russo", "titan"),
    "urgente": ("kanit", "bangers", "blackops", "anton", "archivo", "bungee"),
    "sofisticado": ("cinzel", "playfair", "dmserif", "abril", "montserrat"),
    "elegante": ("playfair", "dmserif", "cinzel", "abril", "greatvibes", "satisfy"),
    "moderno": ("montserrat", "bebas", "poppins", "righteous", "russo", "archivo"),
    "tecnologico": ("orbitron", "russo", "bebas", "monoton", "archivo", "kanit"),
    "divertido": ("titan", "luckiest", "shrikhand", "lobster", "bangers", "righteous", "pacifico"),
    "acolhedor": ("poppins", "lobster", "pacifico", "righteous", "satisfy", "montserrat", "dmserif"),
    "natural": ("pacifico", "marker", "satisfy", "dmserif", "montserrat", "righteous"),
    "festivo": ("shrikhand", "berkshire", "lobster", "luckiest", "titan", "greatvibes"),
    "artesanal": ("marker", "pacifico", "lobster", "berkshire", "satisfy"),
}
EFFECTS = ("neon", "metal", "3d", "adesivo", "gradiente", "contorno", "faixa", "arco", "italico", "sombra")


def font_path(key):
    f = FONTS[key][0]
    for d in (EXTRA_FONTS, STUDIO_FONTS):
        p = os.path.join(d, f)
        if os.path.exists(p):
            return p
    return None


@functools.lru_cache(maxsize=1)
def _all_icons():
    out = {}
    if os.path.isdir(ICON_DIR):
        for f in os.listdir(ICON_DIR):
            if f.endswith(".svg"):
                out[f[:-4]] = open(os.path.join(ICON_DIR, f), encoding="utf-8").read()
    return out


ICONS = _all_icons()


def font_css(keys):
    """@font-face só das fontes usadas (a peça fica leve)."""
    out = []
    for k in sorted(set(keys)):
        if str(k).startswith("g:"):  # família do catálogo do Google Fonts (baixada sob demanda)
            from . import fontes_google  # noqa: PLC0415
            face = fontes_google.font_face(k[2:])
            if face:
                out.append(face)
            continue
        if k not in FONTS:
            continue
        p = font_path(k)
        if not p:
            continue
        w = "font-weight:100 900;" if p.endswith("-VF.ttf") else ""
        data = base64.b64encode(open(p, "rb").read()).decode()
        out.append(f"@font-face{{font-family:'{FONTS[k][1]}';src:url(data:font/ttf;base64,{data});{w}font-display:block}}")
    return "\n".join(out)


def font_menu(seed=0):
    """Menu de fontes em ordem embaralhada por briefing (o modelo tende a escolher as primeiras da lista)."""
    import random  # noqa: PLC0415
    keys = list(FONTS)
    random.Random(seed).shuffle(keys)
    return "; ".join(f"{k} ({FONTS[k][2]})" for k in keys)


def icon_svg(name, color):
    svg = ICONS.get(name)
    if not svg:
        return ""
    return (svg.replace('stroke="currentColor"', f'stroke="{color}"').replace('width="24"', 'width="100%"')
            .replace('height="24"', 'height="100%"'))
