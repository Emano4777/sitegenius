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
                    r"\b(est[aá]|t[aá]|ficou|muito)\s+(\w+\s+){0,2}(pequen\w*|miud\w*)", re.IGNORECASE)
SMALLER = re.compile(r"\b(menor|diminui|diminua|reduz|reduza|mais discreto)\b|\b(est[aá]|t[aá]|ficou|muito)\s+(\w+\s+){0,2}(grande|enorme)",
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
            formatting = op.get("formato") or op.get("polido") or op.get("origem") in ("maiusculas", "mensagem")
            if not literal or not (formatting or textplan._present(literal, message)):
                continue  # o modelo não pode inventar texto (só mudar a forma do texto que o cliente escreveu)
            if action == "trocar" and targets_:
                targets_[0]["texto"] = literal  # substitui no mesmo bloco: posição, papel e estilo preservados
                targets_[0].pop("confirmado", None)
                if position:
                    targets_[0]["posicao"] = position
            elif action == "trocar":
                continue  # sem saber qual texto trocar: não adiciona por engano (pergunta ao cliente)
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


# ----------------------------------------------------------------------------------- confiabilidade dos ajustes
# O que o cliente pediu vira uma lista de MUDANÇAS ESPERADAS; depois de aplicar, cada uma é conferida no estado
# final (e, na cena, na imagem). A resposta ao cliente só afirma o que foi conferido.

ACTION_ALIASES = {"mudar": "trocar", "alterar": "trocar", "substituir": "trocar", "corrigir": "trocar", "editar": "trocar",
                  "reescrever": "trocar", "maiuscula": "trocar", "maiúscula": "trocar", "minuscula": "trocar",
                  "minúscula": "trocar", "capitalizar": "trocar", "excluir": "remover", "apagar": "remover",
                  "tirar": "remover", "incluir": "adicionar", "colocar": "adicionar", "inserir": "adicionar",
                  "ampliar": "aumentar", "reduzir": "diminuir", "colorir": "cor", "posicionar": "mover"}

SCENE_TOTAL = re.compile(
    r"realist|fotograf|foto real|menos artificial|mais natural|completamente|totalmente|outra proposta|outra ideia|"
    r"muda bastante|mude bastante|bem diferente|nada a ver|do zero|refa[zç]\w* tudo|outro estilo|outro (fundo|cen[aá]rio)|"
    r"n[aã]o gostei d[oa] (fundo|cen[aá]rio|arte|imagem|estilo)|troc\w* (o |de )?(fundo|cen[aá]rio)|"
    r"mud\w* (o |de )?(fundo|cen[aá]rio)|n[aã]o gostei|arte totalmente|abstrat", re.IGNORECASE)
REALISTIC = re.compile(r"realist|fotograf|foto real|menos artificial|mais natural|n[aã]o (quero|seja) (arte )?abstrat",
                       re.IGNORECASE)
COMPLAINT = re.compile(
    r"n[aã]o mudou|nada mudou|n[aã]o (alterou|trocou|fez|funcionou|mudou nada)|continua (igual|errad\w*|a mesma|o mesmo|"
    r"min[uú]scul\w*|mai[uú]scul\w*)|t[aá] igual|est[aá] igual|ficou igual|ainda (est[aá]|t[aá]|continua)|n[aã]o era isso|"
    r"mesma coisa|n[aã]o (deu|saiu) certo|errad\w* de novo|de novo errad|voc[eê] n[aã]o|n[aã]o atendeu", re.IGNORECASE)
LEVELS = ("pequena", "media", "grande", "total")
# Força do img2img por nível de mudança da cena. Medido em produção (logo, 02/11/2026): 0,55-0,65 mal muda um fundo
# liso; pedido "médio" precisa de liberdade real.
LEVEL_STRENGTH = {"pequena": 0.6, "media": 0.8, "grande": 0.9, "total": 1.0}


def normalize_ops(ops):
    out = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        action = ACTION_ALIASES.get(str(op.get("acao") or "").lower(), str(op.get("acao") or "").lower())
        if action in OPERATIONS:
            out.append({**op, "acao": action})
    return out[:8]


def _same_letters(a, b):
    """Mesmo texto a menos de maiúsculas, acentos e pontuação (mudança de forma, não de conteúdo)."""
    from .ocr import fold  # noqa: PLC0415

    return bool(a and b) and fold(a) == fold(b)


def _target_block(blocks, message, word=None):
    folded = _fold(message)
    if word:
        hit = [b for b in blocks if _fold(word) in _fold(b["texto"])]
        if hit:
            return hit[0]
    hit = [b for b in blocks if _fold(b["texto"]) in folded]
    if hit:
        return hit[0]
    roles = targets(message)
    hit = [b for b in blocks if b["papel"] in roles]
    return hit[0] if hit else (blocks[0] if len(blocks) == 1 else None)


def _resolve_target(blocks, op, message):
    """Bloco a alterar: o citado pelo modelo (mesmo com outra grafia), o do papel citado, ou o único texto."""
    alvo = textplan._nfc(op.get("alvo") or "")
    if alvo:
        exact = [b for b in blocks if b["texto"] == alvo] or [b for b in blocks if _same_letters(b["texto"], alvo)]
        if exact:
            return exact[0]
    role = op.get("papel")
    same_role = [b for b in blocks if role and (b["papel"] == role or (role == "titulo" and b["papel"] == "marca"))]
    if len(same_role) == 1:
        return same_role[0]
    mentioned = targets(message)
    by_mention = [b for b in blocks if b["papel"] in mentioned]
    if len(by_mention) == 1:
        return by_mention[0]
    if len(blocks) == 1:
        return blocks[0]
    return None


_REPLACE = re.compile(
    r"\b(?:muda|mude|mudar|troca|troque|trocar|altera|altere|alterar|substitui|substitua|corrige|corrija|deixa|deixe|"
    r"coloca|coloque|p[oõ]e|ponha|escreve|escreva|bota|bote)\s+(?:s[oó]\s+|apenas\s+|agora\s+)?(?:o|a)?\s*"
    r"(?P<what>texto|nome|t[ií]tulo|frase|slogan|subt[ií]tulo|pre[cç]o|valor|chamada|bot[aã]o|telefone|marca)"
    r"(?:\s+(?:principal|de cima|de baixo))?\s+(?:para|pra|por|como|com)\s+(?:o\s+|a\s+)?(?P<value>.+?)"
    r"(?=\s*(?:[,;.!?\n]|\s+e\s+(?:troca|troque|muda|mude|coloca|coloque|deixa|deixe|faz|fa[cç]a|bota|p[oõ]e|"
    r"tira|remove|aumenta|diminui|o\s+fundo|a\s+cor)|$))", re.IGNORECASE)
_WHAT_ROLE = {"texto": None, "nome": "titulo", "titulo": "titulo", "marca": "titulo", "frase": "subtitulo",
              "slogan": "subtitulo", "subtitulo": "subtitulo", "preco": "preco", "valor": "preco", "chamada": "cta",
              "botao": "cta", "telefone": "contato"}


def replace_ops(message, blocks):
    """"muda o texto pra X", "troca o preço por 49,90": substituição com o texto literal da mensagem.

    Rede de segurança determinística (o modelo continua sendo a fonte principal da interpretação)."""
    ops = []
    for m in _REPLACE.finditer(message or ""):
        value = textplan._nfc(re.sub(r"[\"“”']", "", m.group("value"))).strip(" .:-")
        role = _WHAT_ROLE.get(_fold(m.group("what")))
        if role == "preco":
            price = textplan._PRICE_RE.search(value) or textplan._PRICE_BIG.search(value)
            if not price:
                continue
            value = textplan.format_price(next(g for g in price.groups() if g))
        if not value or len(value) > textplan.MAX_TEXT:
            continue
        target = _resolve_target(blocks, {"papel": role}, message)
        if role == "titulo" and value == value.lower() and not any(c.isdigit() for c in value):
            # Título/nome todo em minúsculas: inicial maiúscula em cada palavra (exceto conectivos).
            small = {"de", "da", "do", "das", "dos", "e", "em", "para", "pra", "com", "a", "o", "as", "os"}
            value = " ".join(w if (k and w in small) else w[:1].upper() + w[1:] for k, w in enumerate(value.split()))
        op = {"acao": "trocar", "alvo": target["texto"] if target else None, "texto": value, "papel": role,
              "origem": "mensagem"}
        if target is None and len(blocks) > 1:
            op["ambiguo"] = True
        ops.append(op)
    return ops


def case_ops(message, blocks):
    """Maiúsculas/minúsculas pedidas em linguagem natural -> troca com o texto exato esperado.

    Ex.: "coloca o T de tecnologia maiúsculo" em "tecnologia é aqui" -> "Tecnologia é aqui". Só muda a forma das
    letras que o cliente já escreveu; o conteúdo continua o dele."""
    t = _fold(message)
    if not re.search(r"maiuscul|minuscul|caixa alta|caixa baixa|capitaliz", t):
        return []
    upper = bool(re.search(r"maiuscul|caixa alta|capitaliz", t))
    letter = re.search(r"\b(?:o|a)\s+([a-z])\s+(?:de|do|da)\s+([a-z0-9]+)", t)
    block = _target_block(blocks, message, letter.group(2) if letter else None)
    if not block:
        return []
    text = block["texto"]
    if re.search(r"\b(tudo|todo o texto|todas as letras|caixa alta|caixa baixa|inteir)", t):
        new = text.upper() if upper else text.lower()
    elif re.search(r"cada palavra|todas as palavras", t):
        new = " ".join((w[:1].upper() if upper else w[:1].lower()) + w[1:] for w in text.split(" "))
    elif letter:
        ch, word = letter.group(1), letter.group(2)
        folded_text = _fold(text)
        start = folded_text.find(word)
        pos = folded_text.find(ch, start if start >= 0 else 0)
        if pos < 0:
            return []
        new = text[:pos] + (text[pos].upper() if upper else text[pos].lower()) + text[pos + 1:]
    else:  # "primeira letra maiúscula", "com inicial maiúscula"
        new = (text[:1].upper() if upper else text[:1].lower()) + text[1:]
    if new == text:
        return []
    return [{"acao": "trocar", "alvo": text, "texto": new, "origem": "maiusculas"}]


def scene_level(message, model_level=None, complaint=False, previous_level=None):
    """Intensidade da mudança de cena. O pedido do cliente define um piso; o modelo pode pedir mais, nunca menos."""
    level = model_level if model_level in LEVELS else None
    floor = "total" if SCENE_TOTAL.search(message or "") else None
    if complaint and previous_level in LEVELS:
        floor = LEVELS[min(len(LEVELS) - 1, LEVELS.index(previous_level) + 2)]
    candidates = [lv for lv in (level, floor) if lv]
    if not candidates:
        return None
    return max(candidates, key=LEVELS.index)


def expectations(ops):
    """Lista do que precisa estar verdade depois do ajuste (textos)."""
    out = []
    for op in ops:
        action = op["acao"]
        if action in ("trocar", "adicionar") and op.get("texto"):
            out.append({"tipo": action, "alvo": op.get("alvo"), "texto": textplan._nfc(op["texto"])})
        elif action == "remover":
            out.append({"tipo": "remover", "alvo": op.get("alvo"), "papel": op.get("papel")})
        elif action in ("aumentar", "diminuir"):
            out.append({"tipo": action, "alvo": op.get("alvo"), "papel": op.get("papel")})
        elif action == "mover" and op.get("posicao") in textplan.POSITIONS:
            out.append({"tipo": "mover", "alvo": op.get("alvo"), "posicao": op["posicao"]})
        elif action == "cor":
            out.append({"tipo": "cor"})
    return out


def _texts(plan):
    return [b["texto"] for b in (plan or {}).get("blocos") or []]


def verify(previous_plan, new_plan, expected):
    """(feitas, faltando): cada mudança esperada conferida no estado final do plano."""
    done, missing = [], []
    before, after = _texts(previous_plan), _texts(new_plan)
    by_text = {b["texto"]: b for b in (new_plan or {}).get("blocos") or []}
    old_by_text = {b["texto"]: b for b in (previous_plan or {}).get("blocos") or []}
    for e in expected:
        kind = e["tipo"]
        if kind in ("trocar", "adicionar"):
            ok = e["texto"] in after
            label = (f"texto “{e['alvo']}” → “{e['texto']}”" if kind == "trocar" and e.get("alvo")
                     else f"incluir “{e['texto']}”")
        elif kind == "remover":
            gone = e.get("alvo")
            ok = (gone in before and gone not in after) if gone else len(after) < len(before)
            label = f"remover “{gone}”" if gone else "remover o texto"
        elif kind in ("aumentar", "diminuir"):
            name = e.get("alvo") if e.get("alvo") in by_text else next(iter(by_text), None)
            new_s = float((by_text.get(name) or {}).get("escala", 1.0))
            old_s = float((old_by_text.get(name) or {}).get("escala", 1.0))
            ok = new_s > old_s if kind == "aumentar" else new_s < old_s
            label = f"{kind} “{name}”"
        elif kind == "mover":
            name = e.get("alvo") if e.get("alvo") in by_text else next(iter(by_text), None)
            ok = (by_text.get(name) or {}).get("posicao") == e["posicao"]
            label = f"mover “{name}”"
        elif kind == "cor":
            ok = (new_plan or {}).get("cor") != (previous_plan or {}).get("cor")
            label = "cor das letras"
        else:
            continue
        (done if ok else missing).append(label)
    return done, missing


def plan_signature(plan):
    """O que importa do plano para saber se algo mudou de verdade."""
    plan = plan or {}
    return ([(b["texto"], b.get("papel"), b.get("posicao"), float(b.get("escala", 1.0))) for b in plan.get("blocos") or []],
            plan.get("cor"), plan.get("estilo"), plan.get("efeito"))


def plan_diff(previous_plan, new_plan):
    """Descrição do que mudou de fato entre dois planos (fonte da resposta ao cliente)."""
    out = []
    old = {b["texto"]: b for b in (previous_plan or {}).get("blocos") or []}
    new = {b["texto"]: b for b in (new_plan or {}).get("blocos") or []}
    replaced = set()
    for text, b in new.items():
        if text in old:
            sa, sb = float(old[text].get("escala", 1.0)), float(b.get("escala", 1.0))
            if sb > sa:
                out.append(f"“{text}” maior")
            elif sb < sa:
                out.append(f"“{text}” menor")
            if old[text].get("posicao") != b.get("posicao"):
                out.append(f"“{text}” em outra posição")
            continue
        gone = next((t for t in old if t not in new and t not in replaced and old[t].get("papel") == b.get("papel")), None)
        if gone:
            replaced.add(gone)
            out.append(f"“{gone}” → “{text}”")
        else:
            out.append(f"novo texto “{text}”")
    out += [f"“{t}” removido" for t in old if t not in new and t not in replaced]
    if (previous_plan or {}).get("cor") != (new_plan or {}).get("cor") and (new_plan or {}).get("cor"):
        out.append("nova cor nas letras")
    if (previous_plan or {}).get("efeito") != (new_plan or {}).get("efeito"):
        out.append("novo efeito nas letras")
    if (previous_plan or {}).get("estilo") != (new_plan or {}).get("estilo"):
        out.append("novo estilo de letra")
    return out


def scene_label(level, realistic):
    if not level:
        return ""
    if level == "total":
        return "um fundo totalmente novo" + (" em estilo fotográfico realista" if realistic else "")
    return {"pequena": "um ajuste leve no fundo", "media": "um fundo ajustado", "grande": "um fundo bem diferente"}[level]


def plan_adjustment(previous_spec, message, model_data, user_texts):
    """Decisão completa de um ajuste: textos esperados e aplicados, nível de cena e o que conferir depois.

    Devolve dict: plan (validado), esperado, feitas, faltando, cena (nível ou None), realista, reclamacao."""
    previous_plan = (previous_spec or {}).get("text_plan") or {"versao": 1, "blocos": [], "estilo": "moderno",
                                                                 "efeito": "auto", "cor": None}
    data = model_data if isinstance(model_data, dict) else {}
    adj = data.get("ajuste") if isinstance(data.get("ajuste"), dict) else {}
    complaint = bool(COMPLAINT.search(message or ""))
    ops = normalize_ops(adj.get("operacoes_texto"))
    blocks = previous_plan.get("blocos") or []
    # Troca cujo novo texto só muda maiúsculas/acentos do texto atual é legítima: o conteúdo continua do cliente.
    for op in ops:
        if op["acao"] in ("trocar", "adicionar") and op.get("texto") and not textplan._present(op["texto"], message):
            target = _resolve_target(blocks, op, message)
            polished = textplan.polish_from_model(op["texto"], message)
            if polished:
                op["texto"], op["polido"] = polished[0], True  # escrita melhorada do texto do cliente
            elif target and _same_letters(target["texto"], op["texto"]):
                op["alvo"], op["formato"] = target["texto"], True  # só a forma do texto atual (ex.: T maiúsculo)
        if op["acao"] in ("trocar", "remover", "aumentar", "diminuir", "mover"):
            target = _resolve_target(blocks, op, message)
            op["alvo"] = target["texto"] if target else (op.get("alvo") if op.get("alvo") in [b["texto"] for b in blocks]
                                                         else None)
            if op["acao"] == "trocar" and not op["alvo"] and len(blocks) > 1:
                op["ambiguo"] = True
    for derived in replace_ops(message, blocks):
        if not any(o["acao"] == "trocar" and o.get("alvo") == derived["alvo"] and o.get("texto") for o in ops):
            ops = [o for o in ops if not (o["acao"] == "trocar" and o.get("alvo") == derived["alvo"])] + [derived]
    for derived in case_ops(message, blocks):
        ops = [o for o in ops if not (o["acao"] == "trocar" and o.get("alvo") == derived["alvo"])] + [derived]
    plan = apply_operations(previous_plan, ops, message) if ops else revise_plan(previous_plan, message)
    # Acabamento: títulos e nomes novos/trocados com capitalização profissional (só a caixa muda).
    previous_texts = set(_texts(previous_plan))
    asked_case = bool(re.search(r"maiuscul|minuscul|caixa alta|caixa baixa|capitaliz", _fold(message)))
    for b in plan.get("blocos") or []:
        if b["texto"] not in previous_texts and not asked_case:  # pedido explícito de caixa sempre vence
            b["texto"] = textplan.professional_case(b["texto"], b["papel"], message)
    for op in ops:  # a conferência espera a versão com acabamento
        if op.get("texto") and op.get("origem") != "maiusculas" and not asked_case:
            op["texto"] = textplan.professional_case(op["texto"], op.get("papel") or "titulo", message)                 if op.get("papel") in (None, "titulo", "marca") else op["texto"]
    approved = [o["texto"] for o in ops if o.get("formato") or o.get("origem") or o.get("polido")]

    sources = list(user_texts) + [message] + _texts(previous_plan) + approved
    plan = textplan.validate_plan(plan, sources) or {**previous_plan, "blocos": []}
    expected = expectations([o for o in ops if not o.get("ambiguo")])
    _, missing = verify(previous_plan, plan, expected)
    ambiguous = [o["texto"] for o in ops if o.get("ambiguo") and o.get("texto")]
    done = plan_diff(previous_plan, plan)  # só o que mudou de fato
    model_scene = adj.get("cena") is True
    explicit_scene = bool(SCENE_TOTAL.search(message or "") or SCENE_WORDS.search(message or "")
                          or SMALL_CHANGE.search(message or ""))
    if model_scene and not explicit_scene and not complaint and (done or ops):
        model_scene = False  # pedido só de texto: nunca regerar a imagem para mudar letras
    level = scene_level(message, str(adj.get("intensidade") or "").lower() if model_scene else None, complaint,
                        ((previous_spec or {}).get("pedido_ajuste") or {}).get("cena"))
    if not (model_scene or complaint or explicit_scene):
        level = None
    if level and not model_scene and not explicit_scene and not complaint:
        level = None
    return {"plan": plan, "esperado": expected, "feitas": done, "faltando": missing, "cena": level,
            "ambiguo": ambiguous,
            "realista": bool(REALISTIC.search(message or "")), "reclamacao": complaint}


# Mudança visual mínima esperada para cada nível (diferença média 0..1 entre a arte anterior e a nova).
MIN_VISUAL_CHANGE = {"pequena": 0.02, "media": 0.05, "grande": 0.08, "total": 0.1}


def visual_change(old_bytes, new_bytes):
    """Quanto a arte mudou (0 = idêntica, 1 = totalmente diferente), em cor e em estrutura."""
    import io  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415
    from PIL import Image, ImageFilter  # noqa: PLC0415

    def small(data):
        img = Image.open(io.BytesIO(data)).convert("RGB").resize((64, 64), Image.BILINEAR)
        return np.asarray(img, dtype=np.float32) / 255, \
            np.asarray(img.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float32) / 255

    (a, ea), (b, eb) = small(old_bytes), small(new_bytes)
    color = float(np.abs(a - b).mean())
    structure = float(np.abs(ea - eb).mean())
    return round(max(color, structure * 1.5), 3)
