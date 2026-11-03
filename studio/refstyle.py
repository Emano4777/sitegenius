"""Análise visual de uma arte de referência -> especificação estruturada de layout (sem copiar conteúdo comercial).

Fluxo: referência -> análise (aqui) -> adaptação aos produtos do briefing (composer) -> composição final.
O que é extraído (coordenadas normalizadas 0..1), com sinais robustos em vez de "adivinhar" cada produto:
  - cabeçalho: altura, cores (principal/secundária), selo tipo "explosão" e onde fica o logotipo (com as cores dele);
  - tarja de título abaixo do cabeçalho: altura e cores;
  - conteúdo: cor do fundo, moldura (cor/espessura), faixas de produtos (linhas) e produtos por linha;
  - preço: cor dos números, tamanho em relação à faixa, centavos sobrescritos, posição em relação à foto,
    fração com selo (cor e forma do selo); fotos recortadas (sem cartão) ou em cartões.
Nada de texto, preço, nome, foto ou marca da referência é guardado: a referência orienta a organização visual; os
produtos, preços, fotos e a loja vêm do briefing do cliente.

Roda onde há OCR + OpenCV (ferramentas da HostGator); o servidor só recebe a especificação (JSON pequeno).
"""
import io
import json
import re

from PIL import Image, ImageOps

VERSION = 2


def _rgb(c):
    return [int(round(float(v))) for v in list(c)[:3]]


def _dist(a, b):
    return sum(abs(int(x) - int(y)) for x, y in zip(a, b))


def _sat(c):
    return max(c) - min(c)


def _median(arr):
    import numpy as np  # noqa: PLC0415

    flat = arr.reshape(-1, 3)
    return _rgb(np.median(flat, axis=0)) if len(flat) else [255, 255, 255]


def _kcolors(arr, k=3):
    """Cores principais (k-means) com a fração de cada uma, da mais frequente para a menos."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    data = arr.reshape(-1, 3).astype(np.float32)
    if len(data) > 20000:
        data = data[np.random.RandomState(1).choice(len(data), 20000, replace=False)]
    k = max(1, min(k, len(np.unique(data.astype(int), axis=0))))
    _, labels, centers = cv2.kmeans(data, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0), 3,
                                    cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k)
    order = np.argsort(-counts)
    return [(_rgb(centers[i]), round(float(counts[i] / counts.sum()), 3)) for i in order]


def _ink_color(arr, bg):
    """Cor da "tinta" (texto) numa região: pixels mais distantes do fundo."""
    import numpy as np  # noqa: PLC0415

    reg = arr.reshape(-1, 3).astype(int)
    if not len(reg):
        return [0, 0, 0]
    d = np.abs(reg - np.array(bg)).sum(axis=1)
    return _rgb(np.median(reg[d >= np.percentile(d, 85)], axis=0))


def _row_profile(arr):
    import numpy as np  # noqa: PLC0415

    a = arr.astype(int)
    med = np.median(a, axis=1)
    spread = np.median(np.abs(a - med[:, None, :]).sum(axis=2), axis=1)
    return med, spread


def _superscript_cents(arr, box, bg):
    """Centavos menores e no alto ("4⁴⁷"): a tinta do terço final é bem mais baixa que a do início."""
    import numpy as np  # noqa: PLC0415

    x0, y0, x1, y1 = (int(v) for v in box)
    reg = arr[y0:y1, x0:x1].astype(int)
    if reg.shape[0] < 8 or reg.shape[1] < 12:
        return False
    ink = np.abs(reg - np.array(bg)).sum(axis=2) > 120
    cols = ink.shape[1]

    def span(part):
        rows = np.where(part.any(axis=1))[0]
        return (rows[0], rows[-1]) if len(rows) else (0, 0)

    a0, a1 = span(ink[:, : int(cols * 0.45)])
    b0, b1 = span(ink[:, int(cols * 0.7):])
    ha, hb = a1 - a0, b1 - b0
    return bool(ha > 0 and 0 < hb < ha * 0.72 and b0 <= a0 + ha * 0.25)


def _badge(arr, box, around, content_bg):
    """Selo atrás do preço: (cor, forma) ou (None, "texto") quando o preço está solto no fundo."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    if _dist(around, content_bg) < 90:
        return None, "texto"
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = (int(v) for v in box)
    bw, bh = x1 - x0, y1 - y0
    X0, Y0, X1, Y1 = max(0, x0 - bw), max(0, y0 - bh), min(w, x1 + bw), min(h, y1 + bh)
    reg = arr[Y0:Y1, X0:X1].astype(int)
    mask = (np.abs(reg - np.array(around)).sum(axis=2) < 70).astype(np.uint8)
    mask[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = 1
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask)
    k = lab[min((y0 + y1) // 2 - Y0, lab.shape[0] - 1), min((x0 + x1) // 2 - X0, lab.shape[1] - 1)]
    if k == 0:
        return None, "texto"
    gx, gy, gw, gh, ga = st[k]
    if gw * gh > (X1 - X0) * (Y1 - Y0) * 0.95:
        return None, "texto"
    fill = ga / max(1, gw * gh)
    return around, ("retangulo" if fill > 0.9 else "arredondado" if fill > 0.8 else "circulo" if fill > 0.7 else "explosao")


def analyze(image_bytes):
    """Referência (bytes) -> (especificação, relatório). Especificação None se não parece uma arte de ofertas."""
    import numpy as np  # noqa: PLC0415

    from . import ocr  # noqa: PLC0415

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    W0, H0 = img.size
    if max(img.size) < 1200:  # referência pequena (print, WhatsApp): amplia para a leitura achar os números
        s = 1200 / max(img.size)
        img = img.resize((int(W0 * s), int(H0 * s)), Image.LANCZOS)
    W, H = img.size
    arr = np.asarray(img)
    med, spread = _row_profile(arr)

    # Fundo do conteúdo: cor de linha "limpa" (pouca variação) mais comum na metade de baixo.
    lower = [i for i in range(int(H * 0.35), H) if spread[i] < 12]
    content_bg = _median(med[lower][:, None, :]) if lower else _median(arr[int(H * 0.5):])

    # Fim do cabeçalho: primeira linha "limpa" com a cor do conteúdo.
    first_content = next((k for k in range(int(H * 0.04), H) if spread[k] < 12 and _dist(med[k], content_bg) < 30), 0)
    # Tarja: logo acima do conteúdo, linhas de cor saturada constante (o texto da tarja não muda a cor dominante).
    strip = None
    k = first_content - 1
    steps = 0
    while k > 0 and steps < 8 and _sat(med[k]) < 90:  # borda suavizada entre tarja e conteúdo
        k -= 1
        steps += 1
    end = k + 1
    while k > 0 and _sat(med[k]) > 90 and _dist(med[k], content_bg) > 140:
        k -= 1
    if H * 0.015 <= end - (k + 1) <= H * 0.12 and k > H * 0.05:
        a = k + 1
        clean = [r for r in range(a, end) if spread[r] < 40] or list(range(a, end))
        color = _median(med[clean][:, None, :])
        strip = (a, end, color)
    header_end = strip[0] if strip else first_content
    header = None
    if header_end > H * 0.06:
        hreg = arr[:header_end]
        colors = _kcolors(hreg[::3, ::3], 4)
        sat_colors = [c for c, f in colors if _sat(c) > 90 and f > 0.06]
        header = {"altura": round(header_end / H, 4), "cores": [c for c, _ in colors],
                  "principal": sat_colors[0] if sat_colors else colors[0][0]}
        # "Explosão"/selo amarelo-laranja compacto no cabeçalho.
        warm = [c for c, f in colors if c[0] > 180 and c[1] > 140 and c[2] < 120 and f > 0.04]
        header["selo"] = warm[0] if warm else None

    # Moldura: bordas laterais/inferior do conteúdo com outra cor.
    top_c = (strip[1] if strip else header_end) + 2
    t = max(2, int(W * 0.012))
    sides = [_median(arr[top_c:, :t]), _median(arr[top_c:, -t:]), _median(arr[-max(2, int(H * 0.01)):, :])]
    agree = [c for c in sides if sum(1 for d in sides if _dist(c, d) < 90) >= 2]
    frame_color = agree[0] if agree else content_bg
    frame = None
    viewer = _sat(frame_color) < 25 and sum(frame_color) < 210  # borda escura de print/visualizador, não do design
    if _dist(frame_color, content_bg) > 80 and not viewer:
        col = arr[int((top_c + H) / 2)].astype(int)
        thick = next((x for x in range(int(W * 0.15)) if _dist(col[x], frame_color) > 90), 0)
        frame = {"cor": frame_color, "espessura": round(max(thick, W * 0.008) / W, 4)}

    # Faixas de produtos: linhas "ocupadas" separadas por linhas de fundo.
    busy = [(spread[k] > 40 or _dist(med[k], content_bg) > 60) for k in range(H)]
    bands, k = [], top_c
    while k < H - int(H * 0.01):
        if busy[k]:
            j = k
            gap = 0
            while j < H and gap < H * 0.012:
                gap = 0 if busy[j] else gap + 1
                j += 1
            if (j - k) > H * 0.05:
                bands.append((k, j - gap))
            k = j
        else:
            k += 1

    # Preços: números grandes lidos (âncoras). A leitura falha em alguns; para a estrutura basta a maioria.
    items = []
    if ocr.engine() is not None:
        items = [it for it in (ocr.read(img) or []) if it["confianca"] >= 0.45]
    num = [it for it in items if len(re.sub(r"\D", "", it["texto"])) >= 1 and it["caixa"][1] > top_c
           and not re.search(r"[a-z]{3,}", it["texto"].lower())]
    hs = sorted((it["caixa"][3] - it["caixa"][1] for it in num), reverse=True)
    ref_h = float(np.median(hs[:8])) if hs else 0
    anchors = [it for it in num if it["caixa"][3] - it["caixa"][1] >= ref_h * 0.6] if ref_h else []
    per_band = []
    for a, b in bands:
        inside = [it for it in anchors if a - 5 <= (it["caixa"][1] + it["caixa"][3]) / 2 <= b + 5]
        per_band.append(len(inside))

    price = {"cor": [20, 20, 20], "centavos_sobrescritos": False, "posicao": "direita", "selo_fracao": 0.0,
             "selo_cor": None, "selo_forma": "explosao", "altura_rel": 0.3}
    if anchors:
        inks, sups, poss, badges = [], [], [], []
        for it in anchors:
            x0, y0, x1, y1 = (int(v) for v in it["caixa"])
            pad = max(3, int((y1 - y0) * 0.25))
            ring = np.concatenate([arr[max(0, y0 - pad):y0, x0:x1].reshape(-1, 3), arr[y1:y1 + pad, x0:x1].reshape(-1, 3),
                                   arr[y0:y1, max(0, x0 - pad):x0].reshape(-1, 3), arr[y0:y1, x1:x1 + pad].reshape(-1, 3)])
            around = _median(ring)
            color, shape = _badge(arr, (x0, y0, x1, y1), around, content_bg)
            badges.append((color, shape))
            bg = around if color else content_bg
            inks.append(_ink_color(arr[y0:y1, x0:x1], bg))
            sups.append(_superscript_cents(arr, (x0, y0, x1, y1), bg))
            # Onde está a foto: centro da "massa" colorida ao lado do preço, na mesma faixa.
            span = int((x1 - x0) * 1.6)
            lx = arr[y0:y1, max(0, x0 - span):x0].astype(int)
            rx = arr[y0:y1, x1:min(W, x1 + span)].astype(int)
            left = float((np.abs(lx - np.array(content_bg)).sum(axis=2) > 90).mean()) if lx.size else 0
            right = float((np.abs(rx - np.array(content_bg)).sum(axis=2) > 90).mean()) if rx.size else 0
            poss.append("direita" if left >= right else "esquerda")
        price["cor"] = _rgb(np.median(np.array([c for c, (b, _) in zip(inks, badges) if not b] or inks), axis=0))
        price["centavos_sobrescritos"] = bool(sum(sups) >= max(1, len(sups) * 0.3))
        price["posicao"] = max(set(poss), key=poss.count)
        with_badge = [b for b in badges if b[0]]
        price["selo_fracao"] = round(len(with_badge) / len(badges), 2)
        if with_badge:
            price["selo_cor"] = _rgb(np.median(np.array([b[0] for b in with_badge]), axis=0))
            shapes = [b[1] for b in with_badge]
            price["selo_forma"] = max(set(shapes), key=shapes.count)
        if bands:
            bh = float(np.median([b - a for a, b in bands]))
            price["altura_rel"] = round(ref_h / max(1.0, bh), 3)

    # Logotipo no cabeçalho: maior texto do cabeçalho (posição e cores), sem guardar o que está escrito.
    logo = None
    if header:
        heads = [it for it in items if it["caixa"][3] <= header_end]
        if heads:
            big = max(heads, key=lambda it: (it["caixa"][3] - it["caixa"][1]) * (it["caixa"][2] - it["caixa"][0]))
            x0, y0, x1, y1 = (int(v) for v in big["caixa"])
            pad = max(3, int((y1 - y0) * 0.3))
            around = _median(np.concatenate([arr[max(0, y0 - pad):y0, x0:x1].reshape(-1, 3),
                                             arr[y1:y1 + pad, x0:x1].reshape(-1, 3)]))
            logo = {"caixa": [round(x0 / W, 4), round(y0 / H, 4), round(x1 / W, 4), round(y1 / H, 4)],
                    "cor_texto": _ink_color(arr[y0:y1, x0:x1], around), "cor_fundo": around}
    strip_spec = None
    if strip:
        a, b, color = strip
        strip_spec = {"inicio": round(a / H, 4), "fim": round(b / H, 4), "cor": color,
                      "cor_texto": _ink_color(arr[a:b], color)}
    total_products = max(len(anchors), sum(per_band))
    spec = {
        "versao": VERSION, "proporcao": round(W0 / H0, 4), "fundo_conteudo": content_bg, "cabecalho": header,
        "logo": logo, "tarja": strip_spec, "moldura": frame,
        "produtos": {"linhas": len(bands), "por_linha": per_band, "total": total_products,
                     "faixas": [[round(a / H, 4), round(b / H, 4)] for a, b in bands],
                     "recortados": True},
        "preco": price,
        "paleta": [c for c, _ in _kcolors(arr[::4, ::4], 6)],
    }
    report = {"ok": bool(bands) or bool(header), "textos": len(items), "precos": len(anchors), "faixas": len(bands)}
    if not bands and not anchors:
        return None, {**report, "ok": False, "motivo": "não encontrei a organização de produtos na referência"}
    return spec, report


def _color(v):
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        try:
            return [max(0, min(255, int(x))) for x in v[:3]]
        except (TypeError, ValueError):
            return None
    return None


def _num(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


VISUAL_KEYS = {
    "organizacao": {"grade", "tabloide", "diagonal", "zigue", "assimetrica", "livre", "destaque"},
    "estilo_geral": {"tabloide_supermercado", "farmacia_clean", "premium", "moderno_minimalista", "outro"},
    "hierarquia": {"todos_iguais", "um_destaque", "tamanhos_variados"},
}


def sanitize(spec):
    """Valida a especificação vinda do worker (só chaves, números e cores conhecidos; nada de texto livre longo)."""
    if not isinstance(spec, dict):
        return None
    out = {"versao": int(_num(spec.get("versao"), 1, 99, VERSION)), "proporcao": _num(spec.get("proporcao"), 0.2, 5, 1),
           "fundo_conteudo": _color(spec.get("fundo_conteudo")) or [255, 255, 255],
           "paleta": [c for c in (_color(x) for x in (spec.get("paleta") or [])[:8]) if c]}
    hd = spec.get("cabecalho")
    if isinstance(hd, dict):
        out["cabecalho"] = {"altura": _num(hd.get("altura"), 0, 0.6, 0.2), "principal": _color(hd.get("principal")),
                            "cores": [c for c in (_color(x) for x in (hd.get("cores") or [])[:6]) if c],
                            "selo": _color(hd.get("selo"))}
    else:
        out["cabecalho"] = None
    lg = spec.get("logo")
    out["logo"] = {"cor_texto": _color(lg.get("cor_texto")), "cor_fundo": _color(lg.get("cor_fundo"))} \
        if isinstance(lg, dict) else None
    tj = spec.get("tarja")
    out["tarja"] = {"inicio": _num(tj.get("inicio"), 0, 1, 0), "fim": _num(tj.get("fim"), 0, 1, 0.05),
                    "cor": _color(tj.get("cor")) or [215, 25, 32], "cor_texto": _color(tj.get("cor_texto"))} \
        if isinstance(tj, dict) else None
    mo = spec.get("moldura")
    out["moldura"] = {"cor": _color(mo.get("cor")) or [255, 204, 0], "espessura": _num(mo.get("espessura"), 0.002, 0.05, 0.01)} \
        if isinstance(mo, dict) and _color(mo.get("cor")) else None
    pr = spec.get("produtos") if isinstance(spec.get("produtos"), dict) else {}
    out["produtos"] = {"linhas": int(_num(pr.get("linhas"), 0, 20, 0)), "total": int(_num(pr.get("total"), 0, 60, 0)),
                       "por_linha": [int(_num(x, 0, 12, 0)) for x in (pr.get("por_linha") or [])[:12]],
                       "recortados": pr.get("recortados") is not False}
    p = spec.get("preco") if isinstance(spec.get("preco"), dict) else {}
    out["preco"] = {"cor": _color(p.get("cor")) or [20, 20, 20], "centavos_sobrescritos": p.get("centavos_sobrescritos") is True,
                    "posicao": p.get("posicao") if p.get("posicao") in ("direita", "esquerda", "abaixo", "sobre") else "direita",
                    "selo_fracao": _num(p.get("selo_fracao"), 0, 1, 0), "selo_cor": _color(p.get("selo_cor")),
                    "selo_forma": p.get("selo_forma") if p.get("selo_forma") in
                    ("explosao", "circulo", "retangulo", "arredondado", "texto") else "explosao",
                    "altura_rel": _num(p.get("altura_rel"), 0.05, 2, 0.4)}
    v = spec.get("visual") if isinstance(spec.get("visual"), dict) else {}
    out["visual"] = {k: v.get(k) for k, allowed in VISUAL_KEYS.items() if v.get(k) in allowed}
    desc = v.get("descricao")
    if isinstance(desc, str):
        out["visual"]["descricao"] = " ".join(desc.split())[:200]
    return out


def describe(spec):
    """Resumo em linguagem simples do que foi entendido da referência (para o cliente conferir)."""
    parts = []
    if spec.get("cabecalho"):
        parts.append("cabeçalho colorido no topo" + (" com selo de destaque" if spec["cabecalho"].get("selo") else ""))
    if spec.get("tarja"):
        parts.append("tarja de título logo abaixo")
    pr = spec.get("produtos") or {}
    if pr.get("total"):
        parts.append(f"produtos {'recortados, sem cartões' if pr.get('recortados') else 'em cartões'} em "
                     f"{pr.get('linhas') or 1} faixa(s)")
    p = spec.get("preco") or {}
    pos = {"direita": "ao lado da foto", "esquerda": "ao lado da foto", "abaixo": "abaixo da foto", "sobre": "sobre a foto"}
    parts.append(("preços em números grandes" + (" com centavos pequenos no alto" if p.get("centavos_sobrescritos") else ""))
                 + " " + pos.get(p.get("posicao"), "") + (" e alguns em selos" if 0 < p.get("selo_fracao", 0) < 0.6 else ""))
    if spec.get("moldura"):
        parts.append("moldura colorida")
    return "; ".join(x.strip() for x in parts if x.strip())


ORDINAL_WORDS = [(0, r"primeir\w*|1\s*[ªa°o]?(?=\s)|imagem 1|referencia 1"), (1, r"segund\w*|2\s*[ªa°o]?(?=\s)|imagem 2|referencia 2"),
                 (2, r"terceir\w*|3\s*[ªa°o]?(?=\s)|imagem 3|referencia 3")]
ROLE_WORDS = {"estrutura": r"layout|organiza\w*|distribui\w*|estrutura|disposi\w*|composi\w*|modelo|jeito",
              "cores": r"cor|cores|paleta|colorid\w*|tons?"}


def parse_roles(text, n):
    """Papel de cada referência no pedido ("layout da primeira e cores da segunda"). Devolve {"estrutura": i, "cores": j}
    com os índices citados; o que não foi dito fica com a primeira imagem."""
    import re as _re
    import unicodedata as _ud

    t = "".join(c for c in _ud.normalize("NFD", (text or "").lower()) if _ud.category(c) != "Mn") + " "
    roles = {"estrutura": 0, "cores": 0}
    for role, rpat in ROLE_WORDS.items():
        # "cores da segunda" (papel antes do número) vale mais que "segunda ... cores".
        direct = [idx for idx, opat in ORDINAL_WORDS if idx < n and
                  _re.search(rf"(?:{rpat})\W+(?:d[aoe]s?|na|no|em)?\W*(?:imagem\W+|referencia\W+)?(?:{opat})", t)]
        loose = [idx for idx, opat in ORDINAL_WORDS if idx < n and
                 _re.search(rf"(?:{opat})\W+(?:\w+\W+){{0,2}}?(?:{rpat})", t)]
        if direct or loose:
            roles[role] = (direct or loose)[0]
    return roles


def merge(specs, roles):
    """Uma especificação combinada: organização de uma referência e cores de outra."""
    specs = [s for s in specs if s]
    if not specs:
        return None
    s_idx = min(roles.get("estrutura", 0), len(specs) - 1)
    c_idx = min(roles.get("cores", 0), len(specs) - 1)
    base = json.loads(json.dumps(specs[s_idx]))
    if c_idx == s_idx:
        return base
    col = specs[c_idx]
    base["fundo_conteudo"] = col.get("fundo_conteudo") or base.get("fundo_conteudo")
    base["paleta"] = col.get("paleta") or base.get("paleta")
    if base.get("cabecalho") and col.get("cabecalho"):
        base["cabecalho"].update({k: col["cabecalho"].get(k) for k in ("cores", "principal", "selo")})
    elif base.get("cabecalho") and col.get("paleta"):
        vivid = [c for c in col["paleta"] if max(c) - min(c) > 80]
        if vivid:
            base["cabecalho"]["principal"] = vivid[0]
    if base.get("tarja"):
        base["tarja"]["cor"] = ((col.get("tarja") or {}).get("cor") or (col.get("cabecalho") or {}).get("principal")
                                or base["tarja"]["cor"])
    if base.get("moldura") and col.get("moldura"):
        base["moldura"]["cor"] = col["moldura"]["cor"]
    for k in ("cor", "selo_cor"):
        if (col.get("preco") or {}).get(k):
            base.setdefault("preco", {})[k] = col["preco"][k]
    base["logo"] = col.get("logo") or base.get("logo")
    return base


def roles_text(roles, n):
    if n < 2:
        return ""
    names = ["1ª", "2ª", "3ª"]
    if roles["estrutura"] == roles["cores"]:
        return (f"Usei a organização e as cores da {names[roles['estrutura']]} imagem. Para combinar, diga por exemplo "
                f"“layout da primeira e cores da segunda”.")
    return f"Usei a organização da {names[roles['estrutura']]} imagem e as cores da {names[roles['cores']]}."
