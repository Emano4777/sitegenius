"""Ajustes sobre uma criação já pronta: entender exatamente o que o cliente pediu e reaproveitar o resto.

- Só texto ("aumenta o título", "tira o subtítulo", "o nome tá pequeno", "preço em vermelho"): refeito na hora
  sobre a mesma arte, sem gerar de novo.
- Só cena ("deixa o fundo mais escuro"): nova arte a partir da anterior (img2img), com força proporcional ao pedido,
  e os textos mantidos como estavam.
- Os dois ("fundo azul e muda o título para X"): nova arte + textos revisados.
Os textos nunca são reescritos pelo modelo: só o que o cliente digitou literalmente entra no plano.
"""
import re

from . import textplan

ROLE_WORDS = [
    ("subtitulo", r"subt[ií]tulo|slogan|frase|legenda|linha de baixo"),
    ("titulo", r"t[ií]tulo|nome|marca|chamada principal|texto principal|texto grande"),
    ("preco", r"pre[cç]o|valor"),
    ("preco_de", r"pre[cç]o antigo|pre[cç]o riscado|de por"),
    ("cta", r"bot[aã]o|chamada"),
    ("contato", r"telefone|whats\w*|contato|site|endere[cç]o"),
    ("descricao", r"descri[cç][aã]o"),
]
TEXT_WORDS = re.compile(r"\b(textos?|t[ií]tulos?|subt[ií]tulos?|pre[cç]os?|nome|frases?|letras?|fontes?|escrit[ao]s?|"
                        r"slogan|bot[aã]o|chamada|tipografia|telefone|contato|legenda)\b", re.IGNORECASE)
SCENE_WORDS = re.compile(r"\b(fundo|cen[aá]rio|imagem|foto|luz|ilumina\w*|c[eé]u|pessoas?|objetos?|produto|ambiente|"
                         r"paisagem|desenho|arte toda|composi[cç][aã]o|cores da imagem|clima|noite|dia|chuva|sol|"
                         r"praia|cidade|mesa|parede|ch[aã]o|roupa|camiseta|casa|carro|estilo da imagem)\b", re.IGNORECASE)
REMOVE = re.compile(r"\b(tira|tire|retira|retire|remove|remova|apaga|apague|exclui|exclua|sem o|sem a)\b", re.IGNORECASE)
BIGGER = re.compile(r"\b(maior|aumenta|aumente|amplia|amplie|mais grande|bem grande|destaque|grande demais n[aã]o)\b|"
                    r"\b(est[aá]|t[aá]|ficou|muito)\s+(pequen\w+|miud\w+)", re.IGNORECASE)
SMALLER = re.compile(r"\b(menor|diminui|diminua|reduz|reduza|mais discreto)\b|\b(est[aá]|t[aá]|ficou|muito)\s+(grande|enorme)",
                     re.IGNORECASE)
SMALL_CHANGE = re.compile(r"mais (escur|clar|quente|fri|vivo|saturad|suave|n[ií]tid|contrast)|menos (escur|clar|saturad)|"
                          r"ilumina|brilho|contraste|tom|cor(es)? (mais|menos)", re.IGNORECASE)
BIG_CHANGE = re.compile(r"troca\w* o (fundo|cen[aá]rio|ambiente)|muda\w* o (fundo|cen[aá]rio|ambiente|estilo)|"
                        r"outro (fundo|cen[aá]rio|lugar|ambiente)|transforma|estilo (anime|aquarela|3d|cartoon|ilustra)",
                        re.IGNORECASE)


def _fold(text):
    return textplan._fold(text)


def targets(text):
    """Papéis citados no pedido ("o título", "o preço"…), na ordem em que aparecem."""
    t = _fold(text)
    found = []
    for role, pattern in ROLE_WORDS:
        m = re.search(_fold(pattern), t)
        if m:
            found.append((m.start(), role))
    roles = [r for _, r in sorted(found)]
    if "preco_de" in roles and "preco" in roles:
        roles.remove("preco")
    return roles


def classify(text, previous_plan):
    """{"texto": bool, "cena": bool}: o que o pedido muda."""
    t = text or ""
    new = textplan.plan_from_request(t)
    has_literal = bool(new and new["blocos"])
    text_change = bool(TEXT_WORDS.search(t)) or has_literal or bool(previous_plan and re.search(
        _fold(r"letras? (dourad|prat|branc|pret|azu|vermelh|amarel|verde|rosa|roxo)|neon|brilho no texto|sombra no texto"),
        _fold(t)))
    # "Mais escuro" sozinho é da cena; "letras mais escuras" é do texto.
    scene = bool(SCENE_WORDS.search(t)) or (bool(SMALL_CHANGE.search(t)) and not TEXT_WORDS.search(t))
    if not previous_plan or not previous_plan.get("blocos"):
        return {"texto": has_literal, "cena": True}
    if not text_change and not scene:
        scene = True  # pedido genérico ("deixa mais bonito"): ajusta a cena, mantém os textos
    return {"texto": text_change, "cena": scene}


def edit_strength(text):
    """Força do img2img: pequena mudança preserva mais a arte; troca de fundo/estilo precisa de mais liberdade."""
    if BIG_CHANGE.search(text or ""):
        return 0.8
    if SMALL_CHANGE.search(text or ""):
        return 0.55
    return 0.65


def revise_plan(previous_plan, text):
    """Plano revisado: textos literais novos, remoções, tamanho, posição, cor, estilo e efeito — só no que foi citado."""
    plan = dict(previous_plan)
    blocks = [dict(b) for b in previous_plan.get("blocos") or []]
    roles = targets(text)

    def chosen():
        picked = [b for b in blocks if b["papel"] in roles or (b["papel"] == "marca" and "titulo" in roles)]
        return picked or blocks

    if REMOVE.search(text):
        quoted = textplan.plan_from_request(text)
        literal = {b["texto"] for b in quoted["blocos"]} if quoted else set()
        gone = [b for b in blocks if b["texto"] in literal or b["papel"] in roles]
        if gone:
            blocks = [b for b in blocks if b not in gone]
            plan["blocos"] = blocks
            return plan

    new = textplan.plan_from_request(text)
    if new:
        for nb in new["blocos"]:
            same = next((b for b in blocks if b["papel"] == nb["papel"]), None)
            if same is None and roles:
                same = next((b for b in blocks if b["papel"] in roles), None)
            if same is None and nb["papel"] in ("titulo", "texto", "marca") and len(blocks) == 1:
                same = blocks[0]
            if same:
                same["texto"] = nb["texto"]
                same.pop("confirmado", None)
                if nb["posicao"] != "auto":
                    same["posicao"] = nb["posicao"]
            else:
                blocks.append({k: nb[k] for k in ("texto", "papel", "posicao")})

    position = textplan.detect_position(re.sub(r"\"[^\"]*\"|“[^”]*”", " ", text))
    if position != "auto" and not (new and any(b["posicao"] != "auto" for b in new["blocos"])):
        for b in chosen():
            b["posicao"] = position
    if BIGGER.search(text) or SMALLER.search(text):
        factor = 1.3 if BIGGER.search(text) else 0.78
        for b in chosen():
            b["escala"] = round(max(0.5, min(2.2, float(b.get("escala", 1.0)) * factor)), 2)
    style = textplan.detect_type_style(text, None)
    if re.search(_fold(r"estilo|fonte|letra|tipografia"), _fold(text)) and style != "moderno":
        plan["estilo"] = style
    effect = textplan.detect_effect(text)
    if effect != "auto":
        plan["efeito"] = effect
    color = textplan.detect_color(text)
    if color:
        plan["cor"] = color
    plan["blocos"] = blocks
    return plan


# ----------------------------------------------------------------------------------- ajuste interpretado pelo modelo

INTENSITY = {"pequena": 0.55, "media": 0.65, "média": 0.65, "grande": 0.8}
OPERATIONS = ("trocar", "adicionar", "remover", "mover", "aumentar", "diminuir", "cor", "estilo", "efeito")


def model_adjustment(data):
    """Normaliza o campo "ajuste" devolvido pelo modelo. None se o modelo disse que é uma criação nova."""
    if not isinstance(data, dict) or data.get("tipo_pedido") != "ajuste":
        return None
    adj = data.get("ajuste") if isinstance(data.get("ajuste"), dict) else {}
    ops = [o for o in (adj.get("operacoes_texto") or []) if isinstance(o, dict) and o.get("acao") in OPERATIONS][:8]
    return {"cena": adj.get("cena") is True, "textos": adj.get("textos") is True or bool(ops),
            "forca": INTENSITY.get(str(adj.get("intensidade") or "").lower()), "operacoes": ops}


def apply_operations(previous_plan, ops, message):
    """Aplica as operações do modelo sobre o plano anterior, conferindo tudo: textos novos só se forem literais
    no pedido; papéis, posições, estilos e efeitos só dos valores conhecidos."""
    plan = dict(previous_plan or {"versao": 1, "blocos": [], "estilo": "moderno", "efeito": "auto", "cor": None})
    blocks = [dict(b) for b in plan.get("blocos") or []]

    def find(op):
        text = textplan._nfc(op.get("alvo") or "")
        role = op.get("papel")
        hit = [b for b in blocks if text and b["texto"] == text] or [b for b in blocks if role and b["papel"] == role]
        if not hit and role == "titulo":
            hit = [b for b in blocks if b["papel"] == "marca"]
        return hit if (hit or op.get("todos")) else hit

    for op in ops:
        action = op["acao"]
        literal = textplan._nfc(op.get("texto") or "")[:textplan.MAX_TEXT]
        role = op.get("papel") if op.get("papel") in textplan.ROLES else None
        position = op.get("posicao") if op.get("posicao") in textplan.POSITIONS else None
        targets_ = find(op) or (blocks if op.get("todos") is True else [])
        if action in ("trocar", "adicionar"):
            if not literal or not textplan._present(literal, message):
                continue  # o modelo não pode inventar texto
            if action == "trocar" and targets_:
                targets_[0]["texto"] = literal
                targets_[0].pop("confirmado", None)
                if position:
                    targets_[0]["posicao"] = position
            else:
                blocks.append({"texto": literal, "papel": role or "texto", "posicao": position or "auto"})
        elif action == "remover":
            blocks = [b for b in blocks if b not in targets_]
        elif action == "mover" and position:
            for b in targets_ or blocks:
                b["posicao"] = position
        elif action in ("aumentar", "diminuir"):
            factor = 1.3 if action == "aumentar" else 0.78
            for b in targets_ or blocks:
                b["escala"] = round(max(0.5, min(2.2, float(b.get("escala", 1.0)) * factor)), 2)
        elif action == "cor":
            color = textplan.detect_color(message) or textplan.detect_color("texto " + str(op.get("valor") or ""))
            if color:
                plan["cor"] = color
        elif action == "estilo" and op.get("valor") in textplan.TYPE_STYLES:
            plan["estilo"] = op["valor"]
        elif action == "efeito" and op.get("valor") in textplan.EFFECTS:
            plan["efeito"] = op["valor"]
    plan["blocos"] = textplan.assign_hierarchy(blocks)
    return plan


REFERENCE_WORDS = re.compile(r"\b(ele|ela|isso|isto|esse|essa|este|esta|dele|dela|nele|nela|aqui|ai|aí|ficou|tá|ta|est[aá]|"
                             r"t[aá] bom|gostei|curti|mais|menos|tamb[eé]m|mas|agora s[oó]|s[oó] que)\b", re.IGNORECASE)


def refers_to_current(text, previous_plan):
    """Sinais de que a mensagem fala da peça atual (e não de uma nova): cita um texto/elemento dela ou faz referência
    a ela, sem pedir para criar e sem trazer um assunto novo longo. Usado só para conferir a decisão do modelo."""
    from .creative import NEW_REQUEST  # noqa: PLC0415

    text = text or ""
    if NEW_REQUEST.search(text):
        return False
    folded = _fold(text)
    mentions_text = any(_fold(b["texto"]) in folded for b in (previous_plan or {}).get("blocos") or [])
    mentions_part = bool(TEXT_WORDS.search(text) or re.search(r"\b(fundo|cor|cores|luz|letra|letras|fonte)\b", folded))
    referential = bool(REFERENCE_WORDS.search(text))
    short = len(text.split()) <= 25
    return (mentions_text or mentions_part or referential) and short
