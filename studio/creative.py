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

from . import edits, textplan

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
# O modelo de imagem nunca desenha letras: os textos pedidos são aplicados depois (studio/typography.py).
NO_TEXT = "no text, no lettering, no signage, no logos, no watermark, clean blank surfaces"
NO_PEOPLE = "no people"
QUALITY = "professional art direction, balanced composition, harmonious color palette, refined lighting, sharp details"
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
    ("9:16", r"\bstor(y|ies)\b|\breels?\b|tik ?tok|9[:x/]16"),
    ("1:1", r"1[:x/]1|quadrad"),
    ("4:5", r"4[:x/]5|\bfeed\b|post (do|de|para) instagram|instagram"),
    ("9:16", r"vertical"),
    ("16:9", r"16[:x/]9|horizontal|paisagem|banner|capa (do|de) (youtube|facebook)|widescreen|wallpaper"),
    ("3:4", r"3[:x/]4"),
    ("2:3", r"2[:x/]3|p[oô]ster|cartaz"),
    ("3:2", r"3[:x/]2"),
    ("4:5", r"4[:x/]5|\bfeed\b|post (do|de|para) instagram"),
]
_AD_WORDS = r"encarte|tabloide|panfleto|ofertas? d[aoe]s? (semana|dia|m[eê]s)|lista de (pre[cç]os|ofertas)"
_PRICE = r"r\$\s*\d|\b\d{1,4},\d{2}\b"
# Peça única (anúncio, post, banner) com até 2 preços: arte livre + preço desenhado pela tipografia.
_SINGLE_PIECE = (r"anuncio|promoc|promo\b|oferta|\bpost\b|banner|\barte\b|imagem|story|stories|criativo|propaganda|campanha|\bad\b|meta ads|"
                 r"instagram|cartaz")
_PEOPLE = (r"pessoa|homem|mulher|menin|crian|garot|\bmodelo\b|cliente|familia|casal|executiv|medic|atleta|retrato|selfie|"
           r"rosto|personagem|astronaut|\bchef\b|vendedor|corretor|noiv|\bbebe|idos|jove|\bgente\b|equipe|"
           r"people|person|\bman\b|woman|girl|\bboy\b|child|portrait|\bface\b|character")
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
    if conversation_mode == "ad" or re.search(strip_accents(_AD_WORDS), t):
        return "ad_composite"
    prices = [m.start() for m in re.finditer(_PRICE, t)]
    # Preço "solto" depois do nome do produto (ex.: "Guaraná 2L 7,99") é lista de produtos: encarte.
    listed = any(not re.search(r"(r\$\s*|preco\W{0,3}|por\s|apenas\s|so\s|de\s)$", t[max(0, i - 12):i]) for i in prices)
    if prices and (len(prices) > 2 or listed or not re.search(_SINGLE_PIECE, t)):
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


NEW_REQUEST = re.compile(r"^\s*(?:(?:e|agora|então|entao|beleza|ok|show|legal|valeu)[,!.]?\s+)*(crie|cria|criar|fa[cç]a|faz|fazer|gere|gera|quero uma?|desenhe|preciso de uma?|"
                         r"me (d[eê]|faz|faça)|nova (arte|imagem)|quero (uma|um) (nova|novo|outra|outro|arte|imagem|post|banner|logo|cartaz|foto))",
                         re.IGNORECASE)
_TEXT_CLAUSE = re.compile(
    r"(\bwith|\bfeaturing|\bshowing|\bdisplaying|\band)?\s*(the\s+|a\s+|an\s+|bold\s+|large\s+|big\s+)*"
    r"\b(text|words?|letters?|lettering|typography|font|title|headline|caption|slogan|tagline|brand name|company name|"
    r"logo(type)?s?|label|price( tag)?s?|signage|sign|written|inscription|watermark)\b[^,;.]*", re.IGNORECASE)
_AD_NOUNS = re.compile(r"\b(advertisement|advert|ad|poster|flyer|banner|billboard|social media post|instagram post|"
                       r"an[uú]ncio|cartaz|panfleto|post)\b", re.IGNORECASE)
_PT_TEXT_LEADS = re.compile(r"\b(com o nome|com a marca|com o texto|com a frase|com os dizeres|com o t[ií]tulo|escrit[oa]|"
                            r"subt[ií]tulo|t[ií]tulo|slogan|pre[cç]o|bot[aã]o|chamada|contato|texto|nome)\b\s*:?", re.IGNORECASE)


def art_prompt_visual(prompt_visual, plan_blocks=()):
    """Descrição da arte sem nenhum texto: remove os textos pedidos, preços e menções a letras/logos/cartazes."""
    text = str(prompt_visual or "")
    for b in plan_blocks:
        text = re.sub(re.escape(b["texto"]), " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[\"“”«»][^\"“”«»]*[\"“”«»]", " ", text)
    text = re.sub(r"R\$\s*[\d.,]+|\b\d{1,4},\d{2}\b(\s*reais)?", " ", text, flags=re.IGNORECASE)
    text = _TEXT_CLAUSE.sub("", text)
    text = _PT_TEXT_LEADS.sub(" ", text)
    text = _AD_NOUNS.sub("commercial key visual", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"([,;:]\s*){2,}", ", ", text).strip(" ,.;:")
    return text


SURFACE_HINTS = {
    "parede": "a large smooth plain wall clearly visible, completely blank, evenly lit, nothing hanging on it",
    "fachada": "a storefront facade with a large blank plain sign band above the entrance, clearly visible",
    "letreiro": "a large blank plain rectangular sign board, clearly visible, evenly lit",
    "placa": "a large blank plain rectangular sign board, clearly visible, evenly lit",
    "outdoor": "a large blank white billboard panel, clearly visible",
    "quadro": "a large blank plain board, clearly visible",
    "tela": "a large blank plain screen, clearly visible",
    "muro": "a long smooth plain wall, completely blank, clearly visible",
    "vitrine": "a shop window with a large blank plain glass area",
    "camiseta": "a plain blank t-shirt seen from the front, centered, no print, no graphics, against a plain solid background in a clearly contrasting color",
    "camisa": "a plain blank t-shirt seen from the front, centered, no print, no graphics, against a plain solid background in a clearly contrasting color",
    "caneca": "a plain blank mug seen from the side, centered, no print, no graphics, against a plain solid background in a clearly contrasting color",
    "embalagem": "a plain blank package box, front facing, no print",
    "caixa": "a plain blank box, front facing, no print",
}


def surface_hint(plan):
    """Com texto em superfície, a arte precisa trazer essa superfície lisa e visível (sem letras)."""
    return SURFACE_HINTS.get((plan or {}).get("superficie") or "")


def text_question(spec):
    """Pedido de texto sem o conteúdo: pergunta em vez de inventar. Devolve (resposta, pending) ou None."""
    pending = ((spec or {}).get("text_plan") or {}).get("pendentes") or []
    if not pending:
        return None
    item = pending[0]
    what = f"{item['tipo']} de {item['tema']}" if item.get("tema") else item["tipo"]
    question = f"Qual {what} você quer na arte? Escreva o texto exato."
    if item.get("sugestao"):
        question += f" Se quiser, posso usar “{item['sugestao']}”: é só responder “pode usar”."
    return question, {**spec, "pending": True, "texto_pedido": item}


def resolve_text_answer(text, pending_spec):
    """Resposta à pergunta de texto: "pode usar" (sugestão aprovada) ou a frase digitada (literal)."""
    item = pending_spec.get("texto_pedido") or {}
    answer = clean(text, textplan.MAX_TEXT)
    if re.search(r"^(pode usar|pode ser|use essa|usa essa|ok|sim|beleza|perfeito|isso)\b", _norm(answer)) and item.get("sugestao"):
        value = item["sugestao"]
    else:
        value = re.sub(r"^[\"“”']|[\"“”']$", "", answer).strip()
        value = re.sub(r"^(a frase( é)?|o texto( é)?|use|coloca|coloque|quero)\s*:?\s*", "", value, flags=re.IGNORECASE).strip()
    if not value:
        return None
    base = {k: v for k, v in pending_spec.items() if k not in ("pending", "texto_pedido")}
    spec = build_spec(
        prompt_original=base.get("prompt_original", ""), prompt_visual=base.get("prompt_visual", ""),
        style=base.get("style"), aspect=base.get("aspect_ratio"), quality=base.get("quality"), user_texts=[text],
        reference_ids=base.get("reference_ids") or [], init_asset_id=base.get("init_asset_id"),
        denoise=base.get("denoise") if base.get("init_asset_id") else None,
        extra_blocks=[{"texto": value, "papel": item.get("papel", "subtitulo"), "posicao": item.get("posicao", "auto"),
                       "confirmado": True}],
        previous_plan=base.get("text_plan"),
    )
    spec["text_plan"]["pendentes"] = []
    return spec


def wants_people(original, has_references=False):
    """Pessoas só quando o pedido fala de pessoas (ou há foto de referência)."""
    return has_references or bool(re.search(_PEOPLE, _norm(original)))


def final_prompt(prompt_visual, style, blocks=(), people_ok=True, hint=""):
    """Prompt da arte. Os textos NUNCA vão para o modelo: o espaço deles é reservado pela composição."""
    parts = [art_prompt_visual(prompt_visual, blocks) or "a beautiful, detailed scene"]
    suffix = STYLES.get(style, STYLES["auto"])[1]
    if suffix:
        parts.append(suffix)
    if hint:
        parts.append(hint)
    parts.append(QUALITY)
    parts.append(NO_TEXT)
    if not people_ok:
        parts.append(NO_PEOPLE)
    return ", ".join(p for p in parts if p)[:1500]


def build_spec(*, prompt_original, prompt_visual, style, aspect, quality, user_texts, reference_ids=(),
               reference_strength="media", init_asset_id=None, denoise=None, seed=None, gen_type="free_image",
               parent_job_id=None, notes="", model_texts=None, model_type_style=None, model_effect=None,
               previous_plan=None, extra_blocks=None, revise_previous=False, text_ops=None, fixed_plan=None):
    """Monta a spec validada.

    Textos na imagem (text_plan): extraídos literalmente do pedido atual (studio/textplan.py) e aplicados depois
    sobre a arte. Sem textos novos num ajuste/nova versão, o plano anterior (revalidado) é mantido.
    """
    style = style if style in STYLES else "auto"
    aspect = aspect if aspect in ASPECTS else DEFAULT_ASPECT
    quality = quality if quality in QUALITIES else "qualidade"
    if fixed_plan is not None:
        plan = fixed_plan  # já revisado e conferido pelo planejador de ajustes
    elif revise_previous and previous_plan and previous_plan.get("blocos"):
        # Ajuste: mantém os textos anteriores e muda só o que o cliente pediu (literal, remoção, tamanho, posição…).
        revised = edits.apply_operations(previous_plan, text_ops, prompt_original) if text_ops             else edits.revise_plan(previous_plan, prompt_original)
        # Fontes: pedidos do cliente + textos da peça anterior (já validados quando ela foi criada).
        plan = textplan.validate_plan(revised, list(user_texts) + [prompt_original] + textplan.plan_texts(previous_plan))
    else:
        plan = textplan.plan_from_request(prompt_original, model_texts, style, model_type_style, model_effect)
    if plan is None and previous_plan and not revise_previous and fixed_plan is None:
        plan = textplan.validate_plan(previous_plan, list(user_texts) + [prompt_original])
    if extra_blocks:  # textos confirmados pelo usuário numa pergunta ("pode usar" / frase digitada)
        plan = plan or {"versao": 1, "blocos": [], "estilo": textplan.detect_type_style(prompt_original, style),
                        "efeito": "auto", "cor": None, "superficie": None, "destaque": None}
        plan = {**plan, "blocos": textplan.assign_hierarchy(plan["blocos"] + list(extra_blocks)), "pendentes": []}
    blocks = textplan.resolve_zones(plan, ASPECT_RATIOS[aspect]) if plan else []
    allowed_texts = textplan.plan_texts(plan)
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
        "prompt": final_prompt(prompt_visual, style, [] if not plan else plan["blocos"],
                               people_ok=wants_people(prompt_original, bool(reference_ids or init_asset_id)),
                               hint=surface_hint(plan) or textplan.composition_hint(blocks)),
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
        "text_plan": plan,
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
            denoise=edits.edit_strength(text) if intent == "edit" else None,
            revise_previous=intent in ("edit", "regenerate"),
            seed=None,
            parent_job_id=base.get("job_id"),
            previous_plan=base.get("text_plan") or textplan.plan_from_request(base.get("prompt_original", "")),
        )
        what = {"edit": "Certo! Vou ajustar a imagem anterior.", "regenerate": "Vou criar outra versão para você.",
                "go_anyway": "Combinado! Vou preparar uma imagem para você."}[intent]
        return "gerar", what, spec

    question = missing_info(text, bool(reference_ids))
    if question:
        pending = {"pending": True, "prompt_original": clean(text, 2000),
                   "prompt_visual": clean(text, 600) or "a beautiful, surprising image",
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
    asked = text_question(spec)
    if asked:
        return "perguntar", asked[0], asked[1]
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


REALISTIC_PROMPT = ("photorealistic photograph of a real-world scene, natural materials and real objects, "
                    "realistic lighting, no abstract graphics, no digital art")


def describe_changes(decision):
    """Frase honesta: o que foi conferido, o que vai ser feito na cena e o que não deu para fazer."""
    parts = []
    if decision["feitas"]:
        parts.append("; ".join(decision["feitas"]))
    if decision["cena"]:
        parts.append(edits.scene_label(decision["cena"], decision["realista"]))
    text = ""
    if parts:
        text = "Alterações: " + "; ".join(parts) + "."
    if decision["faltando"]:
        text += " Não consegui fazer: " + "; ".join(decision["faltando"]) + ". Pode me dizer de outro jeito?"
    return text.strip()


def adjustment_outcome(decision, previous_spec, previous_result_id, prompt_visual, data, options, text, user_texts):
    """Executa a decisão: só texto (mesma arte), só cena (textos preservados) ou os dois."""
    previous_spec = previous_spec or {}
    record = {"texto": clean(text, 600), "cena": decision["cena"], "esperado": decision["esperado"],
              "realista": decision["realista"], "arte_anterior": previous_result_id}
    if decision["reclamacao"] and previous_spec.get("pedido_ajuste"):
        record["texto"] = previous_spec["pedido_ajuste"].get("texto") or record["texto"]
    if not decision["cena"]:
        if not decision["feitas"] and edits.plan_signature(decision["plan"]) == edits.plan_signature(
                previous_spec.get("text_plan")):
            if decision["faltando"]:
                return "perguntar", describe_changes(decision), {**previous_spec}
            return ("perguntar", "Não consegui identificar o que mudar. Pode me dizer exatamente o que quer alterar "
                                 "(texto, tamanho, cor, posição ou o fundo)?", {**previous_spec})
        return "ajustar_texto", "Pronto! " + describe_changes(decision), {"text_plan": decision["plan"],
                                                                          "pedido_ajuste": record}
    level = decision["cena"]
    visual = prompt_visual or previous_spec.get("prompt_visual") or ""
    if decision["realista"]:
        visual = f"{visual}, {REALISTIC_PROMPT}"
    style = "foto" if decision["realista"] else (options.get("estilo") if options.get("estilo") in STYLES
                                                 and options.get("estilo") != "auto" else
                                                 data.get("estilo") or previous_spec.get("style"))
    fresh = level == "total"  # mudança substancial: cenário novo (txt2img); os textos são reaplicados depois
    spec = build_spec(
        prompt_original=text, prompt_visual=visual, style=style,
        aspect=(options.get("proporcao") if options.get("proporcao") in ASPECTS else None)
        or previous_spec.get("aspect_ratio") or DEFAULT_ASPECT,
        quality=options.get("qualidade"), user_texts=user_texts,
        init_asset_id=None if fresh else previous_result_id,
        denoise=None if fresh else edits.LEVEL_STRENGTH[level],
        parent_job_id=previous_spec.get("job_id"), fixed_plan=decision["plan"],
        notes=clean(data.get("descricao_referencias"), 300),
    )
    spec["pedido_ajuste"] = record
    return "gerar", "Certo! Vou fazer agora. " + describe_changes(decision), spec


def retry_last_adjustment(previous_spec, text, previous_result_id, user_texts, options=None):
    """ "Você não mudou nada": recupera o último pedido, confere o que falta e refaz com mais força na cena."""
    last = (previous_spec or {}).get("pedido_ajuste") or {}
    if not last.get("texto"):
        return None
    model_like = {"ajuste": {"cena": bool(last.get("cena")), "intensidade": last.get("cena") or ""}}
    decision = edits.plan_adjustment(previous_spec, f"{last['texto']} {text}", model_like, list(user_texts))
    decision["reclamacao"] = True
    if last.get("cena"):
        decision["cena"] = edits.scene_level("", None, True, last["cena"])
    acao, resposta, spec = adjustment_outcome(decision, previous_spec, previous_result_id, "", {}, options or {},
                                              last["texto"], user_texts)
    already = [e["texto"] for e in last.get("esperado") or [] if e.get("texto") and
               e["texto"] in textplan.plan_texts((previous_spec or {}).get("text_plan"))]
    intro = "Você tem razão, vou refazer. "
    if already and not decision["feitas"]:
        intro += "O texto já está como “" + "”, “".join(already) + "”. "
    return acao, intro + resposta.replace("Certo! Vou fazer agora. ", "").replace("Pronto! ", ""), spec


def _same_scene(a, b):
    """A descrição da cena é (quase) a mesma? Compara as palavras, sem pontuação."""
    wa, wb = set(re.findall(r"[a-z]{3,}", _norm(a))), set(re.findall(r"[a-z]{3,}", _norm(b)))
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.85


def normalize_model_answer(data, text, options, previous_spec, previous_result_id, reference_ids, user_texts):
    """Valida a resposta JSON do modelo da HostGator. Qualquer campo ruim cai para as regras."""
    if not isinstance(data, dict):
        return rules_interpretation(text, options, previous_spec, previous_result_id, reference_ids, None, user_texts)
    options = options or {}
    acao = data.get("acao")
    # O modelo às vezes usa sinônimos ("ajustar", "editar"): o que vale é o conteúdo, não a palavra.
    if acao in ("ajustar", "ajuste", "editar", "alterar", "modificar", "criar", "nova") or             (acao not in ("perguntar", "encarte") and data.get("tipo_pedido") in ("ajuste", "nova")):
        acao = "gerar"
    resposta = friendly_reply(data.get("resposta"), "")
    if acao == "encarte" and classify(text, bool(previous_result_id)) == "ad_composite":
        return "encarte", resposta, None
    if acao == "encarte":
        # Peça única com preço (anúncio, post): arte livre com o preço aplicado pela tipografia.
        if len(clean(data.get("prompt_visual"), 1200)) < 8:
            return rules_interpretation(text, options, previous_spec, previous_result_id, reference_ids, None, user_texts)
        acao, resposta = "gerar", ""
    if acao == "perguntar" and clean(data.get("pergunta"), 400):
        pending = {"pending": True, "prompt_original": clean(text, 2000),
                   "prompt_visual": clean(data.get("prompt_visual"), 1200) or clean(text, 600),
                   "style": data.get("estilo") if data.get("estilo") in STYLES else "auto",
                   "aspect_ratio": data.get("proporcao") if data.get("proporcao") in ASPECTS else DEFAULT_ASPECT}
        question = friendly_reply(data.get("pergunta"), "Pode me contar um pouco mais sobre o que você quer ver na imagem?")
        return "perguntar", question + " Se preferir, diga “gere assim mesmo”.", pending
    prompt_visual = clean(data.get("prompt_visual"), 1200)
    if len(prompt_visual) < 8 and previous_result_id and data.get("tipo_pedido") == "ajuste":
        prompt_visual = clean((previous_spec or {}).get("prompt_visual"), 1200)  # ajuste só de texto
    if acao != "gerar" or len(prompt_visual) < 8:
        return rules_interpretation(text, options, previous_spec, previous_result_id, reference_ids, None, user_texts)

    # O modelo interpreta a intenção: criação nova ou ajuste (cena, textos ou os dois). O servidor valida, aplica e
    # confere cada mudança pedida antes de responder.
    adj = edits.model_adjustment(data) if previous_result_id and previous_spec else None
    if adj is not None or (previous_result_id and previous_spec and edits.COMPLAINT.search(text or "")):
        decision = edits.plan_adjustment(previous_spec, text, data, list(user_texts))
        return adjustment_outcome(decision, previous_spec, previous_result_id, prompt_visual, data, options, text,
                                  user_texts)
    previous_plan = (previous_spec or {}).get("text_plan")
    if adj and adj["cena"] and adj["textos"] and _same_scene(prompt_visual, (previous_spec or {}).get("prompt_visual")):
        adj["cena"] = False  # a descrição da cena não mudou: só os textos mudam, a arte é reaproveitada
    if adj is not None and not adj["cena"] and previous_plan and previous_plan.get("blocos"):
        plan = edits.apply_operations(previous_plan, adj["operacoes"], text) if adj["operacoes"]             else edits.revise_plan(previous_plan, text)
        plan = textplan.validate_plan(plan, list(user_texts) + [text] + textplan.plan_texts(previous_plan))
        if plan:
            return "ajustar_texto", resposta or "Pronto! Ajustei o texto na mesma arte.", {"text_plan": plan}
    if adj is not None:
        edit = previous_result_id if adj["cena"] else None
        revise = True
    elif "tipo_pedido" in data:
        edit, revise = None, False  # o modelo disse que é uma criação nova
    else:  # intérprete antigo (sem tipo_pedido)
        edit = data.get("editar_anterior") is True and previous_result_id
        revise = bool(edit) or bool(previous_result_id and previous_plan and not NEW_REQUEST.search(text or ""))
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
        denoise=((adj or {}).get("forca") or edits.edit_strength(text)) if edit else None,
        # Continuação da mesma peça: os textos anteriores são mantidos e só revisados.
        revise_previous=revise,
        text_ops=(adj or {}).get("operacoes") if adj and adj["textos"] else None,
        parent_job_id=(previous_spec or {}).get("job_id") if edit else None,
        notes=clean(data.get("descricao_referencias"), 300),
        model_texts=data.get("textos"),
        model_type_style=data.get("estilo_texto"),
        model_effect=data.get("efeito_texto"),
        previous_plan=previous_plan if revise else None,
    )
    asked = text_question(spec)
    if asked:
        return "perguntar", asked[0], asked[1]
    return "gerar", resposta or "Entendi sua ideia! Vou preparar sua imagem.", spec
