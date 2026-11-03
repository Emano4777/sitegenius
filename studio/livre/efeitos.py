"""Efeitos de texto descritos por PROPRIEDADES (não por nome): o diretor diz espessura e cor dos contornos,
profundidade e direção, camadas de gradiente, sombras, brilho, inclinação, rotação, curva e espaçamento.
Aqui tudo é validado (faixas seguras, cores válidas, número de camadas limitado) e vira CSS.

Os nomes antigos (neon, metal, 3d…) viram RECEITAS de partida com variação pela semente do briefing: dois anúncios
com "brilho" nunca ficam idênticos. A legibilidade continua conferida no desenho final (valida.ink).
Unidades em em (relativas ao tamanho da letra): o mesmo efeito funciona em qualquer tamanho.
"""
import math
import random

from studio import htmlflyer

MAX_FILTERS = 12


def _f(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v)) if math.isfinite(v) else default


def _c(v, default):
    return htmlflyer._hex(v, default)


def _rgba(hx, a):
    h = _c(hx, "#000000")
    return f"rgba({int(h[1:3], 16)},{int(h[3:5], 16)},{int(h[5:7], 16)},{a:.2f})"


def normalize(raw, col="#ffffff", c2="#111111"):
    """Propriedades seguras a partir do que o diretor descreveu (campos ausentes = não usados)."""
    raw = raw if isinstance(raw, dict) else {}
    fill = raw.get("preenchimento") if isinstance(raw.get("preenchimento"), dict) else {}
    tipo = fill.get("tipo") if fill.get("tipo") in ("solido", "gradiente", "metalico") else "solido"
    cores = [_c(x, col) for x in (fill.get("cores") or [col])[:4] if x] or [col]
    out = {"preenchimento": {"tipo": tipo, "cores": cores, "angulo": _f(fill.get("angulo"), 0, 360, 180)}}
    out["contornos"] = [{"espessura": _f(o.get("espessura"), 0.005, 0.12, 0.03), "cor": _c(o.get("cor"), c2)}
                        for o in (raw.get("contornos") or [])[:3] if isinstance(o, dict)]
    out["sombras"] = [{"x": _f(o.get("x"), -0.25, 0.25, 0.0), "y": _f(o.get("y"), -0.25, 0.3, 0.06),
                       "desfoque": _f(o.get("desfoque"), 0, 0.5, 0.1), "cor": _c(o.get("cor"), "#000000"),
                       "opacidade": _f(o.get("opacidade"), 0.05, 1, 0.5)} for o in (raw.get("sombras") or [])[:3] if isinstance(o, dict)]
    p = raw.get("profundidade") if isinstance(raw.get("profundidade"), dict) else {}
    out["profundidade"] = {"camadas": int(_f(p.get("camadas"), 0, 14, 0)), "direcao": _f(p.get("direcao"), 0, 360, 135),
                           "cor": _c(p.get("cor"), c2)} if p else None
    b = raw.get("brilho") if isinstance(raw.get("brilho"), dict) else {}
    out["brilho"] = {"cor": _c(b.get("cor"), col), "intensidade": _f(b.get("intensidade"), 0.1, 1, 0.6)} if b else None
    out["inclinacao"] = _f(raw.get("inclinacao"), -14, 14, 0)
    out["rotacao"] = _f(raw.get("rotacao"), -8, 8, 0)
    out["curva"] = _f(raw.get("curva"), 0, 1, 0)
    out["espacamento"] = _f(raw.get("espacamento"), -0.05, 0.3, 0)
    return out


def preset(name, col, c2, seed=0):
    """Receita de partida para um estilo nomeado, com variação controlada (cor, espessura, ângulo, profundidade)."""
    r = random.Random(seed * 7919 + sum(map(ord, name or "")))
    j = lambda a, b: round(r.uniform(a, b), 3)  # noqa: E731
    recipes = {
        "neon": {"preenchimento": {"cores": ["#fff8f0"]}, "brilho": {"cor": col, "intensidade": j(0.55, 0.95)},
                 "contornos": [{"espessura": j(0.006, 0.014), "cor": col}] if r.random() < 0.5 else []},
        "metal": {"preenchimento": {"tipo": "metalico", "cores": [col], "angulo": j(160, 200)},
                  "sombras": [{"y": j(0.03, 0.07), "desfoque": j(0.05, 0.12), "opacidade": 0.6}],
                  "contornos": [{"espessura": j(0.008, 0.02), "cor": c2}] if r.random() < 0.6 else []},
        "3d": {"preenchimento": {"cores": [col]}, "profundidade": {"camadas": int(j(5, 11)), "direcao": j(110, 160), "cor": c2},
               "sombras": [{"x": j(0.04, 0.09), "y": j(0.06, 0.12), "desfoque": j(0.08, 0.16), "opacidade": 0.45}]},
        "adesivo": {"preenchimento": {"cores": [col]}, "contornos": [{"espessura": j(0.04, 0.07), "cor": c2}],
                    "sombras": [{"y": j(0.04, 0.08), "desfoque": j(0.06, 0.12), "opacidade": 0.45}], "rotacao": j(-3, 3)},
        "gradiente": {"preenchimento": {"tipo": "gradiente", "cores": [col, c2], "angulo": j(150, 210)},
                      "sombras": [{"y": j(0.03, 0.07), "desfoque": j(0.06, 0.12), "opacidade": 0.55}]},
        "contorno": {"preenchimento": {"cores": [col]}, "contornos": [{"espessura": j(0.02, 0.035), "cor": c2},
                                                                      {"espessura": j(0.012, 0.02), "cor": col}]},
        "italico": {"preenchimento": {"cores": [col]}, "inclinacao": j(-12, -7),
                    "sombras": [{"x": j(-0.07, -0.03), "y": j(0.03, 0.07), "desfoque": 0, "cor": c2, "opacidade": 1}]},
        "sombra": {"preenchimento": {"cores": [col]}, "sombras": [{"y": j(0.04, 0.08), "desfoque": j(0.1, 0.2), "opacidade": j(0.45, 0.7)}]},
        "arco": {"preenchimento": {"cores": [col]}, "contornos": [{"espessura": 0.03, "cor": c2}], "curva": j(0.5, 0.9)},
    }
    return normalize(recipes.get(name, recipes["sombra"]), col, c2)


def css(fx, hpx):
    """CSS do efeito para um texto HTML (letra ~ hpx*0.86 px). Gradiente e metal usam background-clip; contornos,
    profundidade, sombras e brilho vão numa cadeia de drop-shadow (funciona com qualquer preenchimento)."""
    size = hpx * 0.86
    fill = fx["preenchimento"]
    parts = []
    cores = fill["cores"]
    if fill["tipo"] == "gradiente" and len(cores) >= 2:
        stops = ",".join(cores)
        parts.append(f"background:linear-gradient({fill['angulo']:.0f}deg,{stops});-webkit-background-clip:text;"
                     "background-clip:text;color:transparent;")
    elif fill["tipo"] == "metalico":
        c = cores[0]
        parts.append(f"background:linear-gradient({fill['angulo']:.0f}deg,#ffffff 0%,{c} 42%,{_shade(c, .35)} 52%,{c} 78%,#ffffff 100%);"
                     "-webkit-background-clip:text;background-clip:text;color:transparent;")
    else:
        parts.append(f"color:{cores[0]};")
    filters = []
    for o in fx["contornos"]:  # contorno: 4 cópias sem desfoque nas diagonais (anel)
        w = max(1.0, o["espessura"] * size)
        filters += [f"drop-shadow({w:.1f}px 0 0 {o['cor']})", f"drop-shadow(-{w:.1f}px 0 0 {o['cor']})",
                    f"drop-shadow(0 {w:.1f}px 0 {o['cor']})", f"drop-shadow(0 -{w:.1f}px 0 {o['cor']})"]
    p = fx.get("profundidade")
    if p and p["camadas"]:
        step = max(1.0, size * 0.012)
        dx, dy = math.cos(math.radians(p["direcao"])) * step, math.sin(math.radians(p["direcao"])) * step
        for _ in range(min(p["camadas"], 6)):  # cadeia: cada cópia empurra a anterior (extrusão)
            filters.append(f"drop-shadow({dx:.1f}px {dy:.1f}px 0 {p['cor']})")
    for s in fx["sombras"]:
        filters.append(f"drop-shadow({s['x'] * size:.1f}px {s['y'] * size:.1f}px {s['desfoque'] * size:.1f}px {_rgba(s['cor'], s['opacidade'])})")
    b = fx.get("brilho")
    if b:
        filters += [f"drop-shadow(0 0 {size * 0.04 * b['intensidade']:.1f}px {b['cor']})",
                    f"drop-shadow(0 0 {size * 0.12 * b['intensidade']:.1f}px {b['cor']})"]
    if filters:
        parts.append("filter:" + " ".join(filters[:MAX_FILTERS]) + ";")
    tr = []
    if fx["inclinacao"]:
        tr.append(f"skewX({fx['inclinacao']:.1f}deg)")
    if fx["rotacao"]:
        tr.append(f"rotate({fx['rotacao']:.1f}deg)")
    if tr:
        parts.append("transform:" + " ".join(tr) + ";")
    if fx["espacamento"]:
        parts.append(f"letter-spacing:{fx['espacamento']:.3f}em;")
    return "".join(parts)


def _shade(hx, f):
    h = _c(hx, "#888888")
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(int(h[i:i + 2], 16) * f))) for i in (1, 3, 5))
