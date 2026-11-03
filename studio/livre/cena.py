"""Cenário planejado junto com a composição.

1) prompt(): o prompt do gerador sai do PLANO e das ZONAS do resolvedor (onde ficam título, produto, preço e os
   objetos decorativos). O cenário nasce com espaço para a composição, não o contrário.
2) verify(): confere o cenário gerado — texto inventado (OCR), objetos fora das zonas previstas, zonas de texto
   poluídas — e decide: aceitar, corrigir (suavizar o texto inventado) ou gerar de novo (com limite de tentativas).
Funciona com qualquer gerador e qualquer OCR (funções injetadas); nada é específico de um exemplo.
"""
import re

import numpy as np
from PIL import Image, ImageFilter

FORMAT_WORDS = {"feed_4x5": "vertical 4:5", "story": "vertical 9:16", "quadrado": "quadrado 1:1", "a4": "vertical A4"}


def _where(box):
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    hx = "à esquerda" if cx < 36 else "à direita" if cx > 64 else "no centro"
    vy = "no alto" if cy < 36 else "embaixo" if cy > 64 else "no meio"
    return f"{hx}, {vy} (x {x:.0f}-{x + w:.0f}%, y {y:.0f}-{y + h:.0f}%)"


def prompt(plan, solved, briefing):
    """Prompt do cenário a partir do plano semântico + geometria resolvida."""
    cen = plan["cenario"]
    els = solved["elementos"]
    parts = [f"imagem de fundo publicitário, formato {FORMAT_WORDS.get(briefing.get('formato'), 'vertical 4:5')}"]
    if cen["ambiente"]:
        parts.append(cen["ambiente"])
    hero = next((e for e in els if e["papel"] == "heroi"), None)
    if cen["superficie"] != "nenhuma" and solved.get("superficie"):
        if hero:
            hb = hero["b"]
            parts.append(f"um {cen['superficie']} vazio com o topo na altura y {solved['superficie']:.0f}% e centralizado em "
                         f"x {hb[0] + hb[2] / 2:.0f}% (largura de uns {min(90, hb[2] * 1.8):.0f}%), para apoiar um produto")
        else:
            parts.append(f"um {cen['superficie']} vazio na parte de baixo, com o topo em y {solved['superficie']:.0f}%")
    for obj, zone in zip(cen["objetos"], solved["zonas_objetos"] or [None] * len(cen["objetos"])):
        parts.append(f"{obj} {_where(zone)}" if zone else obj)
    free = [e for e in els if e["tipo"] in ("texto", "preco", "lista", "info")]
    if free:
        parts.append("áreas lisas, escuras ou desfocadas (sem objetos) " + "; ".join(_where(e["b"]) for e in free[:4]))
    if hero:
        parts.append(f"espaço vazio {_where(hero['b'])} para o produto (não desenhe nenhum produto ali)")
    parts.append(f"luz dominante {cen['luz']}")
    parts.append("sem nenhum texto, sem letras, sem números, sem logotipos, sem marcas, sem embalagens e sem produtos")
    text = ", ".join(parts)
    # nomes de produtos/empresa nunca vão para o gerador (evita embalagens e letreiros inventados)
    for word in {w for p in briefing.get("produtos") or [] for w in p["nome"].split() if len(w) > 3} | {briefing.get("loja") or ""}:
        if word:
            text = re.sub(re.escape(word), "", text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip()


def _saliency(img):
    g = np.asarray(img.convert("L").resize((96, 120)), dtype=np.float32)
    gy, gx = np.gradient(g)
    return np.hypot(gx, gy)


def _zone_mean(m, box):
    H, W = m.shape
    x, y, w, h = box
    x0, x1 = int(max(0, x) * W / 100), int(min(100, x + w) * W / 100)
    y0, y1 = int(max(0, y) * H / 100), int(min(100, y + h) * H / 100)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float(m[y0:y1, x0:x1].mean())


def verify(img, plan, solved, ocr=None, official_texts=()):
    """Problemas do cenário. ocr(img) -> [(x, y, w, h, texto, confiança)] em % (ou None se não houver OCR)."""
    issues = []
    texts = []
    if ocr:
        official = {re.sub(r"[^a-z0-9]", "", t.lower()) for t in official_texts}
        for x, y, w, h, txt, conf in ocr(img) or []:
            clean = re.sub(r"[^A-Za-z0-9]", "", txt or "")
            if len(clean) >= 2 and conf >= 0.5:
                texts.append({"b": [x, y, w, h], "texto": txt, "conf": round(conf, 2),
                              "oficial": clean.lower() in official})
    for t in texts:
        # texto inventado pelo gerador: confunde a mensagem e pode contradizer os textos oficiais
        issues.append(("texto_inventado", t["texto"], t["b"]))
    sal = _saliency(img)
    base = float(np.median(sal))
    for e in solved["elementos"]:
        if e["papel"] in ("titulo", "preco_principal"):
            m = _zone_mean(sal, e["b"])
            if m > max(10.0, base * 2.6):
                issues.append(("zona_de_texto_poluida", e["papel"], round(m, 1)))
    for z in solved.get("zonas_objetos") or []:
        if plan["cenario"]["objetos"] and _zone_mean(sal, z) < base * 0.9:
            issues.append(("objeto_fora_da_zona", z))
    return {"problemas": issues, "textos": texts}


def decide(report, attempt, max_attempts=2):
    """Regerar quando o texto inventado é grande ou disputa espaço com os textos oficiais; senão corrigir na imagem."""
    # grande = mais de 1,2% da área da peça ou letras com mais de 4,5% da altura (b em % de largura/altura)
    big = [t for t in report["textos"] if t["b"][2] * t["b"][3] / 100 > 1.2 or t["b"][3] > 4.5]
    if (big or len([p for p in report["problemas"] if p[0] == "zona_de_texto_poluida"]) >= 2) and attempt < max_attempts:
        return "regerar"
    if report["textos"]:
        return "corrigir"
    return "aceitar"


def soften_text(img, texts):
    """Apaga texto inventado: borra a região com folga e funde com a cor do entorno (fica fundo, não letra)."""
    img = img.convert("RGB").copy()
    W, H = img.size
    for t in texts:
        x, y, w, h = t["b"]
        pad_x, pad_y = w * 0.25 + 1, h * 0.45 + 1
        box = (int(max(0, x - pad_x) * W / 100), int(max(0, y - pad_y) * H / 100),
               int(min(100, x + w + pad_x) * W / 100), int(min(100, y + h + pad_y) * H / 100))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        region = img.crop(box)
        rad = max(6, (box[3] - box[1]) // 2)
        blurred = region.filter(ImageFilter.GaussianBlur(rad))
        mask = Image.new("L", region.size, 0)
        inner = Image.new("L", (max(1, region.size[0] - 2 * int(rad * 0.6)), max(1, region.size[1] - 2 * int(rad * 0.6))), 255)
        mask.paste(inner, (int(rad * 0.6), int(rad * 0.6)))
        mask = mask.filter(ImageFilter.GaussianBlur(rad * 0.6))
        img.paste(Image.composite(blurred, region, mask), box[:2])
    return img


def rapidocr_fn():
    """OCR local (RapidOCR, o mesmo da HostGator). None se não estiver instalado."""
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415
    except ImportError:
        return None
    eng = RapidOCR()

    def run(img):
        im = img.convert("RGB")
        W, H = im.size
        res, _ = eng(np.asarray(im))
        out = []
        for box, txt, conf in res or []:
            xs = [p[0] for p in box]; ys = [p[1] for p in box]
            out.append((min(xs) * 100 / W, min(ys) * 100 / H, (max(xs) - min(xs)) * 100 / W, (max(ys) - min(ys)) * 100 / H, txt, float(conf)))
        return out
    return run
