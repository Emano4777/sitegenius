"""Interpretador de pedidos do SiteGenius no HostGator (somente biblioteca padrão).

Processo SEPARADO do WhatsApp: não importa, não altera e não reinicia nada do
WhatsApp. Busca tarefas no SiteGenius por HTTPS assinado (só saída) e consulta o
Ollama local (127.0.0.1:11434), uma tarefa por vez.

ATENÇÃO — para não interferir no WhatsApp:
  * No Ollama, cada requisição redefine o keep_alive do modelo e um num_ctx
    diferente força recarregar o modelo. Por isso SG_OLLAMA_KEEP_ALIVE e
    SG_OLLAMA_NUM_CTX são obrigatórios e devem ser IGUAIS aos usados pelo worker
    do WhatsApp quando o modelo for o mesmo.

Variáveis de ambiente:
    SG_BASE_URL, SG_WORKER_ID, SG_WORKER_SECRET
    SG_OLLAMA_URL         padrão http://127.0.0.1:11434
    SG_OLLAMA_MODEL       ex.: gemma4:e4b (tem visão segundo /api/tags)
    SG_OLLAMA_KEEP_ALIVE  obrigatório (ex.: "-1" ou "30m"), igual ao do WhatsApp
    SG_OLLAMA_NUM_CTX     obrigatório, igual ao do WhatsApp
    SG_OLLAMA_NUM_THREAD  obrigatório, igual ao do WhatsApp (hoje 2). Sem ele o llama.cpp abre threads
                          demais para a cota de CPU do ollama-piloto e a geração fica ~0,5 token/s.
    SG_POLL_SECONDS       padrão 5
    SG_INFER_TIMEOUT      padrão 90

Uso:
    python3 hostgator_interpreter.py --check   # valida configuração e lista modelos (não infere)
    python3 hostgator_interpreter.py --once
    python3 hostgator_interpreter.py
"""
import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

log = logging.getLogger("sg_interpreter")

SYSTEM_PROMPT = """Você é o assistente de criação de encartes do SiteGenius.
Converse em português do Brasil, de forma curta e objetiva.
Sua tarefa é atualizar um briefing JSON de encarte promocional a partir da conversa.
Regras obrigatórias:
- NUNCA invente produtos, preços, descontos, datas ou nome de loja. Use apenas o que o usuário escreveu.
- Se faltar preço, deixe "preco" vazio e peça ao usuário.
- "cenario" descreve somente o fundo/ambiente, em inglês, sem texto, sem logos, sem produtos.
- Se houver imagens de referência, descreva o estilo delas em "cenario" (cores, luz, clima). Não copie marcas.
- formato: um de feed_4x5, quadrado, story, a4.
- Preços podem vir sem centavos ("45 reais", "é 55", "de 15 para 10"): converta para "45,00", "55,00", "15,00"/"10,00".
- Condição por quantidade ("leve 2 paga 45", "1 só é 55"): use "preco" = preço de 1 unidade e explique a condição em
  "observacoes" exatamente como o cliente disse. Se não der para saber se o valor é por unidade ou total, pergunte só isso.
- "ean" só se o cliente digitou o código de barras. Nunca invente código.
- Loja, validade, título e cenário são OPCIONAIS. Se o cliente disse que não tem validade ou mandou usar a criatividade,
  NÃO pergunte de novo: decida o visual você mesmo. Pergunte só o que for indispensável (produto ou preço faltando).
- Fotos: o Studio busca fotos na internet quando o cliente pede. Nunca diga que não pode buscar imagens.
- Antes de perguntar algo, confira o "Briefing atual": não peça o que já está preenchido.
- Promoção por quantidade: além de "observacoes", preencha "oferta" do produto, curta (ex.: "Leve 2 por R$ 45,00").
- "gerar": true SOMENTE se o cliente pediu para criar/gerar/fazer a arte AGORA (qualquer forma de dizer). O Studio
  executa a geração de verdade. NUNCA escreva em "resposta" que está gerando/criando: o Studio informa o andamento.
- "layout": se o cliente descreveu como quer a arte organizada, resuma em "layout" (português, curto). Uma imagem de
  referência anexada é analisada pelo Studio: não a transforme em descrição genérica de fundo.
Responda SOMENTE com JSON no formato:
{"resposta": "mensagem curta ao usuário", "gerar": false, "layout": "", "briefing": {"formato": "", "loja": "", "titulo": "", "tema": "", "cenario": "", "validade": "", "observacoes": "", "produtos": [{"nome": "", "ean": "", "preco": "", "preco_de": "", "unidade": "", "oferta": ""}]}}"""


CREATIVE_PROMPT = """Você é o diretor de arte do SiteGenius Studio. Fale em português do Brasil, curto e simpático.
Transforme o pedido do usuário em um prompt para o gerador de imagens (Z-Image Turbo).
Responda SOMENTE com JSON compacto. Campos possíveis (OMITA os vazios, falsos ou não usados — quanto mais curto, melhor):
{"acao": "gerar", "resposta": "", "pergunta": "", "prompt_visual": "", "estilo": "", "proporcao": "",
 "editar_anterior": false, "uso_referencia": "base", "descricao_referencias": "",
 "textos": [{"texto": "", "papel": "", "posicao": ""}], "estilo_texto": "", "efeito_texto": "",
 "tipo_pedido": "nova", "ajuste": {"cena": false, "textos": false, "intensidade": "",
 "operacoes_texto": [{"acao": "", "alvo": "", "papel": "", "texto": "", "posicao": "", "valor": "", "todos": false}]}}
Ex. de criação nova sem textos: {"acao":"gerar","resposta":"Vou criar um farol ao entardecer!","prompt_visual":"...",
"estilo":"foto","tipo_pedido":"nova"}
Regras:
- acao "encarte" só se o usuário pedir encarte/panfleto/tabloide com VÁRIOS produtos e preços. Anúncio, post,
  banner ou arte de UM produto com preço é acao "gerar" (o preço vai em "textos" com papel "preco").
- acao "perguntar" SOMENTE se não for possível saber o assunto principal da imagem. Nunca pergunte estilo,
  formato, cores ou detalhes: escolha você. Em "pergunta", uma pergunta curta e concreta.
- acao "gerar": "prompt_visual" em INGLÊS, 25 a 60 palavras, denso e específico: assunto, ação, ambiente,
  composição, luz, estilo e paleta. Sem frases de enchimento ("the scene should evoke…", "8k", "high detail").
- "resposta": no máximo 15 palavras.
- Não invente marcas, logotipos, preços, telefones ou endereços.
- TEXTOS: o gerador de imagens NÃO escreve letras; o Studio aplica os textos depois, com fontes reais.
  Em "textos", liste cada texto que o usuário quer na arte, copiado EXATAMENTE como ele escreveu (mesma grafia,
  acentos, maiúsculas, pontuação e números), com papel (titulo, subtitulo, descricao, preco, preco_de, cta, marca,
  contato) e posicao (auto, topo, centro, rodape, esquerda, direita, topo_esquerda, topo_direita, base_esquerda,
  base_direita, abaixo_titulo). Sem textos pedidos: "textos": []. Nunca crie textos que o usuário não escreveu.
  Vale também SEM aspas: nome da empresa ("minha empresa Emanuel Tecnologia"), preço ("por 540 mil reais" ->
  "R$ 540 mil"; "19,90" -> "R$ 19,90"), condições ("aceita financiamento") e frases que ele mandou escrever.
  Se ele pedir um texto sem dizer qual ("uma frase de boas-vindas"), NÃO invente: deixe fora de "textos".
  Texto numa superfície ("escrito na parede", "na camiseta", "na placa"): descreva no prompt_visual a superfície
  LISA, vazia e bem visível (sem letras), para o texto ser aplicado nela depois.
- "prompt_visual" NUNCA menciona texto, letras, palavras, títulos, logotipos, placas, cartazes ou preços: descreva só
  a cena, os objetos, a luz e a composição. Se houver textos, deixe área limpa (céu, parede lisa, fundo suave)
  onde eles vão ficar. Não coloque pessoas, a menos que o usuário peça.
- PEDIDO NOVO OU AJUSTE: entenda a intenção, não as palavras exatas. Se existe imagem anterior e a mensagem fala
  da peça atual (mudar, corrigir, aumentar, tirar, mover, trocar cor/fundo/luz, "ficou pequeno", "não gostei do
  fundo"…), é tipo_pedido "ajuste". Se pede outra peça/tema diferente, é "nova". Na dúvida com imagem anterior, "ajuste".
  Exemplos (com imagem anterior que tem os textos "Loja Sol" e "Ofertas da semana"):
  * "o nome ficou pequeno" -> ajuste, cena false, textos true, [{"acao":"aumentar","alvo":"Loja Sol"}]
  * "e se as letras fossem douradas?" -> ajuste, cena false, [{"acao":"cor","valor":"dourado","todos":true}]
  * "tira o texto de baixo" -> ajuste, cena false, [{"acao":"remover","alvo":"Ofertas da semana"}]
  * "não gostei do fundo, deixa mais escuro" -> ajuste, cena true, textos false, intensidade "pequena"
  * "coloca uma praia no fundo e muda pra Promoção de Verão" -> ajuste, cena true, intensidade "grande",
    [{"acao":"trocar","alvo":"Ofertas da semana","texto":"Promoção de Verão"}]
  * "ficou bom, mas quero mais vibrante" -> ajuste, cena true, intensidade "pequena"
  * "coloca o L de loja sol maiúsculo" (texto atual "loja sol") -> [{"acao":"trocar","alvo":"loja sol","texto":"Loja sol"}]
    (maiúsculas, acentos e pontuação: use "trocar" com o texto final exato)
  * "não gostei, quero algo realista" / "troca o fundo" / "faz algo totalmente diferente" -> cena true,
    intensidade "total" (cenário novo; os textos continuam)
  * "agora faz um cartaz de academia com Treine Forte" -> nova (outro assunto)
  * "quero também uma versão para story" -> ajuste, cena true, intensidade "media" (mesma peça, outro formato)
  Em "ajuste":
  * ajuste.cena = true se a ARTE precisa mudar (fundo, luz, cores da imagem, objetos, enquadramento, estilo visual).
  * ajuste.textos = true se os TEXTOS mudam. Mudança só de texto NÃO muda a cena: a mesma arte é reaproveitada.
  * ajuste.intensidade (só se cena): "pequena" (luz, cor, contraste, detalhe), "media" (adicionar/trocar um objeto),
    "grande" (mudar bastante mantendo a ideia), "total" (outro fundo/cenário/estilo, ex.: abstrato -> foto realista).
  * operacoes_texto, uma por mudança: acao = trocar | adicionar | remover | mover | aumentar | diminuir | cor | estilo
    | efeito; alvo = o texto atual exato (da lista "Textos atuais"); papel = o papel do texto; texto = o NOVO texto
    copiado exatamente da mensagem do cliente (só para trocar/adicionar); posicao = nova posição (mover);
    valor = cor pedida, estilo tipográfico ou efeito; todos = true se vale para todos os textos.
  * Em ajuste de cena, escreva o prompt_visual COMPLETO da nova versão partindo do prompt anterior e
    use editar_anterior true. Em ajuste só de texto, repita o prompt anterior.
- estilo_texto: um de moderno, minimalista, elegante, luxo, futurista, tecnologico, impacto, divertido, manuscrito.
  efeito_texto: auto, nenhum, sombra, brilho, contorno, gradiente ou metalico (só se o usuário pedir).
- Imagem de referência: descreva objetivamente o que você vê em "descricao_referencias" e use os detalhes no
  prompt. Se o usuário quer manter a pessoa, objeto, ambiente ou composição da foto: uso_referencia "base".
  Se ela só inspira estilo/cores: "estilo". Se não deve ser usada: "nenhum".
- Se existe imagem anterior e o usuário pede um ajuste (ex.: "deixe mais escuro", "troque o fundo"),
  use editar_anterior true e escreva o prompt_visual COMPLETO da nova versão, partindo do prompt anterior.
- "resposta": uma frase curta e simpática em português, como "Entendi sua ideia! Vou preparar uma cena
  cinematográfica com um astronauta na floresta." Nunca fale de IA, modelos, prompts, servidores ou termos
  técnicos: fale como um designer conversando com o cliente.
- estilo: um de {styles}. proporcao: um de {aspects}, ou "" para manter a atual.
PLANO (obrigatório quando existe imagem anterior; você a VÊ como a primeira imagem, "arte atual"):
 Olhe a arte atual, leia o pedido e o histórico e decida livremente o que preservar, o que mudar e como. Inclua:
 "plano": {"entendimento": "o que o cliente quer, em uma frase",
   "preservar": ["o que fica igual (ex.: símbolo, cores, composição)"],
   "mudar": [{"elemento": "fundo|simbolo|tipografia|texto|cores|composicao|estilo|objeto|luz", "como": "..."}],
   "estrategia": "so_texto|editar_leve|trocar_fundo|reestilizar|recriar",
   "fundo": "só se trocar_fundo: o novo fundo em INGLÊS, sem o assunto principal",
   "textos_finais": [{"texto": "", "papel": "", "posicao": ""}],
   "criterios": ["2 a 4 verificações VISUAIS objetivas do resultado, em português"]}
 Estratégias (escolha pelo efeito visual necessário, não pelas palavras do cliente):
  so_texto = só textos/tipografia mudam, a imagem fica; editar_leve = retoque sutil (luz, tom, detalhe pequeno);
  trocar_fundo = o assunto principal (símbolo, produto, pessoa) fica e o fundo vira OUTRO, de verdade;
  reestilizar = a ideia continua mas o visual muda bastante (cores, estilo, fundo, acabamento);
  recriar = imagem nova do zero (só a marca/textos continuam) — use quando o cliente quer algo diferente, não
  gostou do resultado ou a arte atual não serve. Se o cliente reclamar que nada mudou, use reestilizar ou recriar.
 Pedido de mudança grande NUNCA é editar_leve. "textos_finais" = a lista COMPLETA de textos depois do pedido,
 copiados do que o cliente escreveu (omita o campo se os textos não mudam). Se pedir outra fonte/tipografia,
 escolha estilo_texto DIFERENTE do atual. Com plano, prompt_visual descreve a NOVA imagem completa."""


ADJUST_PROMPT = """Você é o diretor de arte do SiteGenius Studio. Já existe uma arte (a PRIMEIRA imagem, "arte atual").
O cliente mandou uma nova mensagem. Olhe a arte atual, leia o histórico e entenda LIVREMENTE o que ele quer.
Decida o que preservar, o que mudar e a ferramenta certa. Responda SOMENTE com JSON compacto:
{"tipo_pedido": "ajuste" ou "nova",
 "resposta": "frase curta e simpática dizendo o que você VAI fazer (nunca diga que já fez)",
 "prompt_visual": "a NOVA imagem completa em INGLÊS, 25-60 palavras, sem textos/letras/logotipos escritos",
 "estilo": "", "estilo_texto": "", "proporcao": "",
 "plano": {"entendimento": "o que o cliente quer, uma frase",
   "preservar": ["o que fica igual"], "mudar": [{"elemento": "fundo|simbolo|tipografia|texto|cores|composicao|estilo|objeto|luz",
   "como": "..."}],
   "estrategia": "so_texto|editar_leve|trocar_fundo|recolorir|reestilizar|recriar",
   "fundo": "o fundo novo em INGLÊS, sem o elemento principal (se o fundo muda)",
   "cores_novas": ["cores pedidas, da mais escura para a mais clara (nome em português ou #hex)"],
   "textos_finais": [{"texto": "", "papel": "titulo|subtitulo|descricao|preco|cta|marca|contato", "posicao": "auto"}],
   "criterios": ["2 a 4 verificações VISUAIS objetivas, em português"]}}
Estratégias (escolha pelo efeito visual necessário):
 so_texto = só textos/tipografia mudam; editar_leve = retoque sutil (luz, tom, detalhe pequeno);
 trocar_fundo = o elemento principal fica e o fundo vira OUTRO de verdade; reestilizar = mesma ideia, visual bem
 diferente (cores, estilo, fundo, acabamento); recriar = imagem nova do zero mantendo marca/textos (use quando o
 cliente não gostou, reclamou que nada mudou ou pediu algo bem diferente). Mudança grande NUNCA é editar_leve.
 recolorir = trocar a paleta de cores mantendo exatamente formas, símbolo e composição (preencha cores_novas).
 Em reestilizar e recriar, o
 prompt_visual descreve EXPLICITAMENTE cada elemento a preservar (ex.: "a blue cloud icon"), senão ele se perde.
 Critérios: só o que o cliente pediu e o que você decidiu preservar; não invente elementos que não existem.
Textos: o gerador não escreve letras; o Studio aplica os textos com fontes reais. "textos_finais" = a lista COMPLETA de
textos depois do pedido, copiados do que o cliente escreveu (nunca invente). Se ele pediu outra fonte, escolha
"estilo_texto" DIFERENTE do atual entre: moderno, minimalista, elegante, luxo, futurista, tecnologico, impacto,
divertido, manuscrito. "tipo_pedido" "nova" só se ele pediu outra peça sobre outro assunto.
estilo: um de {styles}. proporcao: um de {aspects}, ou "" para manter."""

EVALUATE_PROMPT = """Você confere se uma arte atende ao pedido do cliente. Seja rigoroso e honesto.
Você recebe: a imagem ANTES (se houver) e a imagem DEPOIS, o pedido e os critérios do plano.
Compare de verdade as duas imagens. Uma mudança que não é visível NÃO aconteceu.
Responda SOMENTE com JSON compacto:
{"atendeu": true|false, "nota": 0-10, "mudou": ["o que mudou de fato, curto"], "faltou": ["o que foi pedido e não
aparece, curto"], "resumo": "uma frase para o cliente, em português, sem termos técnicos"}"""


OLLAMA_LOCK = os.getenv("SG_OLLAMA_LOCK", "/opt/sitegenius/ollama.lock")


class ollama_turn:
    """Uma chamada do Studio ao Ollama por vez (intérprete + ferramentas): protege o WhatsApp de chamadas simultâneas.
    flock funciona com o arquivo aberto só para leitura (as units têm o disco protegido)."""

    def __enter__(self):
        self.fd = None
        try:
            import fcntl  # noqa: PLC0415

            self.fd = open(OLLAMA_LOCK, "r")
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except (OSError, ImportError):
            self.fd = None
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            import fcntl  # noqa: PLC0415

            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.fd.close()
        return False


_BREAKER = {"ate": 0.0}


def ollama_healthy(base="http://127.0.0.1:11434"):
    """Disjuntor: se o Ollama não responde em 5 s, o Studio não envia nada por 10 minutos (nunca empilha pedidos
    num Ollama travado; o WhatsApp vem primeiro)."""
    if time.time() < _BREAKER["ate"]:
        return False
    try:
        with urllib.request.urlopen(base + "/api/version", timeout=5) as resp:
            resp.read()
        return True
    except Exception:  # noqa: BLE001
        _BREAKER["ate"] = time.time() + 600
        log.warning("Ollama sem resposta: Studio pausado por 10 minutos")
        return False


def llama_busy(sample=2.0):
    """O modelo está trabalhando (ex.: atendendo o WhatsApp)? Mede a CPU do llama-server em /proc."""
    try:
        clk = os.sysconf("SC_CLK_TCK")
        pids = []
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    with open(f"/proc/{d}/cmdline", "rb") as f:
                        if b"llama-server" in f.read(200):
                            pids.append(d)
                except OSError:
                    continue

        def ticks():
            total = 0
            for pid in pids:
                try:
                    with open(f"/proc/{pid}/stat") as f:
                        parts = f.read().rsplit(")", 1)[1].split()
                    total += int(parts[11]) + int(parts[12])
                except (OSError, IndexError, ValueError):
                    pass
            return total
        a = ticks()
        time.sleep(sample)
        # Ocioso, o llama-server já fica em 60-105% de CPU (medido em 03/11/2026); gerando, 150-180%.
        return (ticks() - a) / (clk * sample) * 100 > 130
    except Exception:  # noqa: BLE001 — sem medição, assume ocupado (prudência)
        return True


def vision_allowed(max_wait=90):
    """Análise de imagem só com o modelo ocioso: espera até max_wait s; se continuar ocupado, a etapa visual é pulada.
    (Duas codificações de imagem simultâneas travaram o Ollama em 02/11/2026.)"""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if not llama_busy():
            return True
        time.sleep(4)
    log.info("modelo ocupado: etapa visual pulada para não disputar com o WhatsApp")
    return False


def sign(secret, timestamp, nonce, method, path, body):
    """Mesmo formato de studio/security.py (canonical_request)."""
    body_hash = hashlib.sha256(body or b"").hexdigest()
    message = f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n{body_hash}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


class SiteGenius:
    def __init__(self, base_url, worker_id, secret):
        parts = urlsplit(base_url)
        if parts.scheme != "https" and parts.hostname not in ("127.0.0.1", "localhost"):
            raise SystemExit("SG_BASE_URL precisa ser https")
        self.base = base_url.rstrip("/")
        self.worker_id = worker_id
        self.secret = secret.encode()

    def call(self, method, path, payload=None, raw=False):
        full_path = "/studio/worker" + path
        body = json.dumps(payload).encode() if payload is not None else b""
        timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
        headers = {
            "X-SG-Worker": self.worker_id,
            "X-SG-Timestamp": timestamp,
            "X-SG-Nonce": nonce,
            "X-SG-Signature": sign(self.secret, timestamp, nonce, method, full_path, body),
            "User-Agent": "SiteGenius-Interpreter/0.1",
        }
        if os.getenv("SG_VERCEL_BYPASS"):  # preview protegido do Vercel
            headers["x-vercel-protection-bypass"] = os.environ["SG_VERCEL_BYPASS"]
        if body:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + full_path, data=body or None, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            if resp.status == 204:
                return None
            return data if raw else json.loads(data)


class Ollama:
    def __init__(self, base_url, model, keep_alive, num_ctx, timeout, num_thread=None):
        host = urlsplit(base_url).hostname
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise SystemExit("SG_OLLAMA_URL deve ser local (127.0.0.1)")
        self.base, self.model, self.timeout = base_url.rstrip("/"), model, timeout
        self.keep_alive, self.num_ctx, self.num_thread = keep_alive, num_ctx, num_thread

    def _post(self, path, payload, timeout):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def tags(self):
        with urllib.request.urlopen(self.base + "/api/tags", timeout=10) as resp:
            return json.loads(resp.read())

    def capabilities(self):
        return self._post("/api/show", {"model": self.model}, 20).get("capabilities", [])

    def chat(self, messages, temperature=0.2, num_predict=700):
        # num_ctx e keep_alive iguais aos do WhatsApp: valor diferente recarregaria o modelo.
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_predict": num_predict, "num_ctx": self.num_ctx},
        }
        if self.num_thread:
            payload["options"]["num_thread"] = self.num_thread
        # Com imagem, a análise visual na CPU leva mais: tempo maior (a imagem já chega reduzida a 768 px).
        has_images = any(m.get("images") for m in messages)
        timeout = max(self.timeout, int(os.getenv("SG_VISION_TIMEOUT", "240"))) if has_images else self.timeout
        if not ollama_healthy(self.base):
            raise RuntimeError("Ollama indisponível (Studio pausado)")
        if has_images and not vision_allowed():  # espera fora do bloqueio (não atrasa outras mensagens)
            for m in payload["messages"]:
                m.pop("images", None)  # segue só com texto: nunca disputa codificação de imagem com o WhatsApp
            has_images = False
            timeout = self.timeout
        with ollama_turn():
            data = self._post("/api/chat", payload, timeout)
        ns = 1e9
        # Métricas reais do Ollama (só log): carga do modelo, leitura do prompt e geração.
        log.info("métricas: carga=%.1fs prompt=%d tok em %.1fs (%.0f tok/s) geração=%d tok em %.1fs (%.1f tok/s)%s",
                 data.get("load_duration", 0) / ns, data.get("prompt_eval_count", 0), data.get("prompt_eval_duration", 0) / ns,
                 data.get("prompt_eval_count", 0) / max(1e-9, data.get("prompt_eval_duration", 0) / ns),
                 data.get("eval_count", 0), data.get("eval_duration", 0) / ns,
                 data.get("eval_count", 0) / max(1e-9, data.get("eval_duration", 0) / ns), " (com imagem)" if has_images else "")
        return data["message"]["content"]


def build_messages(payload, images_b64):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({
        "role": "system",
        "content": "Briefing atual (JSON): " + json.dumps(payload.get("previous_briefing") or {}, ensure_ascii=False)[:6000],
    })
    history = payload.get("history") or []
    for i, item in enumerate(history):
        message = {"role": "user" if item["role"] == "user" else "assistant", "content": item["content"][:4000]}
        if i == len(history) - 1 and item["role"] == "user" and images_b64:
            message["images"] = images_b64
        messages.append(message)
    return messages


def build_creative_messages(payload, images_b64, vision):
    styles = ", ".join(payload.get("styles") or ["auto"])
    aspects = ", ".join(payload.get("aspects") or ["4:5"])
    # Com arte anterior (e o modelo enxergando-a): prompt focado no plano de ajuste.
    base = ADJUST_PROMPT if payload.get("current_image_id") and vision and images_b64 else CREATIVE_PROMPT
    messages = [{"role": "system", "content": base.replace("{styles}", styles).replace("{aspects}", aspects)}]
    context = []
    previous = payload.get("previous_spec") or {}
    if previous.get("prompt_visual"):
        context.append("Prompt anterior: " + previous["prompt_visual"][:1200])
        context.append(f"Estilo anterior: {previous.get('style', 'auto')}; proporção anterior: {previous.get('aspect_ratio', '')}")
    context.append("Existe imagem anterior gerada: " + ("sim" if payload.get("previous_result_id") else "não"))
    if payload.get("current_image_id") and vision and images_b64:
        context.append("A PRIMEIRA imagem enviada é a ARTE ATUAL (resultado anterior); as demais são anexos do cliente.")
    if previous.get("text_plan"):
        context.append("Estilo de texto atual: " + str((previous.get("text_plan") or {}).get("estilo") or "moderno"))
    blocks = (previous.get("text_plan") or {}).get("blocos") or []
    if blocks:
        context.append("Textos atuais da peça: " + json.dumps(
            [{"texto": b.get("texto"), "papel": b.get("papel"), "posicao": b.get("posicao")} for b in blocks],
            ensure_ascii=False))
    options = {k: v for k, v in (payload.get("options") or {}).items() if v and v != "auto"}
    if options:
        context.append("Escolhas feitas pelo usuário na tela (respeite): " + json.dumps(options, ensure_ascii=False))
    if payload.get("image_asset_ids") and not (vision and images_b64):
        context.append("O usuário anexou imagens, mas você NÃO consegue vê-las: não descreva o conteúdo delas.")
    messages.append({"role": "system", "content": "\n".join(context)})
    for item in (payload.get("history") or [])[-8:]:
        messages.append({"role": "user" if item["role"] == "user" else "assistant", "content": item["content"][:2000]})
    last = {"role": "user", "content": (payload.get("text") or "(apenas imagens anexadas)")[:4000]}
    if vision and images_b64:
        last["images"] = images_b64
    if messages[-1]["role"] == "user" and messages[-1]["content"] == last["content"]:
        messages[-1] = last
    else:
        messages.append(last)
    return messages


def process(site, ollama, job, vision):
    payload = job["payload"]
    if payload.get("mode") == "creative":
        return process_creative(site, ollama, job, vision)
    if payload.get("mode") == "evaluate":
        return process_evaluate(site, ollama, job, vision)
    images = []
    if vision:
        for asset_id in (payload.get("image_asset_ids") or [])[:3]:
            images.append(base64.b64encode(site.call("GET", f"/assets/{asset_id}/preview", raw=True)).decode())
    started = time.time()
    content = ollama.chat(build_messages(payload, images))
    log.info("inferência em %.1fs (%d imagem(ns))", time.time() - started, len(images))
    try:
        data = json.loads(content)
    except ValueError:
        raise RuntimeError("modelo não retornou JSON válido")
    if not isinstance(data, dict):
        raise RuntimeError("resposta fora do formato")
    # O servidor revalida tudo (inclusive descarta preço não digitado pelo usuário).
    site.call("POST", f"/jobs/{job['id']}/complete", {"resposta": data.get("resposta", ""), "briefing": data.get("briefing", {}),
                                                     "gerar": data.get("gerar") is True, "layout": data.get("layout") or ""})


def process_evaluate(site, ollama, job, vision):
    """Avaliação visual do resultado: antes x depois x pedido. O servidor decide o que fazer com o veredito."""
    payload = job["payload"]
    base = getattr(ollama, "base", None)  # None = modelo simulado (testes)
    if not vision or (base and not ollama_healthy(base)):
        site.call("POST", f"/jobs/{job['id']}/complete", {"avaliacao": None})
        return
    idle = vision_allowed() if base else True
    if not idle:  # modelo ocupado: sem conferência visual agora (a resposta diz isso), nunca veredito inventado
        site.call("POST", f"/jobs/{job['id']}/complete", {"avaliacao": None})
        return
    images = []
    for key in ("before_id", "after_id"):
        if payload.get(key):
            images.append(base64.b64encode(site.call("GET", f"/assets/{payload[key]}/preview", raw=True)).decode())
    order = "Imagem 1 = ANTES, imagem 2 = DEPOIS." if len(images) == 2 else "A imagem é o resultado (DEPOIS)."
    content = (f"{order}\nPedido do cliente: {payload.get('pedido', '')[:800]}\n"
               f"Critérios: {json.dumps(payload.get('criterios') or [], ensure_ascii=False)[:800]}\n"
               f"Medição automática da mudança da imagem (0 a 1): {payload.get('mudanca', 'n/d')}")
    started = time.time()
    raw = ollama.chat([{"role": "system", "content": EVALUATE_PROMPT},
                       {"role": "user", "content": content, "images": images}], temperature=0.1, num_predict=300)
    log.info("avaliação em %.1fs", time.time() - started)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    site.call("POST", f"/jobs/{job['id']}/complete", {"avaliacao": data if isinstance(data, dict) else None})


def process_creative(site, ollama, job, vision):
    payload = job["payload"]
    images = []
    if vision and getattr(ollama, "base", None) and (payload.get("current_image_id") or payload.get("image_asset_ids"))             and not vision_allowed():
        vision = False  # modelo ocupado: segue sem imagem e com o prompt sem visão (não finge que viu a arte)
    if vision:
        if payload.get("current_image_id"):  # a arte atual primeiro: o modelo vê o que vai mudar
            images.append(base64.b64encode(site.call("GET", f"/assets/{payload['current_image_id']}/preview",
                                                     raw=True)).decode())
        for asset_id in (payload.get("image_asset_ids") or [])[:2]:
            images.append(base64.b64encode(site.call("GET", f"/assets/{asset_id}/preview", raw=True)).decode())
    started = time.time()
    content = ollama.chat(build_creative_messages(payload, images, vision), temperature=0.3, num_predict=1100)
    log.info("criação: inferência em %.1fs (%d imagem(ns))", time.time() - started, len(images))
    try:
        data = json.loads(content)
    except ValueError:
        raise RuntimeError("modelo não retornou JSON válido")
    if not isinstance(data, dict):
        raise RuntimeError("resposta fora do formato")
    # O servidor valida tudo (normalize_model_answer): estilo, proporção, texto na imagem, referências.
    site.call("POST", f"/jobs/{job['id']}/complete", {"modelo": data})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    required = ["SG_BASE_URL", "SG_WORKER_ID", "SG_WORKER_SECRET", "SG_OLLAMA_MODEL", "SG_OLLAMA_KEEP_ALIVE", "SG_OLLAMA_NUM_CTX",
                "SG_OLLAMA_NUM_THREAD"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit("Defina: " + ", ".join(missing))
    keep_alive = os.environ["SG_OLLAMA_KEEP_ALIVE"]
    keep_alive = int(keep_alive) if keep_alive.lstrip("-").isdigit() else keep_alive
    site = SiteGenius(os.environ["SG_BASE_URL"], os.environ["SG_WORKER_ID"], os.environ["SG_WORKER_SECRET"])
    ollama = Ollama(
        os.getenv("SG_OLLAMA_URL", "http://127.0.0.1:11434"),
        os.environ["SG_OLLAMA_MODEL"],
        keep_alive,
        int(os.environ["SG_OLLAMA_NUM_CTX"]),
        int(os.getenv("SG_INFER_TIMEOUT", "90")),
        int(os.environ["SG_OLLAMA_NUM_THREAD"]),
    )
    models = [m["name"] for m in ollama.tags().get("models", [])]
    if ollama.model not in models:
        raise SystemExit(f"Modelo {ollama.model} não instalado. Disponíveis: {', '.join(models)}")
    vision = "vision" in ollama.capabilities()
    log.info("modelo %s; visão: %s", ollama.model, "sim" if vision else "não (imagens serão ignoradas)")
    if args.check:
        log.info("Configuração OK (nenhuma inferência feita).")
        return 0

    poll = int(os.getenv("SG_POLL_SECONDS", "5"))
    while True:
        job = None
        try:
            job = site.call("POST", "/claim", {})
            job = job["job"] if job else None
            if job:
                process(site, ollama, job, vision)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001
            log.error("erro: %s", exc)
            if job:
                try:
                    site.call("POST", f"/jobs/{job['id']}/fail", {"erro": str(exc)[:300]})
                except (urllib.error.URLError, OSError):
                    log.warning("não foi possível reportar a falha")
        if args.once:
            return 0
        if not job:
            time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())
