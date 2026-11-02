"""Segunda validação: leitura visual independente (OCR local, gratuito, na CPU).

Motor: RapidOCR (modelos PP-OCR do PaddleOCR rodando em ONNX Runtime). Mesma qualidade do PaddleOCR sem instalar
o framework Paddle; cerca de 110 MB de RAM e 1,5 a 3 s por imagem na CPU. É opcional: sem o pacote
`rapidocr-onnxruntime` (ex.: no servidor), a validação fica só com as verificações internas da tipografia.

A leitura NÃO substitui a validação interna (que compara o que foi desenhado com o pedido). Ela confere o
resultado visual: textos ausentes, cortados ou ilegíveis e letras indesejadas na arte. A comparação ignora acentos
e pontuação, porque o OCR costuma perder acentos ("Verão" -> "Verao").
"""
import io
import os
import re
import threading
import time
import unicodedata

_engine = {"obj": None, "erro": None}
_lock = threading.Lock()
MAX_SIDE = int(os.getenv("SG_OCR_MAX_SIDE", "1280"))


def enabled():
    return os.getenv("SG_OCR", "1") != "0"


def engine():
    """Carrega o RapidOCR uma vez (CPU). None se indisponível."""
    if not enabled():
        return None
    with _lock:
        if _engine["obj"] is None and _engine["erro"] is None:
            try:
                from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415 — dependência opcional

                _engine["obj"] = RapidOCR()
            except Exception as exc:  # noqa: BLE001
                _engine["erro"] = f"{type(exc).__name__}: {exc}"[:200]
    return _engine["obj"]


def fold(text):
    """Minúsculas, sem acentos e só letras/números (base da comparação tolerante)."""
    text = unicodedata.normalize("NFD", str(text or "").lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", text)


def digits(text):
    return re.sub(r"\D", "", str(text or ""))


def read(image):
    """Textos lidos: [{"texto", "confianca", "caixa"}]. image: PIL.Image ou bytes."""
    eng = engine()
    if eng is None:
        return None
    from PIL import Image  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    img = Image.open(io.BytesIO(image)) if isinstance(image, (bytes, bytearray)) else image
    img = img.convert("RGB")
    scale = min(1.0, MAX_SIDE / max(img.size))
    if scale < 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    result, _ = eng(np.asarray(img))
    items = []
    for box, text, conf in result or []:
        pts = [(float(x) / scale, float(y) / scale) for x, y in box]
        items.append({"texto": str(text), "confianca": round(float(conf), 3),
                      "caixa": [round(min(p[0] for p in pts)), round(min(p[1] for p in pts)),
                                round(max(p[0] for p in pts)), round(max(p[1] for p in pts))]})
    return items


_CONFUSIONS = str.maketrans({"0": "o", "1": "i", "l": "i", "5": "s", "8": "b", "|": "i"})


def _letters(text):
    """Forma de comparação das letras: confusões típicas do OCR igualadas (0/o, 1/l/i, 5/s…)."""
    return text.translate(_CONFUSIONS).replace("rn", "m").replace("vv", "w")


def best_distance(needle, hay):
    """Menor distância de edição entre `needle` e qualquer trecho de `hay` (alinhamento semi-global)."""
    if not needle:
        return 0
    prev = [0] * (len(hay) + 1)
    for i, a in enumerate(needle, 1):
        cur = [i] + [0] * len(hay)
        for j, b in enumerate(hay, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a != b))
        prev = cur
    return min(prev)


def _partial_ratio(needle, hay):
    """Semelhança 0..1 de `needle` com o melhor trecho de `hay` (1 - distância/tamanho)."""
    if not needle:
        return 1.0
    if not hay:
        return 0.0
    return max(0.0, 1 - best_distance(needle, hay) / len(needle))


def compare(expected, items, min_conf=0.6):
    """Compara os textos pedidos com a leitura. Devolve o relatório (sem efeitos colaterais)."""
    readable = [i for i in items if i["confianca"] >= min_conf]
    hay = "".join(fold(i["texto"]) for i in readable)
    hay_digits = "".join(digits(i["texto"]) for i in readable)
    report = []
    letters_hay = _letters(hay)
    for text in expected:
        f = _letters(fold(text))
        dist = best_distance(f, letters_hay) if f else 0
        ratio = max(0.0, 1 - dist / len(f)) if f else 1.0
        d = digits(text)
        digits_ok = (not d) or d in hay_digits
        if re.fullmatch(r"R\$\s?[\d.,]+(\s?(mil|milh[aã]o|milh[oõ]es))?", text.strip()):
            # Preço: o selo é lido em pedaços ("RS", "199", ",90"); vale a sequência exata dos algarismos.
            dist, ratio = (0, 1.0) if digits_ok else (len(d), 0.0)
        # Letras: idênticas depois de igualar acentos e confusões típicas; números: exatos.
        if digits_ok and dist == 0:
            status = "ok"
        elif digits_ok and ratio >= 0.6:
            status = "parcial"
        else:
            status = "nao_lido"
        report.append({"texto": text, "status": status, "semelhanca": round(ratio, 2), "diferencas": dist,
                       "numeros_ok": digits_ok})
    # Texto lido que não corresponde a nada pedido: letra falsa na arte (ou resto de logotipo inventado).
    folded_expected = [_letters(fold(t)) for t in expected]
    extras = []
    for item in readable:
        f = _letters(fold(item["texto"]))
        if len(f) < 3 or item["confianca"] < 0.8:
            continue
        if any(_partial_ratio(f, e) >= 0.7 or f in e for e in folded_expected):
            continue
        if any(d and digits(item["texto"]) and digits(item["texto"]) in digits(t) for t in expected for d in [digits(t)]):
            continue
        extras.append({"texto": item["texto"], "confianca": item["confianca"], "caixa": item["caixa"]})
    return {"textos": report, "indesejados": extras,
            "ok": all(r["status"] == "ok" for r in report) and not extras}


def validate(image, expected):
    """Lê e compara. Devolve None se o OCR não estiver disponível."""
    started = time.time()
    items = read(image)
    if items is None:
        return None
    out = compare(expected, items)
    out["tempo_s"] = round(time.time() - started, 2)
    out["lidos"] = [i["texto"] for i in items][:30]
    return out


def status():
    eng = engine()
    return {"disponivel": eng is not None, "erro": _engine["erro"], "motor": "rapidocr-onnxruntime (PP-OCR, CPU)"}
