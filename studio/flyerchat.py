"""Encarte pela conversa: cada mensagem vira operações sobre o MESMO briefing que o painel mostra.

O briefing (sg_conversations.briefing) é a única fonte de verdade: o chat e o painel leem e escrevem nele.
Operações reconhecidas aqui (determinísticas, respostas imediatas):
  produtos com preço ("leite integral por R$ 4,99, arroz por 31,90"), remover, trocar preço, ordem, destaque,
  formato, cores, estilo/layout, "me mostra alguns estilos", "gostei do segundo", buscar/trocar fotos,
  confirmar preço incerto ("31,90"), loja, título e "pode gerar".
O que não for reconhecido segue para o modelo de linguagem (HostGator), como antes.
Nunca inventa produto, preço, código de barras ou loja: tudo vem do que o cliente escreveu (ou confirmou).
"""
import re
import unicodedata

from . import layouts
from .briefing import FORMATS, MAX_PRODUCTS, format_brl, price_to_cents

COLOR_WORDS = {
    "vermelh": (215, 25, 32), "amarel": (255, 204, 0), "azul": (0, 110, 200), "verde": (0, 150, 80),
    "pret": (18, 18, 20), "branc": (250, 250, 250), "dourad": (214, 180, 96), "laranja": (255, 128, 0),
    "rosa": (240, 80, 150), "roxo": (120, 60, 200), "lilas": (180, 140, 230), "cinza": (150, 150, 156),
    "vinho": (120, 20, 40), "marrom": (110, 70, 40), "bege": (235, 222, 200), "turquesa": (0, 190, 190),
}
STYLE_WORDS = [("jornal", r"jornal|tabloide|supermercado|grandes? (redes?|mercados?)|cl[aá]ssico"),
               ("farmacia", r"farm[aá]cia|drogaria|sa[uú]de|limp[oa]"),
               ("premium", r"premium|luxo|sofisticad|escur|elegante|minimalista escur"),
               ("relampago", r"rel[aâ]mpago|urgente|imperd[ií]vel|queima|black friday|chamativ"),
               ("catalogo", r"cat[aá]logo|moderno|minimalista|clean|clar[oa]")]
ORDINALS = {"primeir": 0, "1": 0, "um": 0, "segund": 1, "2": 1, "dois": 1, "terceir": 2, "3": 2, "tres": 2,
            "quart": 3, "4": 3, "quint": 4, "5": 4}


GEN_INTENT = re.compile(
    r"^\s*(?:(?:ok|beleza|blz|show|perfeito|isso|sim|certo|top|otimo|entao|agora|pronto|e)[\s,!.]+)*"
    r"(?:pode\s+|ja\s+|agora\s+|entao\s+|por favor\s+)*"
    r"(?:gere|gerar|gera|crie|cria|criar|faca|faz|fazer|monte|montar|monta|manda|mande|finalize|finaliza|finalizar|"
    r"produza|produzir|bora|vamos(?:\s+la)?|manda\s+ver|solta)"
    r"(?:\s+(?:a|o|as|os|um|uma|ele|ela|isso|ai|logo|agora|entao|de novo|novamente|pra mim|por favor|"
    r"arte|encarte|imagem|panfleto|tabloide|flyer|post|story|feed|final|versao|assim|desse jeito|"
    r"dessa forma|nesse estilo|nesse layout|com isso))*\s*[!.]*\s*$")
EXPLICIT_GEN = re.compile(r"\b(pode gerar|gera o encarte|gere o encarte|gerar o encarte|faz a arte|faca a arte|"
                          r"cria a arte|crie a arte|gera a arte|gere a arte|e gera|e gere|e ja gera|pode criar a arte|"
                          r"pode fazer a arte|gera agora|gere agora|gera entao|gere entao|manda ver|pode criar|"
                          r"pode fazer o encarte|pode montar)\b")
NEW_VERSION = re.compile(r"\b(nova versao|outra versao|outra opcao|mais uma versao|refaz(?:er)? diferente|"
                         r"gera outra|gere outra|faz outra|faca outra|cria outra|crie outra|tenta outra|tenta de novo|"
                         r"mais (?:criativ|dinamic|moderno|ousad)\w*)\b")
REFERENCE_WORDS = re.compile(r"(parecid|igual|mesmo estilo|mesmo layout|mesmo modelo|inspirad|inspira|baseado|"
                             r"com base|tipo (?:a|essa|esta|dessa|desta) (?:imagem|arte|foto|refer)|como (?:a|essa|esta) "
                             r"(?:imagem|arte|foto)|da imagem|dessa imagem|desta imagem|em anexo|anexad|referencia|"
                             r"segue o modelo|nesse estilo|nesse modelo|siga o modelo|"
                             r"(?:cores?|layout|organiza\w*|paleta|estrutura)\W+(?:\w+\W+){0,3}(?:primeir|segund|terceir)\w*)")
COMPOSITION_WORDS = [
    ("arranjo", "diagonal", r"diagona|inclinad|torto|tortos|angulad"),
    ("arranjo", "assimetrica", r"assimetric|desigual"),
    ("arranjo", "zigue", r"\bzig|alternad|escadinha|escada"),
    ("arranjo", "livre", r"\blivre\b|solto|espalhad|bagun"),
    ("arranjo", "destaque", r"um grande|produto grande|destaque grande"),
    ("arranjo", "tabloide", r"tabloide|jornal de ofertas|supermercado"),
    ("arranjo", "grade", r"\bgrade\b|organizad|alinhad|certinho"),
    ("preco", "explosao", r"explos|estrela|estourad|splash"),
    ("preco", "etiqueta", r"etiqueta|plaquinha|\btag\b"),
    ("preco", "circulo", r"preco\w* (?:em|no|num) circulo|bolinha"),
    ("preco", "numeros", r"numeros grandes|preco grande|precos? soltos?"),
    ("fundo", "raios", r"raios|sol atras|sunburst|radial"),
    ("fundo", "listras", r"listra|faixas diagona"),
    ("fundo", "bolhas", r"bolha|circulos no fundo"),
    ("fundo", "diagonal", r"fundo (?:em |na )?diagonal|dividid"),
    ("produto_forma", "circulo", r"fotos? (?:em circulo|redond|circular)|circulos? nas fotos"),
    ("cabecalho", "onda", r"\bonda|ondulad"),
]


NEW_FLYER = re.compile(r"\b(novo encarte|outro encarte|mais um encarte|segundo encarte|terceiro encarte|encarte novo|"
                       r"outro panfleto|novo panfleto)\b")
BACK_FLYER = re.compile(r"\bvolt\w*\s+(?:ao|para o|pro|no|para)\s+(?:encarte|panfleto)\s*(?P<q>.*)$")
LIST_FLYERS = re.compile(r"\b(quais|meus|lista\w*|mostra\w*|ver)\s+(?:os\s+)?(encartes|panfletos)\b")
PHOTO_CHOICE = re.compile(r"^(?:(?:usa|use|usar|pode usar|quero|escolho|fico com|fica com|pega|coloca|prefiro|e|sim)\s+)*"
                          r"(?:a|o)?\s*(?:opcao|foto|imagem|numero|n)?\s*(?P<n>[123]|primeir\w*|segund\w*|terceir\w*)"
                          r"\s*(?:opcao|foto|imagem)?\s*(?:(?:d[oa]s?|pr[oa]|para o|para a|no|na)\s+(?P<nome>[\w\s\-]+?))?\s*[.!]*$")
NO_OPTION = re.compile(r"\b(nenhuma|nenhum desses|nenhuma delas|nao serve|nao e nenhuma)\b")


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")


def _cap(name):
    name = " ".join(name.split())
    return name[:1].upper() + name[1:] if name else name


def _price(raw):
    cents = price_to_cents((raw or "").replace(" ", ""))
    return format_brl(cents) if cents else ""


_LEAD = re.compile(r"^(?:e|tamb[eé]m|tbm|mais|coloca|coloque|adiciona|adicione|inclui|inclua|p[oõ]e|ponha|bota|bote|"
                   r"quero|tem|temos|com|o|a|os|as|um|uma|uns|umas|produtos?:?|itens?:?)\s+", re.IGNORECASE)
def parse_products(text):
    """Produtos com preço numa frase natural. Devolve [{"nome", "preco", "preco_de"}] (literais do cliente)."""
    out = []
    # Separa itens por vírgula, ponto e vírgula, quebra de linha ou " e " antes de um novo item.
    parts = re.split(r"[;\n]|,(?!\d)|\s+e\s+(?=[A-Za-zÀ-ÿ])", text or "")
    for part in parts:
        part = part.strip(" .:")
        m = re.search(r"(?P<nome>[A-Za-zÀ-ÿ][\wÀ-ÿ\s.\-/&']*?)\s+(?:(?:sai\s+)?de\s+(?:r\$\s*)?(?P<de>\d{1,5}(?:[.,]\d{1,2})?)"
                      r"(?:\s*reais)?\s+(?:por|para|pra)\s+|(?:por|a|=|:|custando|sai(?:\s+a|\s+por)?)\s+|(?=r\$)|(?=\d{1,5},\d{2}\b))"
                      r"(?:r\$\s*)?(?P<preco>\d{1,5}(?:[.,]\d{1,2})?)(?:\s*reais)?\b", part, re.IGNORECASE)
        if not m:
            continue
        nome = m.group("nome").strip()
        while True:
            new = _LEAD.sub("", nome)
            if new == nome:
                break
            nome = new
        if re.search(r"\b(encarte|panfleto|tabloide|loja)\b", fold(nome)) and re.search(r"\s(?:com|tem)\s", nome):
            nome = re.split(r"\s(?:com|tem)\s", nome)[-1]  # "novo encarte para a Farmácia Vida com dipirona"
        nome = re.sub(r"\s+(?:por|a|de|com|sai)$", "", nome, flags=re.IGNORECASE).strip(" -")
        if len(nome) < 2 or len(nome.split()) > 6 or re.search(r"\b(encarte|ofertas?|promo[cç][aã]o|loja|story|feed)\b",
                                                                 fold(nome)):
            continue
        preco = _price(m.group("preco"))
        de = _price(m.group("de") or "")
        if preco:
            out.append({"nome": _cap(nome), "preco": preco, "preco_de": de if de and de != preco else ""})
    return out


def _find(products, text):
    """Produto citado na mensagem (por nome, palavra principal ou posição: "o segundo produto")."""
    t = fold(text)
    for key, idx in ORDINALS.items():
        if re.search(r"\b" + key + r"\w*\s+(produto|item)", t) and idx < len(products):
            return products[idx]
    best, score = None, 0
    for p in products:
        words = [w for w in re.findall(r"[a-z0-9]+", fold(p["nome"])) if len(w) >= 3]
        hits = sum(1 for w in words if re.search(r"\b" + w, t))
        if hits > score:
            best, score = p, hits
    return best


def _colors(text):
    t = fold(text)
    found = []
    for m in re.finditer(r"[a-z]+", t):
        for prefix, rgb in COLOR_WORDS.items():
            if m.group(0).startswith(prefix) and rgb not in found:
                found.append(rgb)
    return found


PRICE_MENTION = re.compile(r"r\$\s*\d|\b\d{1,5},\d{2}\b|\b\d{1,5}\s*reais\b", re.IGNORECASE)


def understood_all_prices(message, done_items):
    """Todos os preços da mensagem viraram itens? Se não, a mensagem vai para o modelo (nada se perde)."""
    mentions = len(PRICE_MENTION.findall(message or ""))
    return mentions <= done_items


def apply(message, briefing):
    """Aplica a mensagem ao briefing. Devolve (briefing_novo, feitos: [str], acoes: set, pendente: str|None).

    acoes: {"gerar", "previas", "buscar_fotos", "trocar_foto"}; feitos vazio + acoes vazias = não reconhecido."""
    b = {**briefing, "produtos": [dict(p) for p in briefing.get("produtos") or []],
         "layout": dict(briefing.get("layout") or {})}
    t = fold(message)
    done, actions = [], set()
    products = b["produtos"]

    # Confirmação de preço incerto ("31,90" / "é 4,99" / "o do arroz é 31,90").
    pend = [c for c in b.get("confirmar") or []]
    lone = re.fullmatch(r"\s*(?:e|é|eh|o correto e|correto|isso|sim,?)?\s*(?:r\$\s*)?(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:reais)?\s*\.?\s*",
                        t)
    if pend and (lone or _find([{"nome": c["produto"]} for c in pend], message)):
        values = re.findall(r"\d{1,5}(?:[.,]\d{1,2})?", message)
        target = pend[0] if lone or len(pend) == 1 else next(
            (c for c in pend if fold(c["produto"]).split()[0] in t), pend[0])
        if values:
            p = next((x for x in products if x["nome"] == target["produto"]), None)
            if p is not None:
                p[target["campo"]] = _price(values[-1])
                b["confirmar"] = [c for c in pend if c is not target]
                done.append(f"{'preço' if target['campo'] == 'preco' else 'preço antigo'} de {p['nome']}: {p[target['campo']]}")
                if lone:
                    return b, done, actions, None

    # Vários encartes na mesma conversa (cada um com o seu briefing).
    if LIST_FLYERS.search(t):
        actions.add("listar_encartes")
    back = BACK_FLYER.search(t)
    if back:
        actions.add("voltar_encarte:" + back.group("q").strip(" .!?"))
        return b, done, actions, None
    if NEW_FLYER.search(t):
        actions.add("novo_encarte")

    # Escolha de uma das fotos sugeridas ("usa a 2 do leite", "a primeira", "nenhuma serve").
    opcoes = b.get("opcoes_foto") or {}
    if opcoes:
        m = PHOTO_CHOICE.match(t.strip())
        if m:
            n = m.group("n")
            idx = next(v for k, v in ORDINALS.items() if n.startswith(k))
            names = list(opcoes)
            target = _find([{"nome": x} for x in names], m.group("nome") or "") if m.group("nome") else None
            name = target["nome"] if target else (names[0] if len(names) == 1 else None)
            if name and idx < len(opcoes[name]):
                actions.add(f"foto:{idx}:{name}")
                return b, done, actions, None
            if not name:
                actions.add("foto:?")
                return b, done, actions, None
        elif NO_OPTION.search(t):
            names = list(opcoes)
            target = _find([{"nome": x} for x in names], message)
            for name in ([target["nome"]] if target else names):
                opcoes.pop(name, None)
            b["opcoes_foto"] = opcoes
            done.append("sem foto sugerida para " + (target["nome"] if target else ", ".join(names)))

    # Loja ("encarte para a Drogarias PoupAqui").
    m = re.search(r"(?:encarte|panfleto|tabloide|promo[cç][aã]o|ofertas?)\s+(?:para|pra|da|do)\s+(?:a\s+|o\s+)?"
                  r"(?P<loja>[A-ZÀ-Ý][\wÀ-ÿ&'.]*(?:\s+[A-ZÀ-Ý][\wÀ-ÿ&'.]*)*)", message)
    if m and m.group("loja").strip().rstrip(".!,;:") != b.get("loja"):
        b["loja"] = m.group("loja").strip().rstrip(".!,;:")
        done.append(f"loja: {b['loja']}")

    # Remover.
    m = re.search(r"\b(remov\w*|tir\w*|exclu\w*|apag\w*)\s+(?:o|a|os|as)?\s*(?P<nome>.+)", t)
    if m and products:
        p = _find(products, m.group("nome"))
        if p:
            products.remove(p)
            done.append(f"removi {p['nome']}")

    # Produtos e preços (novos ou atualização de preço de um produto já existente).
    if not re.search(r"\b(remov|tir|exclu|apag)", t):
        changed_price = re.search(r"(pre[cç]o|valor)\s+d[oa]s?\s+(?P<nome>.+?)\s+(?:para|pra|por)\s+(?:r\$\s*)?"
                                  r"(?P<preco>\d{1,5}(?:[.,]\d{1,2})?)", t)
        if changed_price and products:
            p = _find(products, changed_price.group("nome"))
            if p:
                p["preco"] = _price(changed_price.group("preco"))
                done.append(f"preço de {p['nome']}: {p['preco']}")
        else:
            for item in parse_products(message):
                same = next((p for p in products if fold(p["nome"]) == fold(item["nome"]) or
                             fold(item["nome"]) in fold(p["nome"]).split(" ")[:1]), None)
                if same:
                    same["preco"] = item["preco"]
                    if item["preco_de"]:
                        same["preco_de"] = item["preco_de"]
                    done.append(f"preço de {same['nome']}: {same['preco']}")
                elif len(products) < MAX_PRODUCTS:
                    products.append({"nome": item["nome"], "ean": "", "preco": item["preco"], "preco_de": item["preco_de"],
                                     "unidade": "", "asset_id": None})
                    done.append(f"{item['nome']} {item['preco']}" + (f" (de {item['preco_de']})" if item["preco_de"] else ""))

    # Ordem ("coloca o café primeiro").
    m = re.search(r"\b(?:coloca|coloque|deixa|deixe|p[oõ]e|ponha)\s+(?:o|a)?\s*(?P<nome>.+?)\s+(?:em\s+)?"
                  r"(?P<pos>primeiro|no come[cç]o|na frente|por ultimo|no final|em ultimo)", t)
    if m and products:
        p = _find(products, m.group("nome"))
        if p:
            products.remove(p)
            if re.search(r"ultimo|final", m.group("pos")):
                products.append(p)
            else:
                products.insert(0, p)
            done.append(f"{p['nome']} {'por último' if re.search(r'ultimo|final', m.group('pos')) else 'primeiro'}")

    # Destaque ("café em destaque", "o café maior", "destaca o shampoo").
    m = re.search(r"(?P<nome>[\w\s]+?)\s+(?:em\s+destaque|maior|destacad\w*|como principal)|destac\w+\s+(?:o|a)?\s*(?P<nome2>.+)", t)
    if m and products:
        p = _find(products, m.group("nome") or m.group("nome2") or "")
        if p:
            b["layout"]["destaque"] = p["nome"]
            done.append(f"{p['nome']} em destaque")

    # Formato.
    for key, pat in (("story", r"\bstor(y|ies)\b|vertical|9\s*[:x/]\s*16"), ("quadrado", r"quadrad|1\s*[:x/]\s*1"),
                     ("feed_4x5", r"\bfeed\b|4\s*[:x/]\s*5"), ("a4", r"\ba4\b|impress")):
        if re.search(pat, t) and b.get("formato") != key:
            b["formato"] = key
            done.append(f"formato {FORMATS[key]['nome']}")
            break

    # Cores ("fundo vermelho e amarelo").
    if re.search(r"\b(fundo|cores?|cor)\b", t):
        cores = _colors(message)
        if cores:
            b["layout"]["cores"] = [list(c) for c in cores[:2]]
            done.append("cores do fundo")

    # Estilos: escolher, pedir prévias, "gostei do segundo", "cria outro".
    previews = b["layout"].get("previas") or []
    liked = re.search(r"gost\w*\s+d[oa]\s+(?P<o>primeir\w*|segund\w*|terceir\w*|[123]|um|dois|tres)", t)
    if liked and previews:
        idx = next(v for k, v in ORDINALS.items() if liked.group("o").startswith(k))
        if idx < len(previews):
            chosen = previews[idx]
            if chosen.startswith("criativo:"):
                b["layout"].update({"modelo": "criativo", "semente": int(chosen.split(":")[1])})
            else:
                b["layout"]["modelo"] = chosen
                if chosen in layouts.STYLES:
                    b["layout"]["composicao"] = {}
            done.append(f"estilo {layouts.preview_name(chosen)}")
    elif re.search(r"(mostr|ver|v[eê]|sugir|sugest|op[cç][oõ]es|outros?)\w*\s*(alguns|outros|umas|op[cç][oõ]es)?\s*"
                   r"(estilos?|layouts?|modelos?|op[cç][oõ]es de (layout|estilo))", t) or \
            re.search(r"(n[aã]o gostei|cria outro|outro layout|outro estilo)", t):
        actions.add("previas")
    else:
        for modelo, pat in STYLE_WORDS:
            if re.search(r"(estilo|layout|modelo|jeito|tipo|parecido)", t) and re.search(pat, t):
                b["layout"]["modelo"] = modelo
                done.append(f"estilo {layouts.STYLES[modelo]['nome']}")
                break
    if re.search(r"fundo\s+(com|de)\s+(foto|imagem|cen[aá]rio)|fundo\s+realista|fundo\s+gerado", t):
        b["layout"]["fundo_ia"] = True
        done.append("fundo com imagem gerada")

    # Composição livre pedida pelo cliente ("produtos na diagonal", "preço em explosão", "fundo com raios").
    comp = dict(b["layout"].get("composicao") or {})
    asked = {}
    for key, value, pat in COMPOSITION_WORDS:
        if key not in asked and re.search(pat, t):
            asked[key] = value
    if asked:
        comp.update(asked)
        b["layout"]["composicao"] = comp
        if b["layout"].get("modelo") != "referencia":
            b["layout"]["modelo"] = "criativo"
        nomes = {"diagonal": "produtos na diagonal", "assimetrica": "composição assimétrica", "zigue": "produtos em zigue-zague",
                 "livre": "composição livre", "destaque": "um produto grande em destaque", "tabloide": "estilo tabloide",
                 "grade": "produtos alinhados", "explosao": "preços em explosão", "etiqueta": "preços em etiqueta",
                 "circulo": "círculos", "numeros": "preços em números grandes", "raios": "fundo com raios",
                 "listras": "fundo com listras", "bolhas": "fundo com bolhas", "onda": "cabeçalho em onda"}
        done.append(", ".join(nomes.get(v, v) for v in asked.values()))
    if NEW_VERSION.search(t):
        b["layout"]["semente"] = int(b["layout"].get("semente") or 1) + 1
        if not b["layout"].get("modelo") or b["layout"].get("modelo") in layouts.STYLES:
            b["layout"]["modelo"] = "criativo"
        done.append("nova versão do layout (mesmos produtos, preços e fotos)")
        actions.add("gerar" if briefing.get("gerado") else "previas")
    if REFERENCE_WORDS.search(t):
        actions.add("referencia")

    # Fotos.
    if re.search(r"(troc|mud)\w*\s+(a\s+)?foto", t):
        actions.add("trocar_foto")
    elif re.search(r"(busc|procur|pesquis|peg|baix)\w*.*\b(fotos?|imagens?)|\b(fotos?|imagens?)\b.*internet|internet", t):
        actions.add("buscar_fotos")

    # Gerar.
    if wants_generation(message):
        actions.add("gerar")
    if re.search(r"\b(atualiza|refaz|gera de novo|gera outra vez)\b", t) and briefing.get("gerado"):
        actions.add("gerar")
    return b, done, actions, None


def numbers_accounted(message, briefing):
    """Cada número da mensagem virou preço/preço antigo de um produto do briefing (ou é formato/posição conhecidos)?
    Se sobra número ("leve 2 paga 45", "1 só é 55"), o leitor rápido não entendeu tudo."""
    known = set()
    for p in briefing.get("produtos") or []:
        for field in ("preco", "preco_de"):
            cents = price_to_cents((p.get(field) or "").replace("R$", "").strip())
            if cents:
                known.add(cents)
    t = fold(message)
    t = re.sub(r"\b\d+\s*[:x/]\s*\d+\b|\ba4\b|\b(?:primeir|segund|terceir)\w*", " ", t)  # 9:16, A4, ordinais
    for raw in re.findall(r"\d{1,5}(?:[.,]\d{1,2})?", t):
        cents = price_to_cents(raw)
        if not cents or cents not in known:
            return False
    return True


def wants_generation(message):
    """O cliente pediu para gerar AGORA ("gere então", "pode criar", "faz a arte", "manda ver")?"""
    t = fold(message).strip()
    return bool(GEN_INTENT.match(t) or EXPLICIT_GEN.search(t))


def missing(briefing):
    """O que falta (indispensável) para gerar: produtos, preços e fotos. Loja/validade/formato são opcionais."""
    items = []
    if not briefing.get("produtos"):
        items.append("os produtos com preço")
    for p in briefing.get("produtos") or []:
        if not p.get("preco"):
            items.append(f"o preço de {p['nome']}")
    for c in briefing.get("confirmar") or []:
        items.append(f"confirmar {('o preço' if c['campo'] == 'preco' else 'o preço antigo')} de {c['produto']}"
                     + (f" (li {c['lido']})" if c.get("lido") else ""))
    no_photo = [p["nome"] for p in briefing.get("produtos") or [] if not p.get("asset_id")]
    return items, no_photo


def summary(briefing, done):
    """Resposta natural: o que mudou, como está e o próximo passo (sem pedir o que é opcional)."""
    parts = []
    if done:
        parts.append("Anotei: " + "; ".join(done) + ".")
    prods = briefing.get("produtos") or []
    if prods:
        parts.append("Encarte" + (f" da {briefing['loja']}" if briefing.get("loja") else "") + ": "
                     + ", ".join(f"{p['nome']} {p['preco'] or '(sem preço)'}" for p in prods) + ".")
    need, no_photo = missing(briefing)
    if need:
        parts.append("Falta " + ", ".join(need) + ".")
    elif no_photo:
        parts.append("Sem foto: " + ", ".join(no_photo) + ". Posso buscar na internet (é só dizer “busca as fotos”) "
                     "ou você envia as fotos aqui.")
    else:
        parts.append("Está tudo pronto: diga “pode gerar” ou “me mostra alguns estilos”.")
    return " ".join(parts)


_UNDERSTOOD = re.compile(
    r"stor(y|ies)|vertical|quadrad|\bfeed\b|\ba4\b|formato|fundo|cor(es)?\b|estilo|layout|modelo|destaq|maior|primeiro|"
    r"ultimo|final|remov|tir[aeo]|exclu|apag|foto|imagem|imagens|internet|busc|procur|pesquis|gera|gerar|pode|manda|"
    r"finaliza|gostei|mostr|op[cç]|outr[oa]|encarte|panfleto|tabloide|para a|para o|pra|loja|drogaria|farmacia|mercado|"
    r"preco|valor|troc|mud|coloc|deix|quero|favor|obrigad|ok|sim|beleza|valeu|oi|ola|bom dia|boa (tarde|noite)|"
    r"jornal|premium|relampago|catalogo|moderno|minimalista|gere|gera|cri[ae]|faz|faca|mont|versao|diagona|"
    r"assimetr|zig|alternad|explos|etiqueta|raios|listra|bolha|circul|onda|dinamic|criativ|parecid|igual|inspir|"
    r"referencia|anexo|anexad|imagem|arte|entao|agora|isso|assim", re.IGNORECASE)


def all_understood(message):
    """Cada pedaço da mensagem foi entendido (item com preço ou operação conhecida)? Senão, o modelo interpreta tudo
    — assim um produto sem preço ou um pedido diferente nunca se perde."""
    parts = [p.strip(" .:!?") for p in re.split(r"[;\n]|,(?!\d)|\s+e\s+(?=[A-Za-zÀ-ÿ])", message or "") if p.strip(" .:!?")]
    for part in parts:
        if parse_products(part):
            continue
        if _UNDERSTOOD.search(fold(part)) or len(part.split()) <= 1 and PRICE_MENTION.search(part):
            continue
        if re.fullmatch(r"(?:r\$\s*)?\d{1,5}(?:[.,]\d{1,2})?(?:\s*reais)?", fold(part)):
            continue
        return False
    return True
