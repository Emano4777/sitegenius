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


def _format_words(briefing):
    if briefing.get("tamanho"):
        w, h = briefing["tamanho"]
        r = w / h
        return ("horizontal panorâmico" if r > 1.6 else "horizontal" if r > 1.1 else "quadrado" if r > 0.9
                else "vertical alto" if r < 0.62 else "vertical") + f" {w}x{h}"
    return FORMAT_WORDS.get(briefing.get("formato"), "vertical 4:5")


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
    parts = [f"imagem de fundo publicitário, formato {_format_words(briefing)}"]
    if plan.get("direcao_visual"):
        parts.insert(0, plan["direcao_visual"])  # direção artística livre do diretor (estilo, paleta, luz, câmera)
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
    zones = solved["zonas_objetos"] or []
    for j, obj in enumerate(cen["objetos"]):  # cada objeto numa zona prevista (alternando se houver mais objetos que zonas)
        zone = zones[j % len(zones)] if zones else None
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
    # nome completo dos produtos e da empresa nunca vão para o gerador (evita embalagens e letreiros inventados);
    # palavras comuns (café, arroz, casa) continuam, porque descrevem o tema
    for name in sorted({p["nome"] for p in briefing.get("produtos") or []} | {briefing.get("loja") or ""}, key=len, reverse=True):
        if name and len(name) > 3:
            text = re.sub(re.escape(name), "", text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip()


_EN_POS = {"à esquerda": "left", "à direita": "right", "no centro": "center"}
_EN_V = {"no alto": "upper", "embaixo": "lower", "no meio": "middle"}
_TRIGGERS = re.compile(r"\b(text|texts|letter\w*|word\w*|typograph\w*|font\w*|logo\w*|label\w*|sign\w*|poster\w*|advert\w*|"
                       r"brand\w*|packag\w*|product\w*|price\w*|banner\w*|caption\w*|headline\w*|title\w*|written|writing|"
                       r"texto\w*|letra\w*|palavra\w*|marca\w*|embalage\w*|produto\w*|pre[çc]o\w*|cartaz\w*|placa\w*|"
                       r"etiqueta\w*|r[óo]tulo\w*|textual|callout\w*|grid|guide\w*|layout\w*|infograph\w*|magazine\w*|"
                       r"editorial|menu\w*|flyer\w*|leaflet\w*|catalog\w*|sale\w*|discount\w*|offer\w*)\b", re.I)


TONE_EN = {  # tom declarado pelo diretor -> atmosfera em inglês (o Klein entende melhor; nunca é estilo fixo por segmento)
    "impacto": "bold high-energy mood, dramatic contrast, vivid saturated accents, dynamic light",
    "urgente": "intense urgent mood, strong contrast, glowing accents, motion energy",
    "sofisticado": "sophisticated refined mood, elegant materials, controlled soft light",
    "elegante": "elegant luxurious mood, soft glow, premium textures, calm composition",
    "moderno": "clean modern mood, crisp light, minimal contemporary materials",
    "tecnologico": "futuristic high-tech mood, cool glow, sleek reflective surfaces",
    "divertido": "playful cheerful mood, bright colors, lively light",
    "acolhedor": "warm cozy inviting mood, golden soft light, friendly textures",
    "natural": "fresh natural mood, daylight, organic textures and greenery",
    "festivo": "festive celebratory mood, sparkling lights, rich colors",
    "artesanal": "handcrafted rustic mood, warm light, natural wood and craft textures",
}


def _area(box):
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    hz = "left" if cx < 36 else "right" if cx > 64 else "center"
    vt = "upper" if cy < 36 else "lower" if cy > 64 else "middle"
    return f"{vt} {hz}" if hz != "center" else f"{vt} area"


def prompt_klein(plan, solved, briefing):
    """Prompt do cenário para o FLUX.2 Klein: só o que a cena DEVE ter, em inglês, sem nenhuma palavra que puxe escrita
    (no FLUX, negar "texto"/"embalagem" tende a fazê-los aparecer). Áreas dos textos viram superfícies calmas; as bordas
    ganham detalhes do ambiente para a cena não ficar vazia. Genérico: vem do plano e da geometria."""
    cen = plan["cenario"]
    els = solved["elementos"]
    # uma cena única e contínua: pedir áreas "em cima e embaixo" fazia o Klein gerar duas imagens empilhadas
    parts = [plan.get("direcao_visual") or "", f"One single continuous scene, one camera shot, {_format_words(briefing).split(' ')[0]} framing",
             TONE_EN.get(plan.get("tom") or "", "")]
    if cen.get("ambiente"):
        parts.append(f"setting: {cen['ambiente']}")
    if not cen.get("ambiente") and not plan.get("direcao_visual"):
        # plano sem cena descrita: a cena evoca o segmento e o tom (o gerador decide os elementos), nunca fica vazia
        # objetos genéricos sem superfície para rótulo (frascos e caixas vinham com escritos inventados)
        parts.append(f"a beautiful real-world environment that evokes {briefing.get('segmento') or 'the business'}, built from "
                     f"natural materials, plants, fabrics, light and architecture of that world, layered depth")
    hero = next((e for e in els if e["papel"] == "heroi"), None)
    if cen["superficie"] != "nenhuma" and hero:
        parts.append(f"a clean empty {cen['superficie']} surface in the {_area(hero['b'])}, bare and uncluttered, ready for an object")
    zones = solved["zonas_objetos"] or []
    for j, obj in enumerate(cen["objetos"]):
        z = zones[j % len(zones)] if zones else None
        parts.append(f"{obj} in the {_area(z)}" if z else obj)
    calm = sorted({_area(e["b"]) for e in els if e["papel"] in ("titulo", "preco_principal", "lista")})
    if calm:  # espaço livre descrito como respiro da MESMA cena (não como painéis separados)
        parts.append("generous open negative space with a softly blurred, evenly lit background around the subject, "
                     "shallow depth of field")
    parts.append("rich environmental details and atmosphere toward the edges and corners")
    if cen.get("luz"):
        parts.append(f"dominant light color {cen['luz']}")
    parts.append("cinematic lighting, high detail, sharp focus, professional color grading")
    text = ". ".join(p.strip(" .,") for p in parts if p)
    for name in sorted({p["nome"] for p in briefing.get("produtos") or []} | {briefing.get("loja") or ""}, key=len, reverse=True):
        if name and len(name) > 3:
            text = re.sub(re.escape(name), "", text, flags=re.I)
    # remove qualquer frase que ainda cite escrita, embalagem ou produto
    text = " ".join(s for s in re.split(r"(?<=[.;,])\s+", text) if not _TRIGGERS.search(s))
    text = re.sub(r"[,;]\s*(?=\.|$)", "", text)  # vírgula solta onde uma frase foi removida
    return re.sub(r"\s{2,}", " ", text).strip(" .,") + "."


def full_prompt(plan, briefing):
    """Abordagem A (geração completa): o gerador tenta desenhar a arte INTEIRA, com os textos exatos entre aspas.
    Só para comparação: a fidelidade dos textos é medida depois por OCR."""
    texts = [(plan["titulo"]["txt"], "large bold headline")]
    if plan.get("loja"):
        texts.append((plan["loja"], "small brand name"))
    if plan.get("chamada"):
        texts.append((plan["chamada"]["txt"], "short tagline"))
    for i, p in enumerate(briefing.get("produtos") or []):
        role = "main product" if i == plan.get("heroi") else "product"
        texts.append((p["nome"], f"{role} name"))
        if p.get("preco"):
            texts.append((p["preco"], f"{role} price, big and clear"))
    if plan.get("selo"):
        texts.append((plan["selo"]["txt"], "badge"))
    for k, v in (briefing.get("info") or {}).items():
        texts.append((f"{k.capitalize()}: {v}", "small footer info"))
    quoted = "; ".join(f'"{t}" ({role})' for t, role in texts if t)
    head = plan.get("direcao_visual") or plan["cenario"].get("ambiente") or ""
    return (f"Professional advertising poster, {_format_words(briefing)}. {head} The poster shows these exact texts, spelled exactly "
            f"as written, with correct Portuguese accents: {quoted}. Clear hierarchy, legible typography, products and prices "
            f"well highlighted.")


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
    g = np.asarray(img.convert("L").resize((120, 150)), dtype=np.float32)
    mean = g.mean(axis=1)
    # linha sem detalhe E chapada em branco/preto puro: imagem incompleta (degradê suave de parede/céu não conta)
    flat = (g.std(axis=1) < 2.0) & ((mean > 246) | (mean < 8))
    for band in (flat[:], flat[::-1]):
        run = int(np.argmax(~band)) if (~band).any() else len(band)
        if run > len(band) * 0.15:
            issues.append(("area_vazia", round(run / len(band), 2)))
            break
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
    empty = any(p[0] == "area_vazia" for p in report["problemas"])
    if (big or empty or len([p for p in report["problemas"] if p[0] == "zona_de_texto_poluida"]) >= 2) and attempt < max_attempts:
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
