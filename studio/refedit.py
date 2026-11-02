"""Edição direta de uma imagem enviada pelo cliente (anúncio, cartaz, post): troca de textos sem refazer a arte.

Usado quando o cliente pede para alterar um dado de uma imagem dele ("troca o aluguel para 1000 reais").
Nada da imagem é gerado de novo: fotos, logotipo, contatos e layout continuam sendo os pixels originais.

1. Leitura visual (OCR, studio/ocr.py) encontra os textos e as posições.
2. O texto a trocar é localizado (preço, ou o texto citado pelo cliente).
3. A área é apagada com reconstrução do fundo (OpenCV inpainting; fundo liso = cor de fundo).
4. O texto novo é desenhado no mesmo lugar, com cor e tamanho estimados do original.
5. Validação: o OCR lê o texto novo e o antigo não aparece mais.
6. Entrega no formato do Instagram (4:5, 1080 px), sem cortar nada da arte (extensão desfocada nas bordas).
Precisa de rapidocr-onnxruntime e opencv (ambos no Acer). Sem eles, devolve None e o Studio avisa o cliente.
"""
import io
import re

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import ocr, textplan, typography

PRICE_RE = re.compile(r"(?:R\$|RS|R\s?\$)?\s*\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})?|\d+(?:,\d{2})", re.IGNORECASE)


def _line_groups(items):
    """Junta caixas na mesma linha e próximas ("R$" "1.500" ",00" -> um grupo)."""
    items = sorted(items, key=lambda i: (i["caixa"][1], i["caixa"][0]))
    groups = []
    for it in items:
        x0, y0, x1, y1 = it["caixa"]
        h = y1 - y0
        for g in groups:
            gx0, gy0, gx1, gy1 = g["caixa"]
            same_line = min(y1, gy1) - max(y0, gy0) > 0.45 * min(h, gy1 - gy0)
            near = x0 - gx1 < 1.2 * max(h, gy1 - gy0) and gx0 - x1 < 1.2 * max(h, gy1 - gy0)
            if same_line and near:
                g["itens"].append(it)
                g["caixa"] = [min(gx0, x0), min(gy0, y0), max(gx1, x1), max(gy1, y1)]
                g["texto"] = " ".join(i["texto"] for i in sorted(g["itens"], key=lambda i: i["caixa"][0]))
                break
        else:
            groups.append({"itens": [it], "caixa": list(it["caixa"]), "texto": it["texto"]})
    return groups


def _is_price(text):
    t = text.replace(" ", "")
    return bool(re.search(r"\d", t)) and bool(re.search(r"R\$|RS|reais|\d,\d{2}|\d\.\d{3}", t, re.IGNORECASE))


def find_target(items, change):
    """Grupo de texto a trocar. change = {"tipo": "preco"} ou {"antigo": "texto atual"}."""
    groups = _line_groups([i for i in items if i["confianca"] >= 0.5])
    if change.get("antigo"):
        want = ocr.fold(change["antigo"])
        scored = [(ocr.best_distance(want, ocr.fold(g["texto"])) / max(1, len(want)), g) for g in groups]
        scored = [s for s in scored if s[0] <= 0.25]
        return min(scored, key=lambda s: s[0])[1] if scored else None
    prices = [g for g in groups if _is_price(g["texto"])]
    if not prices:
        return None
    # O preço principal é o maior (mais destacado) da peça.
    return max(prices, key=lambda g: (g["caixa"][3] - g["caixa"][1]) * (g["caixa"][2] - g["caixa"][0]))


def _colors(img, box):
    """(cor do texto, cor do fundo) estimadas dentro da caixa: o fundo é o tom mais comum; o texto, o mais distante."""
    crop = img.crop(box).convert("RGB").resize((64, 24))
    pixels = list(crop.getdata())
    lum = sorted(pixels, key=lambda p: 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2])
    dark, light = lum[: len(lum) // 6], lum[-len(lum) // 6:]

    def avg(ps):
        return tuple(int(sum(p[i] for p in ps) / len(ps)) for i in range(3))

    border = [crop.getpixel((x, y)) for x in range(crop.width) for y in (0, crop.height - 1)]
    bg = avg(border)
    d_dark = sum(abs(a - b) for a, b in zip(avg(dark), bg))
    d_light = sum(abs(a - b) for a, b in zip(avg(light), bg))
    return (avg(dark) if d_dark > d_light else avg(light)), bg


def _dominant(pixels):
    """Cor mais frequente (quantizada) de um conjunto de pixels."""
    import numpy as np  # noqa: PLC0415

    q = (pixels // 8).astype(np.int32)
    keys, counts = np.unique(q[:, 0] * 1_000_000 + q[:, 1] * 1000 + q[:, 2], return_counts=True)
    top = keys[np.argmax(counts)]
    mask = (q[:, 0] * 1_000_000 + q[:, 1] * 1000 + q[:, 2]) == top
    return tuple(int(v) for v in pixels[mask].mean(axis=0))


def _erase(img, box):
    """Apaga o texto antigo. Fundo liso (selo, faixa): em cada linha, cobre do primeiro ao último pixel da cor do
    fundo com essa cor — o texto some e nada vaza para fora do selo. Fundo com textura (foto): inpainting (OpenCV)."""
    import numpy as np  # noqa: PLC0415

    h = box[3] - box[1]
    pad = max(3, int(h * 0.25))
    x0, y0 = max(0, box[0] - pad), max(0, box[1] - pad)
    x1, y1 = min(img.width, box[2] + pad), min(img.height, box[3] + pad)
    arr = np.asarray(img.convert("RGB")).astype(np.int32)
    region = arr[y0:y1, x0:x1]
    ring = np.concatenate([region[:max(1, pad // 2)].reshape(-1, 3), region[:, :max(1, pad // 2)].reshape(-1, 3),
                           region[:, -max(1, pad // 2):].reshape(-1, 3)])
    bg = np.array(_dominant(ring))
    near_bg = np.abs(region - bg).sum(axis=2) < 45
    uniform = near_bg.mean() > 0.35 and np.median(np.abs(ring - bg).sum(axis=1)) < 45
    out = arr.copy()
    if uniform:
        for r in range(region.shape[0]):
            cols = np.nonzero(near_bg[r])[0]
            if cols.size:
                out[y0 + r, x0 + cols.min():x0 + cols.max() + 1] = bg
        return Image.fromarray(out.astype(np.uint8))
    try:
        import cv2  # noqa: PLC0415

        mask = np.zeros(arr.shape[:2], np.uint8)
        mask[max(0, box[1] - 2):box[3] + 2, max(0, box[0] - 2):box[2] + 2] = 255
        res = cv2.inpaint(cv2.cvtColor(arr.astype(np.uint8), cv2.COLOR_RGB2BGR), mask, max(3, h // 6),
                          cv2.INPAINT_TELEA)
        return Image.fromarray(cv2.cvtColor(res, cv2.COLOR_BGR2RGB))
    except ImportError:
        out[box[1]:box[3], box[0]:box[2]] = bg
        return Image.fromarray(out.astype(np.uint8))


def _draw_text(img, box, text, color, align="center"):
    """Texto novo com a mesma altura de letra e na mesma linha de base do antigo."""
    x0, y0, x1, y1 = box
    h, w = y1 - y0, x1 - x0
    family, weight = "poppins", 800
    # A caixa do OCR envolve as letras: altura de maiúscula/algarismo ~ 85% dela.
    probe = typography.get_font(family, weight, 100)
    cap = probe.getbbox("8")[3] - probe.getbbox("8")[1]
    size = max(10, int(h * 0.86 * 100 / cap))
    font = typography.get_font(family, weight, size)
    while size > 10 and font.getlength(text) > w * 1.35:
        size -= 2
        font = typography.get_font(family, weight, size)
    tw = font.getlength(text)
    left = x0 if align == "left" else (x1 - tw if align == "right" else (x0 + x1) / 2 - tw / 2)
    left = max(4, min(img.width - tw - 4, left))
    baseline = y1 - h * 0.07
    out = img.copy()
    ImageDraw.Draw(out).text((left, baseline), text, font=font, fill=color, anchor="ls")
    return out


def _align(box, width, groups):
    """Alinhamento provável do texto original (esquerda/centro/direita) pela posição na peça."""
    x0, _, x1, _ = box
    cx = (x0 + x1) / 2
    if abs(cx - width / 2) < width * 0.08:
        return "center"
    return "left" if cx < width / 2 else "right"


def instagram(img):
    """4:5 (1080x1350) sem cortar a arte: centraliza e preenche as sobras com a própria arte desfocada."""
    target_w, target_h = 1080, 1350
    ratio = img.width / img.height
    if abs(ratio - 0.8) < 0.02:
        return img.resize((target_w, target_h), Image.LANCZOS)
    bg = ImageOps.fit(img, (target_w, target_h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(28))
    fg = img.copy()
    fg.thumbnail((target_w, target_h), Image.LANCZOS)
    bg.paste(fg, ((target_w - fg.width) // 2, (target_h - fg.height) // 2))
    return bg


def edit(image_bytes, changes, to_instagram=True):
    """Aplica as trocas na imagem do cliente. changes: [{"tipo": "preco"|"texto", "antigo"?: str, "novo": str}].

    Devolve (jpeg, relatorio) ou (None, relatorio) se não for possível com segurança."""
    if ocr.engine() is None:
        return None, {"ok": False, "motivo": "leitura de texto indisponível"}
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    items = ocr.read(img) or []
    report = {"lidos_antes": [i["texto"] for i in items][:40], "trocas": []}
    for change in changes:
        target = find_target(items, change)
        if target is None:
            report["trocas"].append({"novo": change["novo"], "ok": False, "motivo": "texto a trocar não encontrado"})
            continue
        box = tuple(int(v) for v in target["caixa"])
        color, _ = _colors(img, box)
        align = _align(box, img.width, None)
        img = _erase(img, box)
        img = _draw_text(img, box, change["novo"], color, align)
        report["trocas"].append({"antigo": target["texto"], "novo": change["novo"], "caixa": list(box), "ok": None})
    # Validação visual: o novo aparece e o antigo sumiu.
    after = ocr.read(img) or []
    for t in report["trocas"]:
        if t.get("ok") is False:
            continue
        check = ocr.compare([t["novo"]], after)
        new_ok = check["textos"][0]["status"] == "ok"
        old_digits = ocr.digits(t["antigo"])
        still_old = any(ocr.fold(i["texto"]) == ocr.fold(t["antigo"]) or (old_digits and ocr.digits(i["texto"]) == old_digits
                                                                            and old_digits != ocr.digits(t["novo"]))
                        for i in after)
        t["ok"] = new_ok and not still_old
    report["lidos_depois"] = [i["texto"] for i in after][:40]
    report["ok"] = bool(report["trocas"]) and all(t["ok"] for t in report["trocas"])
    if not any(t.get("ok") for t in report["trocas"]):
        return None, report
    final = instagram(img) if to_instagram else img
    out = io.BytesIO()
    final.save(out, "JPEG", quality=93)
    report["tamanho"] = list(final.size)
    return out.getvalue(), report


def price_text(raw):
    """"1000" -> "R$ 1.000"; "1500,00" -> "R$ 1.500,00"; mantém os algarismos do cliente."""
    raw = raw.strip()
    whole, _, cents = raw.partition(",")
    digits = re.sub(r"\D", "", whole)
    if not digits:
        return textplan.format_price(raw)
    grouped = f"{int(digits):,}".replace(",", ".")
    return "R$ " + grouped + (f",{cents}" if cents else "")


# ----------------------------------------------------------------------------------- redesign: ler a arte do cliente
# Para modernizar um encarte SEM trocar os produtos: lê nomes e preços (OCR) e recorta as fotos originais (OpenCV).
# O Studio monta o encarte novo com esses dados e recortes (composição determinística); a IA só faz o fundo.

def _num(text):
    m = re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|\d+", text.replace(" ", ""))
    return m.group(0) if m else ""


def _price_value(group):
    """Preço de um grupo OCR ("R$ 4 ,99" / "R$4,99" / "4,99") -> "4,99"; "de R$ 6,49" também."""
    txt = group["texto"].replace("RS", "R$").replace(" ", "")
    m = re.search(r"(\d{1,3}(?:\.\d{3})*|\d+)[,.](\d{2})(?!\d)", txt)
    if m:
        return f"{m.group(1)},{m.group(2)}"
    parts = [i["texto"] for i in sorted(group["itens"], key=lambda i: i["caixa"][0])]
    digits = [re.sub(r"\D", "", p) for p in parts]
    digits = [d for d in digits if d]
    if len(digits) >= 2 and len(digits[-1]) == 2:
        return f"{digits[-2]},{digits[-1]}"
    return ""


def _photo_regions(img, text_boxes):
    """Fotos de produto: regiões com textura fora das áreas de texto, crescidas até a borda da embalagem.
    Devolve caixas (x0, y0, x1, y1) na escala da imagem."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    scale = 640 / max(img.size)
    small = np.asarray(img.resize((int(img.width * scale), int(img.height * scale))))
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    busy = (grad > 25).astype(np.uint8)
    for x0, y0, x1, y1 in text_boxes:  # texto não é foto
        busy[max(0, int(y0 * scale) - 4):int(y1 * scale) + 4, max(0, int(x0 * scale) - 4):int(x1 * scale) + 4] = 0
    busy = cv2.morphologyEx(busy, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(busy)
    area_img = busy.shape[0] * busy.shape[1]
    seeds = []
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if area < area_img * 0.003 or w < 18 or h < 18 or area / (w * h) < 0.3 or w * h > area_img * 0.35:
            continue
        seeds.append([x, y, x + w, y + h])
    # Junta pedaços da mesma embalagem (próximos).
    merged = True
    while merged:
        merged = False
        for a in range(len(seeds)):
            for b in range(a + 1, len(seeds)):
                A, B = seeds[a], seeds[b]
                if A[0] - 14 < B[2] and B[0] - 14 < A[2] and A[1] - 14 < B[3] and B[1] - 14 < A[3]:
                    seeds[a] = [min(A[0], B[0]), min(A[1], B[1]), max(A[2], B[2]), max(A[3], B[3])]
                    del seeds[b]
                    merged = True
                    break
            if merged:
                break
    # Cresce até a borda da embalagem: pixels diferentes do fundo do cartão, conectados à semente.
    out = []
    for x0, y0, x1, y1 in seeds:
        px, py = int((x1 - x0) * 0.5), int((y1 - y0) * 0.35)
        X0, Y0 = max(0, x0 - px), max(0, y0 - py)
        X1, Y1 = min(small.shape[1], x1 + px), min(small.shape[0], y1 + py)
        reg = small[Y0:Y1, X0:X1].astype(np.int32)
        border = np.concatenate([reg[0], reg[-1], reg[:, 0], reg[:, -1]])
        bg = np.median(border, axis=0)
        fg = (np.abs(reg - bg).sum(axis=2) > 24).astype(np.uint8)
        for bx0, by0, bx1, by1 in text_boxes:
            fg[max(0, int(by0 * scale) - Y0 - 2):max(0, int(by1 * scale) - Y0 + 2),
               max(0, int(bx0 * scale) - X0 - 2):max(0, int(bx1 * scale) - X0 + 2)] = 0
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        n, lab, st, _ = cv2.connectedComponentsWithStats(fg)
        sx0, sy0, sx1, sy1 = x0 - X0, y0 - Y0, x1 - X0, y1 - Y0
        best = None
        for j in range(1, n):
            gx, gy, gw, gh, ga = st[j]
            if gx < sx1 and sx0 < gx + gw and gy < sy1 and sy0 < gy + gh and (best is None or ga > best[4]):
                best = (gx, gy, gw, gh, ga)
        if best:
            gx, gy, gw, gh, _ = best
            x0, y0, x1, y1 = X0 + gx, Y0 + gy, X0 + gx + gw, Y0 + gy + gh
        out.append(tuple(int(v / scale) for v in (x0, y0, x1, y1)))
    return out


_ACCENTS = {"joao": "João", "pilao": "Pilão", "feijao": "Feijão", "acucar": "Açúcar", "cafe": "Café", "maca": "Maçã",
            "pao": "Pão", "limao": "Limão", "melao": "Melão", "sabao": "Sabão", "algodao": "Algodão", "file": "Filé",
            "oleo": "Óleo", "agua": "Água", "lenco": "Lenço", "lencos": "Lenços", "higienico": "Higiênico",
            "preco": "Preço", "promocao": "Promoção", "sao": "São", "frances": "Francês", "pure": "Purê",
            "acai": "Açaí", "farmacia": "Farmácia", "pacoca": "Paçoca", "mamao": "Mamão", "tomate": "Tomate"}


_FLYER_WORDS = ["OFERTAS", "OFERTA", "PROMOÇÕES", "PROMOÇÃO", "SEMANA", "SUPER", "MEGA", "IMPERDÍVEIS", "IMPERDÍVEL",
                "FIM", "DE", "DA", "DO", "DAS", "DOS", "MÊS", "DIA", "DIAS", "SÓ", "HOJE", "QUEIMA", "ESTOQUE", "LIQUIDA",
                "PREÇOS", "PREÇO", "BAIXO", "BAIXOS", "ESPECIAL", "ESPECIAIS", "FESTIVAL", "SEMANAL"]


def _split_caps(word):
    """"OFERTASDASEMANA" -> "OFERTAS DA SEMANA" (palavras comuns de encarte; senão, mantém)."""
    from .ocr import fold  # noqa: PLC0415

    target = fold(word)
    vocab = sorted({(fold(w), w) for w in _FLYER_WORDS}, key=lambda x: -len(x[0]))
    out, i = [], 0
    while i < len(target):
        for key, w in vocab:
            if target.startswith(key, i):
                out.append(w)
                i += len(key)
                break
        else:
            return word
    return " ".join(out)


def tidy_name(text):
    """Nome lido pelo OCR -> legível: separa palavras grudadas ("LeiteIntegral", "Italac1L") e repõe acentos
    comuns. Não troca palavras: o cliente confere no painel."""
    t = " ".join(_split_caps(w) if w.isupper() and len(w) >= 9 else w for w in (text or "").split())
    t = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý])", " ", t)
    t = re.sub(r"(?<=[A-Za-zÀ-ÿ])(?=\d)", " ", t)
    t = re.sub(r"(?<=\d)(?=[A-Za-zÀ-ÿ]{3,})", " ", t)
    words = []
    for w in t.split():
        key = re.sub(r"[^a-z]", "", w.lower())
        fixed = _ACCENTS.get(key)
        if fixed and len(key) == len(w):
            fixed = fixed if w[:1].isupper() else fixed.lower()
            w = fixed.upper() if w.isupper() and len(w) > 1 else fixed
        words.append(w)
    return " ".join(words)


def _reread_price(img, group):
    """Segunda leitura de um preço incompleto ("R$" sem número): recorta à direita e lê em outra escala."""
    x0, y0, x1, y1 = group["caixa"]
    h = y1 - y0
    box = (max(0, x0 - h // 4), max(0, y0 - int(h * 0.85)), min(img.width, x0 + h * 7), min(img.height, y1 + int(h * 0.35)))
    crop = img.crop(box)
    for target in (64, 96, 40):
        k = target / crop.height
        reread = ocr.read(crop.resize((max(8, int(crop.width * k)), target), Image.LANCZOS)) or []
        text = " ".join(i["texto"] for i in sorted(reread, key=lambda i: i["caixa"][0]))
        value = _price_value({"texto": text, "itens": [{"texto": t, "caixa": [0, 0, 0, 0]} for t in text.split()]})
        if value:
            return value
    return ""


def extract_flyer(image_bytes):
    """Lê uma arte comercial do cliente: produtos (nome, preço, preço antigo, recorte da foto original), título/loja.

    Devolve (dados, relatorio). Nada é inventado: nomes e preços vêm da leitura da própria imagem."""
    if ocr.engine() is None:
        return None, {"ok": False, "motivo": "leitura de texto indisponível"}
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    items = [i for i in (ocr.read(img) or []) if i["confianca"] >= 0.6]
    groups = _line_groups(items)
    for g in groups:  # preço incompleto ("R$" sozinho): segunda leitura
        if re.fullmatch(r"\s*R\s?\$\s*", g["texto"]) and not _price_value(g):
            g["releitura"] = _reread_price(img, g)
    # "R$" sem número legível também marca um produto (preço em branco para o cliente preencher).
    prices = [g for g in groups if _price_value(g) or "releitura" in g]
    texts = [g for g in groups if g not in prices and len(re.sub(r"[^A-Za-zÀ-ÿ]", "", g["texto"])) >= 3]
    photos = _photo_regions(img, [i["caixa"] for i in items])

    def height(g):
        return g["caixa"][3] - g["caixa"][1]

    def center(b):
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

    # Preço principal = maior de cada cartão; "de R$ x" (menor, logo acima) = preço antigo.
    mains = sorted(prices, key=height, reverse=True)
    used, products = set(), []
    for p in mains:
        if id(p) in used:
            continue
        pc = center(p["caixa"])
        olds = [q for q in prices if q is not p and id(q) not in used and height(q) < height(p) * 0.7
                and abs(center(q["caixa"])[0] - pc[0]) < img.width * 0.2 and 0 < pc[1] - center(q["caixa"])[1] < height(p) * 2.5]
        if any(height(q) > height(p) * 1.4 and abs(center(q["caixa"])[0] - pc[0]) < img.width * 0.2
               and abs(center(q["caixa"])[1] - pc[1]) < height(q) * 2 for q in mains if q is not p):
            continue  # é o "de" de outro preço
        used.add(id(p))
        old = olds[0] if olds else None
        if old:
            used.add(id(old))
        # Nome: texto mais próximo acima do preço, na mesma coluna.
        above = [t for t in texts if t["caixa"][3] <= p["caixa"][1] + 4 and abs(center(t["caixa"])[0] - pc[0]) < img.width * 0.22]
        name = max(above, key=lambda t: t["caixa"][3]) if above else None
        # Foto: região de textura mais próxima acima do nome/preço, na mesma coluna.
        ref_y = (name or p)["caixa"][1]
        cands = [b for b in photos if b[3] <= ref_y + 10 and abs(center(b)[0] - pc[0]) < img.width * 0.22]
        photo = max(cands, key=lambda b: b[3]) if cands else None
        products.append({"_grupo": p, "_grupo_de": old,
                         "nome": tidy_name(name["texto"]) if name else "", "preco": _price_value(p) or p.get("releitura", ""),
                         "preco_de": (_price_value(old) or old.get("releitura", "")) if old else "", "foto_caixa": photo,
                         "_y": p["caixa"][1], "_x": p["caixa"][0]})
    # Texto impresso na embalagem (ex.: "NESCAU") colado à foto faz parte da foto, não do layout.
    layout = {id(it) for g in groups if g in prices or any(tidy_name(g["texto"]) == pr["nome"] for pr in products)
              for it in g["itens"]}
    for pr in products:
        box = pr["foto_caixa"]
        if not box:
            continue
        grew = True
        while grew:
            grew = False
            for it in items:
                if id(it) in layout or _price_value({"texto": it["texto"], "itens": [it]}):
                    continue
                bx0, by0, bx1, by1 = it["caixa"]
                overlap = min(box[2], bx1) - max(box[0], bx0)
                gap = max(box[1] - by1, by0 - box[3])
                inside = bx0 >= box[0] and bx1 <= box[2] and by0 >= box[1] and by1 <= box[3]
                if not inside and overlap > 0.5 * (bx1 - bx0) and gap < (box[3] - box[1]) * 0.15:
                    box = (min(box[0], bx0), min(box[1], by0), max(box[2], bx1), max(box[3], by1))
                    grew = True
        pr["foto_caixa"] = box
    # Dupla leitura dos preços: se a releitura em outra escala discordar, o preço fica marcado para conferir.
    for pr in products:
        conferir = []
        main, old = pr.pop("_grupo"), pr.pop("_grupo_de")
        if pr["preco_de"] or old is not None:
            conferir.append("preco_de")  # preço riscado: o traço atravessa os algarismos, leitura incerta
        if main is not None and "releitura" in main and "preco" not in conferir:
            conferir.append("preco")  # veio da segunda leitura (ou não foi lido): o cliente precisa confirmar
        if main is not None and min(i["confianca"] for i in main["itens"]) < 0.9:
            second = _reread_price(img, main)
            if not second or second != pr["preco"]:
                conferir.append("preco")
        if not pr["nome"]:
            conferir.append("nome")
        pr["conferir"] = conferir
    products.sort(key=lambda r: (round(r["_y"] / (img.height * 0.15)), r["_x"]))
    top = [t for t in texts if t["caixa"][3] < img.height * 0.25 and t not in
           [x for x in texts if any(x["texto"] == pr["nome"] for pr in products)]]
    top.sort(key=height, reverse=True)
    titulo = tidy_name(top[0]["texto"]) if top else ""
    loja = tidy_name(top[1]["texto"]) if len(top) > 1 else ""
    crops = []
    for pr in products:
        box = pr.pop("foto_caixa")
        pr.pop("_y"), pr.pop("_x")
        crop = None
        if box:
            pad = 6
            c = img.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(img.width, box[2] + pad), min(img.height, box[3] + pad)))
            buf = io.BytesIO()
            c.save(buf, "JPEG", quality=94)
            crop = buf.getvalue()
        crops.append(crop)
    data = {"titulo": titulo, "loja": loja, "produtos": products}
    report = {"ok": bool(products), "produtos": len(products), "com_foto": sum(1 for c in crops if c),
              "lidos": [i["texto"] for i in items][:60]}
    return {"dados": data, "fotos": crops}, report
