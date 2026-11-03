"""Diretor de arte do encarte: o modelo planeja o design, o Chromium monta, o modelo de visão avalia e refina.

Sem templates fixos: o modelo decide a composição dentro da linguagem de design de htmlflyer (paleta, cabeçalho,
grade, destaque, cartão, preço, fundo, decoração, densidade). A avaliação estética olha a arte pronta e devolve nota,
problemas e ajustes no mesmo formato, aplicados numa nova montagem. Os dados comerciais nunca passam pelo modelo de
forma que ele possa alterá-los: nomes e preços vêm do briefing.
"""
import json

from . import htmlflyer

DESIGN_PROMPT = """Você é um diretor de arte sênior de encartes de varejo (supermercado, farmácia, lojas).
Planeje o DESIGN de um encarte profissional, pronto para divulgar, com os produtos informados. Pense em hierarquia,
equilíbrio, aproveitamento do espaço (fotos grandes, sem vazios), contraste e identidade da loja.
Responda SOMENTE com JSON compacto, com estas chaves:
{"paleta": {"primaria": "#hex", "secundaria": "#hex", "fundo": "#hex", "texto": "#hex", "preco": "#hex"},
 "fundo": "gradiente|listras|raios|bolinhas|diagonal|liso",
 "cabecalho": "faixa|diagonal|curva|selo", "titulo_fonte": "anton|bebas|archivo|montserrat|playfair|orbitron",
 "grade": "uniforme|destaque|mosaico|diagonal|lista", "destaque": "nome EXATO de um produto ou null",
 "cartao": "branco|sem_cartao|vidro|borda", "preco_estilo": "etiqueta|selo|bloco|faixa",
 "decoracao": ["estrelas|confete|circulos|faixas"], "densidade": "compacta|equilibrada|arejada",
 "composicao": "heroi_lateral|heroi_topo|diagonal|mosaico|vitrine|grade",
 "proporcao_destaque": 0.45-0.7, "angulo": -14 a 14, "sobreposicao": 0-0.12, "estilo_unidade": "cartao|solto",
 "chamada": "frase curta genérica de convite (ex.: Aproveite!), sem preços, datas ou promessas", "justificativa": "1 frase"}
Composição (você decide a distribuição, não há modelo fixo): heroi_lateral = um produto grande à esquerda e os demais
empilhados (bom para 3-4 produtos); heroi_topo = destaque largo no topo; diagonal = produtos escalonados numa faixa
inclinada (dinâmico); mosaico = tamanhos variados (4-7 produtos); vitrine = produtos recortados soltos com selo de preço
sobreposto (fotos boas, visual de varejo); grade = muitos produtos. Produtos SEM foto viram automaticamente uma faixa
tipográfica compacta (nunca caixa vazia): planeje o destaque entre os que TÊM foto.
Regras: siga a referência visual e o pedido do cliente quando existirem; cores com bom contraste (texto legível);
"destaque" só se fizer sentido (produto principal, oferta mais forte ou pedido do cliente); poucos produtos pedem
cartões grandes e densidade arejada; muitos produtos pedem densidade compacta; farmácia combina com azul/verde/branco,
mercado com vermelho/amarelo, premium com escuro/dourado — mas a referência e a marca do cliente vêm primeiro."""

CRITIQUE_PROMPT = """Você é um diretor de arte exigente avaliando um encarte de ofertas pronto para divulgação.
Avalie a imagem: equilíbrio da composição, espaços vazios excessivos, tamanho das fotos (produtos devem ser
protagonistas), proporção e organização dos preços, legibilidade dos textos, contraste, hierarquia, acabamento e se
parece profissional. Compare com o pedido do cliente e com a referência (se houver).
Responda SOMENTE com JSON compacto:
{"nota": 0-10, "problemas": ["curtos e concretos"],
 "ajustes": {só as chaves do design que devem mudar: paleta, fundo, cabecalho, titulo_fonte, composicao, destaque,
 proporcao_destaque, angulo, sobreposicao, estilo_unidade, cartao, preco_estilo, decoracao, densidade}}
Se a composição está monótona (tudo igual, repetitivo) ou desequilibrada, troque "composicao" ou o "destaque".
Seja honesto: nota 8+ só se estiver realmente profissional."""


def describe_products(briefing):
    lines = []
    for p in briefing.get("produtos") or []:
        extra = []
        if p.get("preco_de"):
            extra.append(f"de {p['preco_de']}")
        if p.get("oferta"):
            extra.append(p["oferta"])
        lines.append(f"- {p['nome']}: {p.get('preco') or 'sem preço'}" + (f" ({'; '.join(extra)})" if extra else "")
                     + ("" if p.get("asset_id") else " [sem foto]"))
    return "\n".join(lines)


def design_messages(briefing, reference=None, instructions="", previous=None, seed=1):
    """Mensagens para o modelo planejar o design (texto; a referência já chega analisada)."""
    fmt = briefing.get("formato") or "feed_4x5"
    parts = [f"Loja: {briefing.get('loja') or '(não informada)'}", f"Formato: {fmt}",
             f"Título: {briefing.get('titulo') or 'Ofertas'}", f"Produtos ({len(briefing.get('produtos') or [])}):",
             describe_products(briefing)]
    if instructions:
        parts.append(f"Pedido do cliente sobre o visual: {instructions[:500]}")
    if reference:
        parts.append("Referência visual analisada (siga a organização e as cores): " + json.dumps(reference_brief(reference),
                                                                                               ensure_ascii=False))
    if previous:
        parts.append(f"Design anterior (o cliente pediu OUTRA versão, faça diferente; variação nº {seed}): "
                     + json.dumps({k: previous.get(k) for k in ("grade", "fundo", "cabecalho", "preco_estilo", "cartao")},
                                  ensure_ascii=False))
    return [{"role": "system", "content": DESIGN_PROMPT}, {"role": "user", "content": "\n".join(parts)}]


def reference_brief(spec):
    """Resumo da especificação da referência em termos de design (cores em hex, organização)."""
    hd = spec.get("cabecalho") or {}
    pr = spec.get("preco") or {}
    return {"cor_cabecalho": htmlflyer._hex(hd.get("principal")) if hd.get("principal") else None,
            "cor_selo": htmlflyer._hex(hd.get("selo")) if hd.get("selo") else None,
            "fundo_conteudo": htmlflyer._hex(spec.get("fundo_conteudo")),
            "tarja": bool(spec.get("tarja")), "moldura": htmlflyer._hex((spec.get("moldura") or {}).get("cor"))
            if spec.get("moldura") else None,
            "produtos_recortados": (spec.get("produtos") or {}).get("recortados", True),
            "preco_cor": htmlflyer._hex(pr.get("cor")) if pr.get("cor") else None,
            "precos_com_selo": pr.get("selo_fracao", 0) >= 0.4, "organizacao": (spec.get("visual") or {}).get("organizacao"),
            "descricao": (spec.get("visual") or {}).get("descricao")}


def design_from_reference(spec):
    """Design inicial derivado da referência (usado se o modelo falhar e como base de cores)."""
    hd = spec.get("cabecalho") or {}
    pr = spec.get("preco") or {}
    primary = htmlflyer._hex(hd.get("principal") or (spec.get("tarja") or {}).get("cor"), "#d71920")
    second = htmlflyer._hex(hd.get("selo"), "#ffcc00")
    bg = htmlflyer._hex(spec.get("fundo_conteudo"), "#ffffff")
    return htmlflyer.normalize({
        "paleta": {"primaria": primary, "secundaria": second, "fundo": bg, "texto": "#1e1e22",
                   "preco": htmlflyer._hex(pr.get("cor"), primary) if htmlflyer._luma(htmlflyer._hex(pr.get("cor"), primary)) > 60
                   else primary},
        "fundo": "liso" if htmlflyer._luma(bg) > 235 else "gradiente", "cabecalho": "faixa",
        "grade": "uniforme", "cartao": "sem_cartao" if (spec.get("produtos") or {}).get("recortados", True) else "branco",
        "preco_estilo": "etiqueta", "decoracao": ["estrelas"] if hd.get("selo") else [], "densidade": "compacta",
        "estilo_unidade": "solto" if (spec.get("produtos") or {}).get("recortados", True) else "cartao",
        "composicao": "vitrine" if (spec.get("produtos") or {}).get("recortados", True) else "auto",
    })


def compositions_for(n_photos):
    """Composições que fazem sentido para a quantidade de produtos com foto (mais adequadas primeiro)."""
    if n_photos <= 1:
        return ["heroi_topo", "vitrine", "diagonal"]
    if n_photos <= 3:
        return ["heroi_lateral", "diagonal", "vitrine", "heroi_topo"]
    if n_photos <= 6:
        return ["mosaico", "vitrine", "heroi_lateral", "diagonal", "heroi_topo"]
    return ["grade", "mosaico", "vitrine", "heroi_topo"]


def merge(design, changes, names):
    """Aplica os ajustes da avaliação (só chaves conhecidas; destaque precisa ser um produto do briefing)."""
    out = dict(design)
    for k, v in (changes or {}).items():
        if k == "paleta" and isinstance(v, dict):
            out["paleta"] = {**out.get("paleta", {}), **v}
        elif k == "destaque":
            out["destaque"] = v if v in names else None
        elif k in htmlflyer.DEFAULT:
            out[k] = v
    return htmlflyer.normalize(out)
