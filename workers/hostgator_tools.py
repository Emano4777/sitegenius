"""Ferramentas do SiteGenius Studio na HostGator (processo isolado, limitado pelo systemd).

Tarefas (fila do servidor, só as marcadas como ferramenta):
  - layout_ref: análise visual de uma arte de referência -> especificação de layout
      1) OpenCV + OCR (studio/refstyle.py): medidas, cores, faixas, preços, centavos, selos;
      2) leitura visual do modelo já carregado no Ollama (gemma4, com visão): organização, hierarquia, estilo.
     As medições valem mais que a leitura do modelo quando os dois discordam.
  - websearch: busca de fotos de produto em sites que permitem acesso automatizado (robots.txt), com Chromium
    headless (Playwright) aberto só durante a tarefa. Sem contornar bloqueios: CAPTCHA/403 = fonte ignorada.

Uso: python -m workers.hostgator_tools [--check] [--once]
Variáveis: as mesmas do intérprete (SG_BASE_URL, SG_WORKER_ID, SG_WORKER_SECRET, SG_OLLAMA_*).
"""
import argparse
import base64
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

log = logging.getLogger("sitegenius-tools")
UA = "SiteGeniusStudio/2.6 (+https://www.sitegenius.com.br/studio)"
MAX_IMAGE = 6 * 1024 * 1024

VISION_PROMPT = """Analise a ORGANIZAÇÃO VISUAL desta arte de ofertas (não leia preços, nomes ou marcas).
Responda SOMENTE com JSON compacto:
{"organizacao": "grade|tabloide|diagonal|zigue|assimetrica|livre|destaque",
 "estilo_geral": "tabloide_supermercado|farmacia_clean|premium|moderno_minimalista|outro",
 "hierarquia": "todos_iguais|um_destaque|tamanhos_variados",
 "descricao": "uma frase sobre como os elementos estão organizados (cabeçalho, faixas, produtos, preços, selos)"}"""


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
    body_hash = hashlib.sha256(body or b"").hexdigest()
    message = f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n{body_hash}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


class Site:
    def __init__(self, base_url, worker_id, secret):
        if urlsplit(base_url).scheme != "https":
            raise SystemExit("SG_BASE_URL precisa ser https")
        self.base, self.worker_id, self.secret = base_url.rstrip("/"), worker_id, secret.encode()

    def call(self, method, path, payload=None, raw=False, timeout=60):
        full = "/studio/worker" + path
        body = json.dumps(payload).encode() if payload is not None else b""
        ts, nonce = str(int(time.time())), secrets.token_hex(16)
        headers = {"X-SG-Worker": self.worker_id, "X-SG-Timestamp": ts, "X-SG-Nonce": nonce,
                   "X-SG-Signature": sign(self.secret, ts, nonce, method, full, body), "User-Agent": "SiteGenius-Tools/1.0"}
        if body:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + full, data=body or None, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if resp.status == 204:
                return None
            return data if raw else json.loads(data)


def ollama_vision(image_bytes, prompt):
    """Leitura visual com o modelo já carregado (mesmos parâmetros do intérprete: não recarrega o modelo)."""
    from PIL import Image  # noqa: PLC0415

    if not ollama_healthy():
        return None
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((512, 512))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    keep = os.environ.get("SG_OLLAMA_KEEP_ALIVE", "30m")
    payload = {"model": os.environ["SG_OLLAMA_MODEL"], "stream": False, "format": "json", "think": False,
               "keep_alive": int(keep) if keep.lstrip("-").isdigit() else keep,
               "options": {"temperature": 0.1, "num_predict": 300, "num_ctx": int(os.environ["SG_OLLAMA_NUM_CTX"]),
                           "num_thread": int(os.environ.get("SG_OLLAMA_NUM_THREAD", "2"))},
               "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(buf.getvalue()).decode()]}]}
    url = os.getenv("SG_OLLAMA_URL", "http://127.0.0.1:11434")
    if urlsplit(url).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("SG_OLLAMA_URL deve ser local")
    req = urllib.request.Request(url + "/api/chat", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    if not vision_allowed():
        return None
    with ollama_turn():
        with urllib.request.urlopen(req, timeout=int(os.getenv("SG_VISION_TIMEOUT", "180"))) as resp:
            data = json.loads(resp.read())
    try:
        out = json.loads(data["message"]["content"])
        return out if isinstance(out, dict) else None
    except (ValueError, KeyError):
        return None


def ollama_chat(messages, images=None, num_predict=500, temperature=0.4):
    """Chamada ao modelo já carregado (mesmos parâmetros do intérprete). images vai na última mensagem."""
    keep = os.environ.get("SG_OLLAMA_KEEP_ALIVE", "30m")
    if not ollama_healthy():
        return None
    msgs = [dict(m) for m in messages]
    if images:
        msgs[-1]["images"] = [_shrink_b64(i) for i in images]
    payload = {"model": os.environ["SG_OLLAMA_MODEL"], "stream": False, "format": "json", "think": False,
               "keep_alive": int(keep) if keep.lstrip("-").isdigit() else keep,
               "options": {"temperature": temperature, "num_predict": num_predict,
                           "num_ctx": int(os.environ["SG_OLLAMA_NUM_CTX"]),
                           "num_thread": int(os.environ.get("SG_OLLAMA_NUM_THREAD", "2"))},
               "messages": msgs}
    url = os.getenv("SG_OLLAMA_URL", "http://127.0.0.1:11434")
    req = urllib.request.Request(url + "/api/chat", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    if images and not vision_allowed():
        return None
    with ollama_turn():
        with urllib.request.urlopen(req, timeout=int(os.getenv("SG_VISION_TIMEOUT", "180"))) as resp:
            data = json.loads(resp.read())
    try:
        out = json.loads(data["message"]["content"])
        return out if isinstance(out, dict) else None
    except (ValueError, KeyError):
        return None


def _shrink_b64(b64):
    """Imagem para o modelo com no máximo 512 px (codificação mais curta e previsível)."""
    from PIL import Image  # noqa: PLC0415

    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    img.thumbnail((512, 512))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def render_html(page, html_doc, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.set_content(html_doc, wait_until="load", timeout=30000)
    try:
        page.wait_for_selector("body[data-ready='1']", timeout=10000)
    except Exception:  # noqa: BLE001 — sem o ajuste de texto, a captura ainda vale
        pass
    return page.screenshot(type="jpeg", quality=92, clip={"x": 0, "y": 0, "width": size[0], "height": size[1]})


def process_flyer_design(site, job):
    """Direção de arte: plano do modelo -> montagem no Chromium -> avaliação estética (visão) -> até 2 refinamentos."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    from studio import artdirector, htmlflyer  # noqa: PLC0415

    payload = job["payload"]
    briefing = payload["briefing"]
    names = [p["nome"] for p in briefing.get("produtos") or []]
    photos = {}
    for p in briefing.get("produtos") or []:
        if p.get("asset_id"):
            try:
                photos[p["nome"]] = htmlflyer.photo_data_uri(site.call("GET", f"/assets/{p['asset_id']}", raw=True, timeout=90))
            except Exception as exc:  # noqa: BLE001
                log.warning("foto de %s indisponível: %s", p["nome"], exc)
    ref = payload.get("referencia")
    base = artdirector.design_from_reference(ref) if ref else htmlflyer.normalize({})
    t0 = time.time()
    planned = None
    try:
        planned = ollama_chat(artdirector.design_messages(briefing, ref, payload.get("instrucoes") or "",
                                                          payload.get("design_anterior"), payload.get("semente") or 1),
                              num_predict=450, temperature=0.6)
    except Exception as exc:  # noqa: BLE001
        log.warning("plano do design falhou: %s", exc)
    design = artdirector.merge(base, planned or {}, names)
    prev = payload.get("design_anterior") or {}
    if prev.get("composicao") and design.get("composicao") in (prev.get("composicao"), "auto"):
        # Nova versão tem de ser OUTRA composição (o modelo às vezes repete a anterior).
        options = [c for c in artdirector.compositions_for(len(photos)) if c != prev.get("composicao")]
        design["composicao"] = options[(int(payload.get("semente") or 1)) % len(options)]
    for k in ("paleta",):  # com referência, as cores medidas valem mais que as sugeridas
        if ref and base.get(k):
            design[k] = {**design[k], **{c: base[k][c] for c in ("primaria", "secundaria")}}
    size = htmlflyer.SIZES.get(briefing.get("formato"), htmlflyer.SIZES["feed_4x5"])
    report = {"plano_s": round(time.time() - t0, 1), "rodadas": []}
    best = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-zygote", "--disable-gpu"])
        try:
            page = browser.new_page()
            for round_no in range(2):  # no máximo 2 avaliações estéticas (o modelo é compartilhado)
                image = render_html(page, htmlflyer.build(briefing, photos, design), size)
                critique = None
                try:
                    t1 = time.time()
                    critique = ollama_chat([{"role": "system", "content": artdirector.CRITIQUE_PROMPT},
                                            {"role": "user", "content": "Pedido do cliente: " + (payload.get("instrucoes") or
                                                                                                "encarte profissional")
                                             + (". Produtos SEM foto disponível (aparecem só com nome e preço, de "
                                                "propósito; não peça fotos, avalie a composição): "
                                                + ", ".join(n for n in names if n not in photos) if len(photos) < len(names)
                                                else "")}],
                                           images=[base64.b64encode(image).decode()], num_predict=300, temperature=0.2)
                    report["rodadas"].append({"nota": (critique or {}).get("nota"), "s": round(time.time() - t1, 1),
                                              "problemas": (critique or {}).get("problemas", [])[:4]})
                except Exception as exc:  # noqa: BLE001
                    log.warning("avaliação estética falhou: %s", exc)
                try:
                    score = float((critique or {}).get("nota"))
                except (TypeError, ValueError):
                    score = 6.0
                if best is None or score > best[0]:
                    best = (score, image, dict(design), critique)
                if score >= 8 or round_no == 1 or not (critique or {}).get("ajustes"):
                    break
                design = artdirector.merge(design, critique["ajustes"], names)
        finally:
            browser.close()
    score, image, design, critique = best
    log.info("encarte: nota %.1f após %d rodada(s)", score, len(report["rodadas"]))
    site.call("POST", f"/jobs/{job['id']}/complete", {"imagem": base64.b64encode(image).decode(), "design": design,
                                                      "nota": score, "problemas": (critique or {}).get("problemas", [])[:4],
                                                      "relatorio": report}, timeout=120)


def process_layout_ref(site, job):
    from studio import refstyle  # noqa: PLC0415

    payload = job["payload"]
    specs, reports = [], []
    for asset_id in (payload.get("asset_ids") or [payload["asset_id"]])[:3]:  # até 3 referências, uma de cada vez
        image = site.call("GET", f"/assets/{asset_id}", raw=True, timeout=90)
        started = time.time()
        spec, report = refstyle.analyze(image)
        report["medicao_s"] = round(time.time() - started, 1)
        visual = None
        try:
            t = time.time()
            visual = ollama_vision(image, VISION_PROMPT)
            report["visao_s"] = round(time.time() - t, 1)
        except Exception as exc:  # noqa: BLE001 — sem a leitura do modelo, as medições continuam valendo
            log.warning("leitura visual falhou: %s", exc)
        if spec is not None and visual:
            spec["visual"] = {k: visual.get(k) for k in ("organizacao", "estilo_geral", "hierarquia")}
            spec["visual"]["descricao"] = str(visual.get("descricao") or "")[:200]
        specs.append(spec)
        reports.append(report)
        log.info("referência analisada: %s", report)
    site.call("POST", f"/jobs/{job['id']}/complete", {"layout_refs": specs, "layout_ref": specs[0] if specs else None,
                                                      "relatorio": reports})


# ------------------------------------------------------------------------------------------- busca web
_robots = {}


def allowed(url):
    """robots.txt do site permite? (cache por host; 401/403 no robots = site bloqueia robôs)."""
    parts = urlsplit(url)
    host = f"{parts.scheme}://{parts.netloc}"
    rp = _robots.get(host)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(host + "/robots.txt", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10) as resp:
                rp.parse(resp.read().decode("utf-8", "replace").splitlines())
        except urllib.error.HTTPError as exc:
            rp.disallow_all = exc.code in (401, 403)
            rp.allow_all = exc.code >= 404 and exc.code not in (401, 403)
        except Exception:  # noqa: BLE001
            rp.disallow_all = True
        _robots[host] = rp
    return rp.can_fetch(UA, url)


SOURCES = [
    ("Mercado Livre", "https://lista.mercadolivre.com.br/{slug}"),
    ("Carrefour", "https://mercado.carrefour.com.br/busca/{q}"),
    ("DuckDuckGo", "https://duckduckgo.com/?q={q}&iax=images&ia=images"),
]
BLOCK_MARKERS = re.compile(r"captcha|are you a robot|acesso negado|access denied|unusual traffic|verify you are human|"
                           r"verificando se voc|hubo un error|challenges.cloudflare|turnstile|um momento", re.I)


def _download(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        raise ValueError("imagem grande demais")
    return data


SEARX_URL = os.getenv("SG_SEARX_URL", "http://127.0.0.1:8899")


def searx(query, category="general", timeout=15):
    """Busca no SearXNG local (sitegenius-busca, só 127.0.0.1). Motores que bloqueiam ficam de fora (sem contorno)."""
    url = SEARX_URL + "/search?" + urllib.parse.urlencode({"q": query, "format": "json", "categories": category, "language": "pt-BR"})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as resp:
            data = json.loads(resp.read(3_000_000))
        return data.get("results") or [], [f"{e[0]}: {e[1]}" for e in data.get("unresponsive_engines") or [] if isinstance(e, list)]
    except Exception as exc:  # noqa: BLE001
        return [], [f"busca local: {type(exc).__name__}"]


def gtin_valid(code):
    """Dígito verificador de EAN-8/UPC-12/EAN-13/GTIN-14."""
    if not re.fullmatch(r"\d{8}|\d{12,14}", code or ""):
        return False
    digits = [int(c) for c in code]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10 == check


def search_product_searx(query, ean=None, limit=16):
    """Candidatos de imagem pelo SearXNG: {titulo, imagem, pagina, fonte, resolucao, ean_confere}."""
    out, notes = [], []
    queries = [query] + ([f"{query} {ean}"] if ean and gtin_valid(ean) else [])
    for q in queries:
        results, n = searx(q, "images")
        notes += n
        for r in results:
            img = r.get("img_src") or ""
            if not img.startswith("http") or any(o["imagem"] == img for o in out):
                continue
            m = re.match(r"(\d+)\s*[x×]\s*(\d+)", r.get("resolution") or "")
            if m and min(int(m.group(1)), int(m.group(2))) < 300:
                continue  # resolução baixa demais para arte
            text = " ".join(str(r.get(k) or "") for k in ("title", "content", "url", "img_src"))
            out.append({"titulo": (r.get("title") or "").strip(), "imagem": img, "pagina": r.get("url") or "",
                        "fonte": urlsplit(r.get("url") or img).netloc, "resolucao": r.get("resolution") or "",
                        "ean_confere": bool(ean) and ean in text})
            if len(out) >= limit:
                break
    out.sort(key=lambda c: not c["ean_confere"])  # EAN confirmado primeiro
    return out, sorted(set(notes))[:6]


def search_product(page, query, per_source=8, deadline=45):
    """Candidatos {titulo, imagem, pagina, fonte} de fontes permitidas. Nunca contorna bloqueio."""
    from studio import productmatch  # noqa: PLC0415

    q = urllib.parse.quote_plus(query)
    slug = re.sub(r"[^a-z0-9]+", "-", productmatch.fold(query)).strip("-")
    out, notes, t0 = [], [], time.time()
    for name, pattern in SOURCES:
        if time.time() - t0 > deadline:
            notes.append(f"{name}: tempo esgotado")
            break
        url = pattern.format(q=q, slug=slug)
        if not allowed(url):
            notes.append(f"{name}: robots.txt não permite")
            continue
        captured = []
        if name == "DuckDuckGo":
            def on_resp(resp):
                if "/i.js" in resp.url:
                    try:
                        captured.extend(resp.json().get("results") or [])
                    except Exception:  # noqa: BLE001
                        pass
            page.on("response", on_resp)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)
            html = page.content()
            if BLOCK_MARKERS.search(html[:20000]):
                notes.append(f"{name}: bloqueou o acesso automático (respeitado)")
                continue
            if name == "DuckDuckGo":
                for r in captured[:per_source]:
                    out.append({"titulo": r.get("title") or "", "imagem": r.get("image") or "", "pagina": r.get("url") or "",
                                "fonte": urlsplit(r.get("url") or "").netloc or name})
            else:
                items = page.evaluate("""() => Array.from(document.querySelectorAll('li, article, div[class*=product]'))
                    .slice(0, 400).map(el => {
                        const img = el.querySelector('img'); const a = el.querySelector('a[href]');
                        const t = el.querySelector('h2, h3, [class*=title], [class*=name]');
                        return img && a && t ? {titulo: t.innerText.trim(), imagem: img.currentSrc || img.src || img.dataset.src || '',
                                               pagina: a.href} : null; }).filter(Boolean)""")
                seen = set()
                for it in items:
                    if it["imagem"].startswith("http") and it["titulo"] and it["imagem"] not in seen:
                        seen.add(it["imagem"])
                        it["imagem"] = re.sub(r"-[A-Z]\.(webp|jpg)$", r"-O.\1", it["imagem"])  # ML: versão maior
                        out.append({**it, "fonte": name})
                    if len([o for o in out if o["fonte"] == name]) >= per_source:
                        break
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{name}: {type(exc).__name__}")
        finally:
            if name == "DuckDuckGo":
                page.remove_listener("response", on_resp)
    return out, notes


def process_websearch(site, job):
    """Para cada produto: busca, confere tipo/marca/variante/apresentação e envia até 3 opções (o cliente escolhe)."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    from studio import productmatch, products  # noqa: PLC0415

    payload = job["payload"]
    results, notes = [], []

    def choose(name, cands, chosen):
        for c in cands:
            level, motivo = productmatch.evaluate(name, c["titulo"])
            if c.get("ean_confere") and level != "descartar":
                level, motivo = "exato", "EAN confere"
            if level == "descartar" or not c["imagem"].startswith("http") or not allowed(c["imagem"]):
                continue
            key = productmatch.fold(c.get("titulo") or "")
            if any(o["imagem"] == c["imagem"] or (c.get("pagina") and o.get("pagina") == c["pagina"])
                   or (key and productmatch.fold(o.get("titulo") or "") == key and o.get("fonte") == c.get("fonte")) for o in chosen):
                continue  # mesma foto, mesma página ou mesmo anúncio repetido: uma opção só
            try:
                data = _download(c["imagem"])
            except Exception:  # noqa: BLE001
                continue
            if products.looks_like_barcode(data):
                continue
            chosen.append({**c, "nivel": level, "motivo": motivo, "dados": base64.b64encode(data).decode()})
            if len(chosen) >= 3:
                break
        return chosen

    pending = []
    for item in (payload.get("produtos") or [])[:8]:  # 1) busca local (SearXNG): rápida, sem navegador
        name = item["nome"] if isinstance(item, dict) else str(item)
        ean = item.get("ean") if isinstance(item, dict) else None
        cands, n = search_product_searx(name, ean)
        notes += [f"{name} — {x}" for x in n]
        chosen = choose(name, cands, [])
        results.append({"produto": name, "opcoes": chosen})
        if len(chosen) < 2:
            pending.append(results[-1])
    if pending:  # 2) reserva: fontes diretas com navegador, só para o que faltou
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-zygote", "--disable-gpu",
                                                              "--js-flags=--max-old-space-size=256"])
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="pt-BR")
                page.set_default_timeout(20000)
                for r in pending:
                    cands, n = search_product(page, r["produto"])
                    notes += [f"{r['produto']} — {x}" for x in n]
                    r["opcoes"] = choose(r["produto"], cands, r["opcoes"])
            finally:
                browser.close()
    for r in results:  # uma imagem por chamada (limite de corpo do servidor)
        for opt in r["opcoes"]:
            resp = site.call("POST", f"/jobs/{job['id']}/candidate", {"produto": r["produto"], **opt}, timeout=90)
            opt["asset_id"] = (resp or {}).get("asset_id")
            opt.pop("dados", None)
    site.call("POST", f"/jobs/{job['id']}/complete", {"websearch": results, "notas": notes[:20]})


# ------------------------------------------------------------------------------------- pesquisa geral
def public_url(url):
    """Só endereços públicos: nunca 127.0.0.1 (Ollama, SearXNG), rede interna ou metadados da máquina."""
    import ipaddress  # noqa: PLC0415
    import socket  # noqa: PLC0415

    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_url(newurl) or not allowed(newurl):
            raise urllib.error.HTTPError(newurl, 403, "redirecionamento recusado", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_PAGE_OPENER = urllib.request.build_opener(_PublicRedirects)
MAX_PAGE = 2_000_000


def _terms(text):
    from studio import productmatch  # noqa: PLC0415

    return {w for w in productmatch.fold(text).split() if len(w) > 3}


def extract_page(url, query, limit=900):
    """Baixa a página (robots.txt, só endereço público, até 2 MB, 10 s) e extrai o texto principal com o Trafilatura.
    Devolve (trecho mais relevante para a consulta, data, status)."""
    if not public_url(url):
        return "", "", "endereço não público"
    if not allowed(url):
        return "", "", "robots.txt não permite"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
        with _PAGE_OPENER.open(req, timeout=10) as resp:
            if "html" not in (resp.headers.get("Content-Type") or "html"):
                return "", "", "não é página"
            raw = resp.read(MAX_PAGE)
    except urllib.error.HTTPError as exc:
        return "", "", f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return "", "", type(exc).__name__
    try:
        import trafilatura  # noqa: PLC0415

        text = trafilatura.extract(raw, url=url, include_comments=False, include_tables=False, favor_precision=True) or ""
        meta = trafilatura.extract_metadata(raw)
        when = (getattr(meta, "date", None) or "") if meta else ""
    except ImportError:
        html = raw.decode("utf-8", "ignore")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", " ".join(re.findall(r"<p[^>]*>(.*?)</p>", html, re.S | re.I))))
        when = ""
    paras = [p.strip() for p in re.split(r"\n+", text) if len(p.strip()) > 60]
    if not paras:
        return "", when, "sem texto"
    q = _terms(query)
    ranked = sorted(range(len(paras)), key=lambda i: (-len(q & _terms(paras[i])), i))
    chosen, size = [], 0
    for i in ranked:
        if size > limit:
            break
        chosen.append(i)
        size += len(paras[i])
    return " ".join(paras[i] for i in sorted(chosen))[:limit + 300], str(when)[:10], "ok"


def process_research(site, job):
    """Pesquisa geral: SearXNG local -> páginas permitidas -> trechos extraídos (dados, não instruções) -> resumo do
    modelo com citações, só com o modelo ocioso (o WhatsApp vem primeiro). O servidor confere tudo de novo."""
    from studio import research  # noqa: PLC0415

    payload = job["payload"]
    query = str(payload.get("consulta") or "")[:160]
    question = str(payload.get("pergunta") or query)[:400]
    t0 = time.time()
    results, notes = searx(query, "news" if payload.get("tipo") == "noticias" else "general")
    if payload.get("tipo") == "noticias" and len(results) < 3:
        more, n2 = searx(query, "general")
        results, notes = results + more, notes + n2
    t_search = time.time() - t0
    sources, hosts = [], set()
    for r in results:
        url = r.get("url") or ""
        host = urlsplit(url).hostname or ""
        if not url.startswith("http") or host in hosts:
            continue  # um resultado por site: fontes variadas
        hosts.add(host)
        trecho, when, status = extract_page(url, query)
        if status != "ok":
            notes.append(f"{host}: {status}")
            trecho = str(r.get("content") or "")[:400]  # sem extração: o resumo do próprio buscador
        sources.append({"titulo": r.get("title") or host, "url": url, "data": when or str(r.get("publishedDate") or "")[:10],
                        "trecho": trecho, "extraido": status == "ok", "motor": r.get("engine") or ""})
        if len([s for s in sources if s["extraido"]]) >= 4 or len(sources) >= 6 or time.time() - t0 > 45:
            break
    t_extract = time.time() - t0 - t_search
    clean = research.clean_sources(sources)
    answer, t_model = None, 0.0
    if clean and vision_allowed(max_wait=45):  # mesmo critério de ociosidade da leitura visual
        t1 = time.time()
        try:
            answer = ollama_chat(research.prompt_messages(question, clean[:4]), num_predict=320, temperature=0.2)
        except Exception as exc:  # noqa: BLE001 — sem resumo do modelo, a resposta sai dos trechos
            notes.append(f"resumo: {type(exc).__name__}")
        t_model = time.time() - t1
    elif clean:
        notes.append("resumo: modelo ocupado (resposta montada dos trechos)")
    site.call("POST", f"/jobs/{job['id']}/complete", {
        "pesquisa": {"consulta": query, "fontes": sources, "resposta_modelo": answer if isinstance(answer, dict) else None,
                     "notas": sorted(set(notes))[:20],
                     "tempos": {"busca_s": round(t_search, 1), "extracao_s": round(t_extract, 1), "resumo_s": round(t_model, 1)}}})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    missing = [n for n in ("SG_BASE_URL", "SG_WORKER_ID", "SG_WORKER_SECRET", "SG_OLLAMA_MODEL", "SG_OLLAMA_NUM_CTX")
               if not os.getenv(n)]
    if missing:
        raise SystemExit("Defina: " + ", ".join(missing))
    site = Site(os.environ["SG_BASE_URL"], os.environ["SG_WORKER_ID"], os.environ["SG_WORKER_SECRET"])
    if args.check:
        from studio import ocr, refstyle  # noqa: F401,PLC0415

        log.info("leitura de texto: %s", "ok" if ocr.engine() is not None else "INDISPONÍVEL")
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415

            with sync_playwright() as pw:
                b = pw.chromium.launch(headless=True)
                b.close()
            log.info("navegador: ok")
        except Exception as exc:  # noqa: BLE001
            log.warning("navegador indisponível: %s", exc)
        return 0
    poll = int(os.getenv("SG_POLL_SECONDS", "3"))
    while True:
        job = None
        try:
            job = site.call("POST", "/claim", {"capacidades": ["tools"], "somente": ["tools"]})
            job = job["job"] if job else None
            if job:
                mode = job["payload"].get("mode")
                started = time.time()
                if mode == "layout_ref":
                    process_layout_ref(site, job)
                elif mode == "flyer_design":
                    process_flyer_design(site, job)
                elif mode == "websearch":
                    process_websearch(site, job)
                elif mode == "research":
                    process_research(site, job)
                else:
                    raise RuntimeError(f"tarefa desconhecida: {mode}")
                log.info("%s concluída em %.1fs", mode, time.time() - started)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001
            log.error("erro: %s", exc)
            if job:
                try:
                    site.call("POST", f"/jobs/{job['id']}/fail", {"erro": str(exc)[:300]})
                except (urllib.error.URLError, OSError):
                    pass
        if args.once:
            return 0
        if not job:
            time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())
