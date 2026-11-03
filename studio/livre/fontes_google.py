"""Tipografia praticamente ilimitada e gratuita: catálogo do Google Fonts (famílias de código aberto, OFL/Apache/UFL).

O diretor criativo DESCREVE a fonte (classe, peso, largura, personalidade em palavras); aqui se escolhe a família
do catálogo que melhor combina, evitando as usadas nas últimas artes. Só as famílias escolhidas são baixadas e
guardadas localmente (cache), com a licença junto. Sem API paga e sem chave: metadados públicos do Google Fonts e
arquivos do repositório público google/fonts.

Nada aqui é regra por segmento: a personalidade vem da leitura do briefing pelo diretor.
"""
import csv
import io
import json
import math
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("SG_FONT_CACHE") or os.path.join(HERE, "fontes_cache")
UA = {"User-Agent": "SiteGeniusStudio/2.6 (+https://www.sitegenius.com.br/studio)"}
META_URL = "https://fonts.google.com/metadata/fonts"
TAGS_URL = "https://raw.githubusercontent.com/google/fonts/main/tags/all/families.csv"
RAW = "https://raw.githubusercontent.com/google/fonts/main/{lic}/{dir}/{file}"

# palavras que o diretor usa -> etiquetas do catálogo (peso de cada uma)
WORDS = {
    "impacto": {"/Expressive/Loud": 1, "/Expressive/Excited": 0.7, "/Expressive/Active": 0.6},
    "forte": {"/Expressive/Loud": 0.8, "/Expressive/Rugged": 0.6},
    "urgente": {"/Expressive/Excited": 1, "/Expressive/Active": 0.8, "/Expressive/Loud": 0.6},
    "agressivo": {"/Expressive/Rugged": 1, "/Theme/Stencil": 0.6, "/Expressive/Loud": 0.5},
    "esportivo": {"/Expressive/Active": 1, "/Expressive/Rugged": 0.5},
    "elegante": {"/Expressive/Sophisticated": 1, "/Expressive/Fancy": 0.6, "/Serif/Didone": 0.4},
    "sofisticado": {"/Expressive/Sophisticated": 1, "/Expressive/Business": 0.3},
    "luxo": {"/Expressive/Fancy": 1, "/Expressive/Sophisticated": 0.8, "/Serif/Didone": 0.6},
    "classico": {"/Serif/Old Style Garalde": 0.8, "/Serif/Transitional": 0.6, "/Expressive/Sophisticated": 0.4},
    "moderno": {"/Expressive/Innovative": 0.8, "/Sans/Geometric": 0.7, "/Sans/Neo Grotesque": 0.4},
    "minimalista": {"/Expressive/Calm": 0.8, "/Sans/Geometric": 0.6, "/Expressive/Competent": 0.4},
    "tecnologico": {"/Theme/Techno": 1, "/Expressive/Futuristic": 0.9, "/Expressive/Innovative": 0.5},
    "futurista": {"/Expressive/Futuristic": 1, "/Theme/Techno": 0.7},
    "corporativo": {"/Expressive/Business": 1, "/Expressive/Competent": 0.8},
    "confiavel": {"/Expressive/Competent": 1, "/Expressive/Sincere": 0.7, "/Expressive/Calm": 0.5},
    "acolhedor": {"/Expressive/Sincere": 0.9, "/Expressive/Happy": 0.6, "/Sans/Rounded": 0.6, "/Expressive/Calm": 0.4},
    "amigavel": {"/Expressive/Happy": 0.9, "/Sans/Rounded": 0.7, "/Expressive/Sincere": 0.5},
    "limpo": {"/Expressive/Calm": 0.8, "/Purpose/Easy Reading": 0.6, "/Sans/Humanist": 0.5},
    "saude": {"/Expressive/Calm": 0.8, "/Expressive/Competent": 0.6, "/Sans/Humanist": 0.6},
    "divertido": {"/Expressive/Playful": 1, "/Expressive/Happy": 0.6, "/Theme/Wacky": 0.4},
    "infantil": {"/Expressive/Childlike": 1, "/Expressive/Cute": 0.8, "/Theme/Blobby": 0.5},
    "fofo": {"/Expressive/Cute": 1, "/Expressive/Childlike": 0.5},
    "retro": {"/Expressive/Vintage": 1, "/Theme/Woodtype": 0.5, "/Theme/Art Deco": 0.5},
    "vintage": {"/Expressive/Vintage": 1, "/Slab/Clarendon": 0.5},
    "artesanal": {"/Script/Handwritten": 0.9, "/Theme/Brush": 0.7, "/Script/Informal": 0.6},
    "manuscrito": {"/Script/Handwritten": 1, "/Script/Informal": 0.7},
    "pincel": {"/Theme/Brush": 1, "/Script/Informal": 0.5},
    "natural": {"/Script/Informal": 0.6, "/Expressive/Calm": 0.6, "/Sans/Humanist": 0.4},
    "romantico": {"/Script/Formal": 1, "/Seasonal/Valentine's Day": 0.7, "/Expressive/Fancy": 0.5},
    "festivo": {"/Expressive/Happy": 0.8, "/Expressive/Fancy": 0.6, "/Expressive/Excited": 0.5},
    "natal": {"/Seasonal/Christmas": 1, "/Expressive/Fancy": 0.4},
    "artistico": {"/Expressive/Artistic": 1},
    "rustico": {"/Expressive/Rugged": 0.8, "/Theme/Distressed": 0.7, "/Theme/Woodtype": 0.5},
    "art deco": {"/Theme/Art Deco": 1},
    "gotico": {"/Theme/Blackletter": 1, "/Theme/Medieval": 0.6},
}
CLASSES = {"serifa": ("Serif",), "sem_serifa": ("Sans Serif",), "display": ("Display",), "manuscrita": ("Handwriting",),
           "slab": ("Serif",), "qualquer": ("Serif", "Sans Serif", "Display", "Handwriting")}
EXCLUDE = ("/Special use/", "/Monospace/", "/Theme/Pixel", "/Theme/Horror")


def _norm(s):
    import unicodedata  # noqa: PLC0415
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower().strip()


def _fetch(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def build_catalog(path=None, meta_bytes=None, tags_bytes=None):
    """Baixa metadados + etiquetas e grava um catálogo compacto (uma vez; depois é lido do disco)."""
    meta = json.loads(meta_bytes or _fetch(META_URL))
    tags = {}
    for row in csv.reader(io.StringIO((tags_bytes or _fetch(TAGS_URL)).decode("utf-8"))):
        if len(row) >= 4 and row[2].startswith("/") and not row[1]:
            try:
                tags.setdefault(row[0], {})[row[2]] = float(row[3]) / 100
            except ValueError:
                pass
    out = []
    for f in meta["familyMetadataList"]:
        if "latin" not in (f.get("subsets") or []) or f.get("isBrandFont") or f.get("isNoto") or not f.get("isOpenSource", True):
            continue
        t = tags.get(f["family"], {})
        if any(any(k.startswith(x) for x in EXCLUDE) for k in t):
            continue
        weights = sorted({int(re.sub(r"\D", "", w) or 400) for w in (f.get("fonts") or {}) if not w.endswith("i")})
        axes = {a["tag"]: (a["min"], a["max"]) for a in f.get("axes") or []}
        if "wght" in axes:
            weights = sorted(set(weights) | {int(axes["wght"][0]), int(axes["wght"][1])})
        f400 = (f.get("fonts") or {}).get("400") or next(iter((f.get("fonts") or {}).values()), {})
        out.append({"f": f["family"], "c": f.get("category"), "w": weights, "lar": f400.get("width"), "esp": f400.get("thickness"),
                    "pop": f.get("popularity") or 9999, "t": {k: round(v, 2) for k, v in t.items() if v >= 0.15}})
    path = path or os.path.join(CACHE, "catalogo.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return out


_CAT = None


def catalog(path=None):
    global _CAT
    if _CAT is None:
        path = path or os.path.join(CACHE, "catalogo.json")
        _CAT = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else build_catalog(path)
    return _CAT


def choose(desc, recent=(), seed=0, cat=None):
    """desc: {"classe": serifa|sem_serifa|display|manuscrita|slab|qualquer, "peso": leve|normal|pesado|ultra,
    "largura": condensada|normal|larga, "personalidade": ["palavras"...], "familia": opcional}.
    Devolve {"familia", "peso", "score", "motivo"} ou None."""
    cat = cat or catalog()
    desc = desc if isinstance(desc, dict) else {}
    want = str(desc.get("familia") or "").strip()
    if want:
        hit = next((f for f in cat if _norm(f["f"]) == _norm(want)), None)
        if hit and hit["f"] not in recent:
            return {"familia": hit["f"], "peso": _weight(hit, desc.get("peso")), "score": 99, "motivo": "pedida pelo diretor"}
    classes = CLASSES.get(_norm(desc.get("classe")).replace(" ", "_"), CLASSES["qualquer"])
    words = [_norm(w) for w in (desc.get("personalidade") or []) if w][:5]
    target = {}
    for w in words:
        for k, v in WORDS.get(w, {}).items():
            target[k] = target.get(k, 0) + v
    if _norm(desc.get("classe")) == "slab":
        target.update({"/Slab/Geometric": 0.6, "/Slab/Clarendon": 0.6, "/Slab/Humanist": 0.6})
    peso = _norm(desc.get("peso")) or "normal"
    largura = _norm(desc.get("largura")) or "normal"
    scored = []
    for f in cat:
        if f["c"] not in classes or f["f"] in recent:
            continue
        s = sum(f["t"].get(k, 0) * v for k, v in target.items())
        if target and s <= 0:
            continue
        if peso in ("pesado", "ultra") and max(f["w"]) < (800 if peso == "ultra" else 700) and (f.get("esp") or 0) < 7:
            continue
        if peso == "leve" and min(f["w"]) > 300 and (f.get("esp") or 5) > 4:
            continue
        if largura == "condensada" and (f.get("lar") or 7) > 5:
            s *= 0.4
        if largura == "larga" and (f.get("lar") or 7) < 8:
            s *= 0.6
        s += 0.35 / (1 + math.log10(max(1, f["pop"])))  # leve preferência por famílias bem desenhadas e testadas
        scored.append((s, f))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    top, seen = [], set()
    for s_, f in scored:  # uma variante por família-base (Playfair, Playfair Display, Playfair Display SC = uma)
        base = _norm(f["f"]).split()[0]
        if base not in seen:
            seen.add(base)
            top.append((s_, f))
        if len(top) >= 8:
            break  # variedade: entre as melhores, a escolha gira pela semente do briefing
    s, f = top[seed % len(top)]
    return {"familia": f["f"], "peso": _weight(f, peso), "score": round(s, 2),
            "motivo": ", ".join(sorted(f["t"], key=lambda k: -f["t"][k])[:3])}


def _weight(f, peso):
    peso = _norm(peso) or "normal"
    goal = {"leve": 300, "normal": 500, "pesado": 800, "ultra": 900}.get(peso, 500)
    return min(f["w"], key=lambda w: abs(w - goal))


def _dirname(family):
    return re.sub(r"[^a-z0-9]", "", family.lower())


def ensure(family, timeout=30):
    """Arquivo .ttf da família no cache local (baixa uma vez do repositório público google/fonts). (caminho, licença)."""
    d = os.path.join(CACHE, _dirname(family))
    meta_p = os.path.join(d, "fonte.json")
    if os.path.exists(meta_p):
        m = json.load(open(meta_p, encoding="utf-8"))
        p = os.path.join(d, m["arquivo"])
        if os.path.exists(p):
            return p, m["licenca"]
    for lic in ("ofl", "apache", "ufl"):
        try:
            pb = _fetch(RAW.format(lic=lic, dir=_dirname(family), file="METADATA.pb"), timeout).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            continue
        files = re.findall(r'filename:\s*"([^"]+\.ttf)"', pb)
        styles = re.findall(r'style:\s*"(\w+)"', pb)
        weights = [int(w) for w in re.findall(r"weight:\s*(\d+)", pb)]
        cands = [(fn, st, w) for fn, st, w in zip(files, styles + ["normal"] * len(files), weights + [400] * len(files)) if st == "normal"]
        if not cands:
            continue
        var = next((c for c in cands if "[" in c[0]), None)  # variável: um arquivo com todos os pesos
        fn = var[0] if var else max(cands, key=lambda c: c[2])[0]
        os.makedirs(d, exist_ok=True)
        data = _fetch(RAW.format(lic=lic, dir=_dirname(family), file=urllib.request.quote(fn)), 60)
        local = re.sub(r"[^\w.-]", "_", fn)
        open(os.path.join(d, local), "wb").write(data)
        license_file = {"ofl": "OFL.txt", "apache": "LICENSE.txt", "ufl": "UFL.txt"}[lic]
        try:
            open(os.path.join(d, license_file), "wb").write(_fetch(RAW.format(lic=lic, dir=_dirname(family), file=license_file), timeout))
        except Exception:  # noqa: BLE001
            pass
        m = {"familia": family, "arquivo": local, "variavel": bool(var), "licenca": lic.upper(),
             "origem": RAW.format(lic=lic, dir=_dirname(family), file=fn)}
        json.dump(m, open(meta_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return os.path.join(d, local), m["licenca"]
    return None, None


def font_face(family, embed=False):
    """@font-face da família para o compositor. Por padrão aponta para o arquivo no cache local (o Chromium lê do disco:
    bem mais rápido que embutir fontes variáveis grandes em cada medição). embed=True embute (HTML autocontido)."""
    import base64  # noqa: PLC0415
    p, _ = ensure(family)
    if not p:
        return None
    var = json.load(open(os.path.join(os.path.dirname(p), "fonte.json"), encoding="utf-8")).get("variavel")
    src = (f"url(data:font/ttf;base64,{base64.b64encode(open(p, 'rb').read()).decode()})" if embed
           else "url('file:///" + p.replace(os.sep, "/") + "')")
    return f"@font-face{{font-family:'{family}';src:{src};{'font-weight:100 900;' if var else ''}font-display:block}}"
