"""Criação livre de imagens: intenção do pedido, especificação (spec) e validação.

O Studio aceita qualquer imagem permitida (retrato, cena, anúncio, imóvel, logo, arte social…).
Encartes com produtos e preços reais continuam no fluxo de composição determinística (ad_composite).

Regras de segurança do conteúdo gerado:
- Texto dentro da imagem só se o usuário escreveu esse texto (entre aspas). Sem texto, o prompt
  pede "no text" para o modelo não inventar ofertas, preços ou marcas.
- O que o modelo de linguagem propõe passa por normalize_spec(); nada é aceito sem validação.
"""
import json
import math
import os
import random
import re
import unicodedata

# Tamanhos múltiplos de 16 (Z-Image/Flux). "qualidade" fica perto de 0,78 MP (768x1024 testado no
# Acer em ~295 s); "rapido" perto de 0,41 MP. Tempos reais saem das medições (sg_jobs.timings).
ASPECTS = {
    "1:1": {"nome": "Quadrado 1:1", "qualidade": (880, 880), "rapido": (640, 640)},
    "4:5": {"nome": "Retrato 4:5", "qualidade": (768, 960), "rapido": (576, 720)},
    "3:4": {"nome": "Retrato 3:4", "qualidade": (768, 1024), "rapido": (576, 768)},
    "2:3": {"nome": "Retrato 2:3", "qualidade": (720, 1088), "rapido": (528, 784)},
    "9:16": {"nome": "Story 9:16", "qualidade": (656, 1168), "rapido": (480, 848)},
    "16:9": {"nome": "Paisagem 16:9", "qualidade": (1168, 656), "rapido": (848, 480)},
    "3:2": {"nome": "Paisagem 3:2", "qualidade": (1088, 720), "rapido": (784, 528)},
}
DEFAULT_ASPECT = "4:5"
QUALITIES = ("qualidade", "rapido")
ASPECT_RATIOS = {"1:1": 1.0, "4:5": 0.8, "3:4": 0.75, "2:3": 2 / 3, "9:16": 9 / 16, "16:9": 16 / 9, "3:2": 1.5}

# Perfis de geração. Sem SG_RENDER_PROFILES valem a tabela ASPECTS e os passos do workflow do Acer.
# Depois do benchmark, configure no Vercel, ex.:
#   SG_RENDER_PROFILES={"rapido": {"megapixels": 0.4, "steps": 6}, "qualidade": {"megapixels": 0.78, "steps": 9}}
_profiles_cache = {"raw": None, "value": None}


def render_profiles():
    raw = os.getenv("SG_RENDER_PROFILES", "")
    if raw == _profiles_cache["raw"]:
        return _profiles_cache["value"]
    profiles = {q: {"megapixels": None, "steps": None} for q in QUALITIES}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    for quality in QUALITIES:
        item = data.get(quality) if isinstance(data, dict) else None
        if not isinstance(item, dict):
            continue
        mp, steps = item.get("megapixels"), item.get("steps")
        if isinstance(mp, (int, float)) and 0.15 <= mp <= 1.2:
            profiles[quality]["megapixels"] = float(mp)
        if isinstance(steps, int) and 1 <= steps <= 30:
            profiles[quality]["steps"] = steps
    _profiles_cache.update(raw=raw, value=profiles)
    return profiles


def steps_for(quality):
    return render_profiles().get(quality if quality in QUALITIES else "qualidade", {}).get("steps")

STYLES = {
    "auto": ("Automático", ""),
    "foto": ("Foto realista", "photorealistic, natural light, sharp focus, highly detailed, 35mm photo"),
    "cinema": ("Cinematográfico", "cinematic lighting, dramatic composition, film still, shallow depth of field, rich color grading"),
    "ilustracao": ("Ilustração", "detailed digital illustration, clean linework, vibrant colors"),
    "3d": ("3D", "3d render, soft global illumination, physically based materials, highly detailed"),
    "anime": ("Anime", "anime style, cel shading, expressive characters, studio quality"),
    "aquarela": ("Aquarela", "watercolor painting, soft washes, visible paper texture"),
    "minimalista": ("Minimalista", "minimalist, clean composition, generous negative space, flat colors"),
    "produto": ("Foto de produto", "studio product photography, softbox lighting, clean background, commercial quality"),
    "imovel": ("Imobiliário", "professional real estate photography, wide angle, bright natural light, interior design magazine"),
    "logo": ("Logotipo", "flat vector logo design, simple bold shapes, centered, plain background"),
}

# Fidelidade à referência no img2img: quanto menor o denoise, mais a imagem segue a referência.
REFERENCE_STRENGTH = {"baixa": 0.85, "media": 0.7, "alta": 0.5}
EDIT_DENOISE = 0.6
NO_TEXT = "no text, no letters, no watermark, no logo"
NEGATIVE = "blurry, low quality, deformed, distorted, extra fingers, watermark, text artifacts"

_STYLE_WORDS = [
    ("cinema", r"cinemat|filme|epic|épic"),
    ("anime", r"anime|mang[aá]"),
    ("aquarela", r"aquarel"),
    ("3d", r"\b3d\b|render|pixar"),
    ("ilustracao", r"ilustra|desenho|cartoon|quadrinho"),
    ("minimalista", r"minimalis"),
    ("logo", r"\blogo|logotipo|logomarca"),
    ("imovel", r"im[oó]vel|imobili|apartamento|casa [aà] venda|sala de estar|cozinha planejada"),
    ("produto", r"foto de produto|still de produto|packshot"),
    ("foto", r"realista|fotografia|foto real|retrato realista"),
]
_ASPECT_WORDS = [
    ("9:16", r"\bstor(y|ies)\b|\breels?\b|tik ?tok|9[:x/]16|vertical"),
    ("16:9", r"16[:x/]9|horizontal|paisagem|banner|capa (do|de) (youtube|facebook)|widescreen|wallpaper"),
    ("1:1", r"1[:x/]1|quadrad"),
    ("3:4", r"3[:x/]4"),
    ("2:3", r"2[:x/]3|p[oô]ster|cartaz"),
    ("3:2", r"3[:x/]2"),
    ("4:5", r"4[:x/]5|\bfeed\b|post (do|de|para) instagram"),
]
_AD_WORDS = r"encarte|tabloide|panfleto|ofertas? d[aoe]s? (semana|dia|m[eê]s)|lista de (pre[cç]os|ofertas)"
_PRICE = r"r\$\s*\d|\b\d{1,4},\d{2}\b"
_REGENERATE = r"^(outra|mais uma|nova) (vers[aã]o|op[cç][aã]o|varia[cç][aã]o)|^(de novo|novamente|refa[cç]a|regenere|gere outra)"
_EDIT = (
    r"^(agora|mude|muda|troque|troca|deixe|deixa|coloque|coloca|adicione|adiciona|acrescente|remova|remove|tire|tira|"
    r"aumente|diminua|mais |menos |fa[cç]a (ela|ele|a imagem)|mantenha|mesma imagem|na mesma)"
)
_GO_ANYWAY = r"gere? assim mesmo|pode gerar|gera assim|tanto faz|qualquer (um|uma|coisa)|surpreenda|voc[eê] escolhe"
_FILLER = {
    "quero", "queria", "uma", "um", "imagem", "foto", "arte", "gere", "gera", "gerar", "crie", "cria", "criar",
    "faca", "faz", "fazer", "desenhe", "por", "favor", "pf", "pfv", "de", "da", "do", "com", "para", "pra",
    "me", "uma", "algo", "alguma", "coisa", "legal", "bonita", "bonito", "ai", "aí", "ola", "oi", "bom", "dia",
    "boa", "tarde", "noite", "e", "o", "a", "os", "as",
}


def strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFD", text or "") if unicodedata.category(c) != "Mn")


def _norm(text):
    return strip_accents(str(text or "")).lower().strip()


def clean(value, limit):
    text = " ".join(str(value or "").split())
    return text[:limit]


def detect_style(text):
    t = _norm(text)
    for key, pattern in _STYLE_WORDS:
        if re.search(strip_accents(pattern), t):
            return key
    return "auto"


def detect_aspect(text):
    t = _norm(text)
    for key, pattern in _ASPECT_WORDS:
        if re.search(pattern, t):
            return key
    return None


def quoted_texts(text):
    """Textos que o usuário quer DENTRO da imagem: só o que estiver entre aspas."""
    found = re.findall(r"[\"“”']([^\"“”']{1,80})[\"“”']", text or "")
    return [clean(f, 80) for f in found if clean(f, 80)]


def classify(text, has_previous_image=False, conversation_mode=None):
    """Intenção do pedido: free_image | ad_composite | edit | regenerate | go_anyway."""
    t = _norm(text)
    if re.search(_GO_ANYWAY, t):
        return "go_anyway"
    if has_previous_image and re.search(_REGENERATE, t):
        return "regenerate"
    if has_previous_image and re.search(_EDIT, t):
        return "edit"
    # Preço digitado = encarte: o preço só entra pela composição determinística, nunca pelo modelo de imagem.
    if conversation_mode == "ad" or re.search(_AD_WORDS, t) or re.search(_PRICE, t):
        return "ad_composite"
    return "free_image"


def missing_info(text, has_references):
    """Pergunta só o indispensável: o pedido não diz o que deve aparecer na imagem."""
    words = [w for w in re.findall(r"[a-z0-9]+", _norm(text)) if w not in _FILLER and len(w) > 1]
    if words or has_references:
        return None
    return ("O que você quer ver na imagem? Descreva o assunto principal, e se quiser o estilo e o formato. "
            "Ex.: “astronauta numa floresta, luz cinematográfica, formato story”.")


def size_for(aspect, quality):
    aspect = aspect if aspect in ASPECTS else DEFAULT_ASPECT
    quality = quality if quality in QUALITIES else "qualidade"
    mp = render_profiles()[quality]["megapixels"]
    if not mp:
        return list(ASPECTS[aspect][quality])
    ratio = ASPECT_RATIOS[aspect]
    height = math.sqrt(mp * 1_000_000 / ratio)
    # Múltiplos de 16 (latente do Z-Image/Flux), sem passar do orçamento de pixels.
    w, h = int(height * ratio) // 16 * 16, int(height) // 16 * 16
    return [max(256, w), max(256, h)]


def sizes_table():
    """Tamanho e passos de cada formato/qualidade (para a tela mostrar e estimar)."""
    return {q: {"passos": steps_for(q), "tamanhos": {a: size_for(a, q) for a in ASPECTS}} for q in QUALITIES}


def final_prompt(prompt_visual, style, texts_in_image):
    parts = [prompt_visual.strip().rstrip(".")]
    suffix = STYLES.get(style, STYLES["auto"])[1]
    if suffix:
        parts.append(suffix)
    if texts_in_image:
        parts.append("with the exact text " + ", ".join(f"\"{t}\"" for t in texts_in_image) + ", legible typography")
    else:
        parts.append(NO_TEXT)
    parts.append("high quality, detailed")
    return ", ".join(p for p in parts if p)[:1500]


def build_spec(*, prompt_original, prompt_visual, style, aspect, quality, user_texts, reference_ids=(),
               reference_strength="media", init_asset_id=None, denoise=None, seed=None, gen_type="free_image",
               parent_job_id=None, notes=""):
    """Monta a spec validada. Texto na imagem: só o que o usuário digitou entre aspas."""
    style = style if style in STYLES else "auto"
    aspect = aspect if aspect in ASPECTS else DEFAULT_ASPECT
    quality = quality if quality in QUALITIES else "qualidade"
    allowed_texts = []
    for t in user_texts:
        allowed_texts += quoted_texts(t)
    prompt_visual = clean(prompt_visual, 1200) or clean(prompt_original, 1200)
    render_mode, strength = "txt2img", None
    if init_asset_id:
        render_mode = "img2img"
        strength = denoise if denoise is not None else EDIT_DENOISE
    elif reference_ids:
        render_mode = "img2img"
        init_asset_id = reference_ids[0]
        strength = REFERENCE_STRENGTH.get(reference_strength, REFERENCE_STRENGTH["media"])
    return {
        "version": 2,
        "gen_type": gen_type,
        "prompt_original": clean(prompt_original, 2000),
        "prompt_visual": prompt_visual,
        "prompt": final_prompt(prompt_visual, style, allowed_texts),
        "negative_prompt": NEGATIVE,
        "style": style,
        "aspect_ratio": aspect,
        "quality": quality,
        "size": size_for(aspect, quality),
        "steps": steps_for(quality),
        "seed": int(seed) if seed is not None else random.randint(1, 2**31 - 1),
        "render_mode": render_mode,
        "init_asset_id": init_asset_id,
        "denoise": round(float(strength), 2) if strength is not None else 1.0,
        "reference_ids": list(reference_ids),
        "texts_in_image": allowed_texts,
        "parent_job_id": parent_job_id,
        "notes": clean(notes, 300),
    }


def rules_interpretation(text, options, previous_spec, previous_result_id, reference_ids, conversation_mode, user_texts):
    """Interpretação sem modelo de linguagem. Devolve (acao, resposta, spec|None).

    acao: "gerar" | "perguntar" | "encarte".
    """
    options = options or {}
    intent = classify(text, bool(previous_result_id), conversation_mode)
    if intent == "ad_composite":
        return "encarte", "", None
    aspect = options.get("proporcao") if options.get("proporcao") in ASPECTS else None
    quality = options.get("qualidade") if options.get("qualidade") in QUALITIES else "qualidade"
    style = options.get("estilo") if options.get("estilo") in STYLES and options.get("estilo") != "auto" else None
    strength = options.get("fidelidade") if options.get("fidelidade") in REFERENCE_STRENGTH else "media"

    if intent in ("edit", "regenerate", "go_anyway") and previous_spec:
        base = previous_spec
        if intent == "go_anyway" and base.get("pending"):
            prompt_visual = base["prompt_visual"]
        elif intent == "regenerate":
            prompt_visual = base["prompt_visual"]
        else:
            prompt_visual = f"{base['prompt_visual']}, {clean(text, 600)}"
        spec = build_spec(
            prompt_original=text,
            prompt_visual=prompt_visual,
            style=style or detect_style(text) if intent == "edit" and detect_style(text) != "auto" else (style or base.get("style")),
            aspect=aspect or detect_aspect(text) or base.get("aspect_ratio"),
            quality=quality,
            user_texts=user_texts,
            reference_ids=reference_ids,
            reference_strength=strength,
            init_asset_id=previous_result_id if intent == "edit" else None,
            denoise=EDIT_DENOISE if intent == "edit" else None,
            seed=None,
            parent_job_id=base.get("job_id"),
        )
        what = {"edit": "Certo! Vou ajustar a imagem anterior.", "regenerate": "Vou criar outra versão para você.",
                "go_anyway": "Combinado! Vou preparar uma imagem para você."}[intent]
        return "gerar", what, spec

    question = missing_info(text, bool(reference_ids))
    if question:
        pending = {"pending": True, "prompt_visual": clean(text, 600) or "a beautiful, surprising image",
                   "style": style or "auto", "aspect_ratio": aspect or DEFAULT_ASPECT}
        return "perguntar", question + " Se preferir, diga “gere assim mesmo”.", pending

    spec = build_spec(
        prompt_original=text,
        prompt_visual=text,
        style=style or detect_style(text),
        aspect=aspect or detect_aspect(text) or DEFAULT_ASPECT,
        quality=quality,
        user_texts=user_texts,
        reference_ids=reference_ids,
        reference_strength=strength,
    )
    note = " Vou usar a sua foto como base." if reference_ids else ""
    return "gerar", f"Entendi sua ideia! Vou preparar sua imagem.{note}", spec


# Palavras que nunca podem chegar ao cliente numa resposta do modelo de linguagem.
TECH_WORDS = re.compile(
    r"\b(ia|ai|modelo|llm|gemma|qwen|ollama|comfy\w*|z-image|hostgator|vercel|acer|servidor|gpu|vram|cpu|vae|token\w*|"
    r"prompt|seed|api|endpoint|worker|json|inferência|benchmark|cache)\b", re.IGNORECASE)


def friendly_reply(text, fallback):
    """Usa a frase do modelo só se for natural; com qualquer termo técnico, usa a frase padrão."""
    text = clean(text, 600)
    return fallback if not text or TECH_WORDS.search(text) else text


def normalize_model_answer(data, text, options, previous_spec, previous_result_id, reference_ids, user_texts):
    """Valida a resposta JSON do modelo da HostGator. Qualquer campo ruim cai para as regras."""
    if not isinstance(data, dict):
        return rules_interpretation(text, options, previous_spec, previous_result_id, reference_ids, None, user_texts)
    options = options or {}
    acao = data.get("acao")
    resposta = friendly_reply(data.get("resposta"), "")
    if acao == "encarte":
        return "encarte", resposta, None
    if acao == "perguntar" and clean(data.get("pergunta"), 400):
        pending = {"pending": True, "prompt_visual": clean(data.get("prompt_visual"), 1200) or clean(text, 600),
                   "style": data.get("estilo") if data.get("estilo") in STYLES else "auto",
                   "aspect_ratio": data.get("proporcao") if data.get("proporcao") in ASPECTS else DEFAULT_ASPECT}
        question = friendly_reply(data.get("pergunta"), "Pode me contar um pouco mais sobre o que você quer ver na imagem?")
        return "perguntar", question + " Se preferir, diga “gere assim mesmo”.", pending
    prompt_visual = clean(data.get("prompt_visual"), 1200)
    if acao != "gerar" or len(prompt_visual) < 8:
        return rules_interpretation(text, options, previous_spec, previous_result_id, reference_ids, None, user_texts)

    edit = data.get("editar_anterior") is True and previous_result_id
    style = options.get("estilo") if options.get("estilo") in STYLES and options.get("estilo") != "auto" else data.get("estilo")
    aspect = options.get("proporcao") if options.get("proporcao") in ASPECTS else data.get("proporcao")
    strength = options.get("fidelidade") if options.get("fidelidade") in REFERENCE_STRENGTH else "media"
    use_ref = data.get("uso_referencia") if data.get("uso_referencia") in ("base", "estilo", "nenhum") else "base"
    spec = build_spec(
        prompt_original=text,
        prompt_visual=prompt_visual,
        style=style,
        aspect=aspect or (previous_spec or {}).get("aspect_ratio") or DEFAULT_ASPECT,
        quality=options.get("qualidade"),
        user_texts=user_texts,
        # "estilo": a referência entra só pela descrição do modelo de visão, não como base da imagem.
        reference_ids=reference_ids if use_ref == "base" else [],
        reference_strength=strength,
        init_asset_id=previous_result_id if edit else None,
        denoise=EDIT_DENOISE if edit else None,
        parent_job_id=(previous_spec or {}).get("job_id") if edit else None,
        notes=clean(data.get("descricao_referencias"), 300),
    )
    return "gerar", resposta or "Entendi sua ideia! Vou preparar sua imagem.", spec
