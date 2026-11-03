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

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((768, 768))
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
    with urllib.request.urlopen(req, timeout=int(os.getenv("SG_VISION_TIMEOUT", "240"))) as resp:
        data = json.loads(resp.read())
    try:
        out = json.loads(data["message"]["content"])
        return out if isinstance(out, dict) else None
    except (ValueError, KeyError):
        return None


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
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-zygote", "--disable-gpu",
                                                          "--js-flags=--max-old-space-size=256"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="pt-BR")
            page.set_default_timeout(20000)
            for item in (payload.get("produtos") or [])[:8]:
                name = item["nome"]
                cands, n = search_product(page, name)
                notes += [f"{name} — {x}" for x in n]
                chosen = []
                for c in cands:
                    level, motivo = productmatch.evaluate(name, c["titulo"])
                    if level == "descartar" or not c["imagem"].startswith("http") or not allowed(c["imagem"]):
                        continue
                    try:
                        data = _download(c["imagem"])
                    except Exception:  # noqa: BLE001
                        continue
                    if products.looks_like_barcode(data):
                        continue
                    chosen.append({**c, "nivel": level, "motivo": motivo,
                                   "dados": base64.b64encode(data).decode()})
                    if len(chosen) >= 3:
                        break
                results.append({"produto": name, "opcoes": chosen})
        finally:
            browser.close()
    for r in results:  # uma imagem por chamada (limite de corpo do servidor)
        for opt in r["opcoes"]:
            resp = site.call("POST", f"/jobs/{job['id']}/candidate", {"produto": r["produto"], **opt}, timeout=90)
            opt["asset_id"] = (resp or {}).get("asset_id")
            opt.pop("dados", None)
    site.call("POST", f"/jobs/{job['id']}/complete", {"websearch": results, "notas": notes[:20]})


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
                elif mode == "websearch":
                    process_websearch(site, job)
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
