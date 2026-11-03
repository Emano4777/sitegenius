"""Resolução pedida em linguagem natural -> tamanho final exato + plano de geração dentro dos limites do modelo/GPU.

"feed do Instagram em 1080 × 1350", "Story 1080x1920", "banner horizontal de 1920 × 1080", "quadrada 1024 × 1024",
"em 4K", "A4"... O resultado final tem SEMPRE as dimensões pedidas. O gerador trabalha numa resolução nativa (área
máxima medida no Acer, múltiplos de 64, mesma proporção); quando o pedido é maior, o cenário é ampliado por um modelo
de super-resolução (não esticado) e os textos são desenhados já no tamanho final (nítidos em qualquer resolução).
"""
import re

MAX_SIDE = 4096          # limite do produto final (4K cheio = 3840 x 2160)
MIN_SIDE = 256
GEN_MAX_PIXELS = 1_100_000   # FLUX.2 Klein 4B Q4 na RTX 4050 6 GB: ~1 MP (1024² em ~33 s, 768x1344 em ~30 s)
GEN_STEP = 64

NAMED = [  # (padrão, (largura, altura)) — só quando o usuário não deu números
    (r"\b(stor(y|ies)|reels?|tik ?tok|status)\b", (1080, 1920)),
    (r"\bfeed\b|\bpost(agem)?\b.*\binstagram\b|\binstagram\b", (1080, 1350)),
    (r"\bquadrad[oa]\b|\b1\s*[:x]\s*1\b", (1080, 1080)),
    (r"\bbanner\b|\bhorizontal\b|\bpaisagem\b|\bcapa\b|\b16\s*[:x]\s*9\b|\byoutube\b|\bwide\b", (1920, 1080)),
    (r"\b9\s*[:x]\s*16\b|\bvertical\b", (1080, 1920)),
    (r"\b4\s*[:x]\s*5\b", (1080, 1350)),
    (r"\ba4\b", (2480, 3508)),
    (r"\ba3\b", (3508, 4961)),
]
SCALES = [(r"\b8k\b", 7680), (r"\b4k\b|\buhd\b|\b2160p\b", 3840), (r"\b2k\b|\b1440p\b|\bqhd\b", 2560),
          (r"\bfull ?hd\b|\b1080p\b|\bfhd\b", 1920), (r"\bhd\b|\b720p\b", 1280)]


def parse(text, default=(1080, 1350)):
    """(largura, altura, motivo). Números explícitos valem mais; depois nomes de formato; depois escala (4K…)."""
    t = (text or "").lower().replace("×", "x").replace("por", "x")
    m = re.search(r"\b(\d{3,5})\s*(?:px)?\s*x\s*(\d{3,5})\s*(?:px|pixels)?\b", t)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        return _clamp(w, h) + ("dimensões pedidas",)
    base, why = None, None
    for pat, size in NAMED:
        if re.search(pat, t):
            base, why = size, "formato pedido"
            break
    for pat, long_side in SCALES:
        if re.search(pat, t):
            w, h = base or ((1920, 1080) if not re.search(r"vertical|story|stories|retrato|reels", t) else (1080, 1920))
            f = long_side / max(w, h)
            return _clamp(round(w * f), round(h * f)) + (f"{'formato pedido e ' if base else ''}escala pedida",)
    if base:
        return base + (why,)
    return tuple(default) + ("escolhida pelo contexto",)


def _clamp(w, h):
    f = min(1.0, MAX_SIDE / max(w, h))
    w, h = max(MIN_SIDE, round(w * f)), max(MIN_SIDE, round(h * f))
    return w, h


def generation_plan(final, max_pixels=GEN_MAX_PIXELS, step=GEN_STEP):
    """Tamanho de geração (mesma proporção, múltiplos de 64, até a área máxima da GPU) e como chegar ao final."""
    W, H = final
    ar = W / H
    gw = (max_pixels * ar) ** 0.5
    gh = gw / ar
    if W * H <= max_pixels:
        gw, gh = W, H
    gw, gh = max(step, int(gw // step) * step), max(step, int(gh // step) * step)
    factor = max(W / gw, H / gh)
    method = "nativo" if factor <= 1.05 else "super_resolucao_2x" if factor <= 2.05 else "super_resolucao_4x"
    return {"final": (W, H), "geracao": (gw, gh), "fator": round(factor, 2), "metodo": method}
