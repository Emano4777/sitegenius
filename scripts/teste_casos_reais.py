"""Testes reais do SiteGenius Studio pelo Preview (precisa do Acer rodando o agente).

Executa os 3 casos do plano, espera as gerações, baixa as imagens e imprime os tempos medidos:
  (a) cena livre: "astronauta em floresta cinematográfica"
  (b) anúncio imobiliário com referência (img2img), sem preço
  (c) encarte com produto e preço reais (composição determinística)

Variáveis de ambiente (nunca versionar):
    SG_TEST_BASE_URL   padrão https://sitegenius-studio-preview.vercel.app
    SG_VERCEL_BYPASS   token "Protection Bypass for Automation" do Vercel
    SG_TEST_EMAIL      conta de teste do Studio (criada se não existir)
    SG_TEST_SENHA      senha da conta de teste (10+ caracteres)

Uso:
    python scripts/teste_casos_reais.py --ref caminho/foto_sala.jpg [--casos a,b,c] [--saida pasta]
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests


def session(base, bypass):
    s = requests.Session()
    if bypass:
        s.headers["x-vercel-protection-bypass"] = bypass
        s.headers["x-vercel-set-bypass-cookie"] = "samesitenone"
    page = s.get(base + "/studio/entrar", timeout=30)
    page.raise_for_status()
    csrf = re.search(r'name="sg-csrf" content="([^"]+)"', page.text).group(1)
    return s, csrf


def login(s, base, csrf, email, senha):
    h = {"X-SG-CSRF": csrf}
    r = s.post(base + "/studio/auth/login", json={"email": email, "senha": senha}, headers=h, timeout=30)
    if r.status_code == 401:
        r = s.post(base + "/studio/auth/cadastro", json={"nome": "Teste Studio", "email": email, "senha": senha}, headers=h, timeout=30)
    r.raise_for_status()
    page = s.get(base + "/studio/", timeout=30).text  # novo CSRF depois do login
    return re.search(r'name="sg-csrf" content="([^"]+)"', page).group(1)


def api(s, base, csrf, method, path, **kw):
    r = s.request(method, base + "/studio/api" + path, headers={"X-SG-CSRF": csrf}, timeout=60, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:300]}")
    return r.json()


def wait_job(s, base, csrf, job_id, limit_s=1500):
    started, last = time.time(), ""
    while time.time() - started < limit_s:
        job = api(s, base, csrf, "GET", f"/jobs/{job_id}")["job"]
        line = f"  {job['status']} {job['progress']}% {job['mensagem']}"
        if line != last:
            print(line, flush=True)
            last = line
        if job["status"] in ("done", "failed", "canceled"):
            return job
        time.sleep(5)
    raise TimeoutError("geração não terminou no tempo limite")


def run_case(s, base, csrf, out, name, texto, anexos=(), opcoes=None, encarte=False):
    print(f"\n=== caso {name}: {texto!r}")
    conv = api(s, base, csrf, "POST", "/conversations", json={})["id"]
    t0 = time.time()
    api(s, base, csrf, "POST", f"/conversations/{conv}/messages", json={"texto": texto, "anexos": list(anexos), "opcoes": opcoes or {}})
    data = None
    for _ in range(60):  # interpretação (HostGator ou regras)
        data = api(s, base, csrf, "GET", f"/conversations/{conv}")
        if not any(m["status"] == "pending" for m in data["mensagens"]):
            break
        time.sleep(3)
    interp_s = time.time() - t0
    print(f"  assistente ({interp_s:.1f}s): {data['mensagens'][-1]['texto'][:300]}")
    if encarte:
        job = api(s, base, csrf, "POST", f"/conversations/{conv}/jobs")["job"]
    elif data["jobs"]:
        job = data["jobs"][0]
    else:
        return {"caso": name, "resultado": "o assistente perguntou (sem geração)", "interpretacao_s": round(interp_s, 1)}
    job = wait_job(s, base, csrf, job["id"])
    row = {"caso": name, "status": job["status"], "tipo": job["tipo"], "tamanho": job["tamanho"], "modo": job["modo"],
           "interpretacao_s": round(interp_s, 1), "total_job_s": job["duracao_s"], "tempos_acer": job["tempos"],
           "prompt": job["prompt"], "erro": job["erro"]}
    if job["resultado_asset_id"]:
        img = s.get(f"{base}/studio/api/assets/{job['resultado_asset_id']}?download=1", timeout=60)
        path = out / f"caso_{name}.jpg"
        path.write_bytes(img.content)
        row["arquivo"] = str(path)
    print("  " + json.dumps(row, ensure_ascii=False))
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", help="foto de um ambiente para o caso (b)")
    parser.add_argument("--casos", default="a,b,c")
    parser.add_argument("--saida", default="resultados_teste_real")
    args = parser.parse_args()
    base = os.getenv("SG_TEST_BASE_URL", "https://sitegenius-studio-preview.vercel.app").rstrip("/")
    email, senha = os.getenv("SG_TEST_EMAIL"), os.getenv("SG_TEST_SENHA")
    if not (email and senha):
        raise SystemExit("Defina SG_TEST_EMAIL e SG_TEST_SENHA")
    out = Path(args.saida)
    out.mkdir(exist_ok=True)
    s, csrf = session(base, os.getenv("SG_VERCEL_BYPASS", ""))
    csrf = login(s, base, csrf, email, senha)
    print("status:", api(s, base, csrf, "GET", "/status"))
    casos, rows = set(args.casos.split(",")), []
    if "a" in casos:
        rows.append(run_case(s, base, csrf, out, "a", "astronauta em floresta cinematográfica"))
    if "b" in casos:
        if not args.ref:
            print("caso b ignorado: informe --ref com a foto de um ambiente")
        else:
            with open(args.ref, "rb") as fh:
                up = s.post(base + "/studio/api/uploads", headers={"X-SG-CSRF": csrf},
                            files={"arquivo": (Path(args.ref).name, fh)}, data={"tipo": "reference"}, timeout=60)
            up.raise_for_status()
            rows.append(run_case(s, base, csrf, out, "b", "anúncio imobiliário desta sala, mais elegante e iluminada ao entardecer",
                                 anexos=[up.json()["asset"]["id"]], opcoes={"fidelidade": "media"}))
    if "c" in casos:
        rows.append(run_case(s, base, csrf, out, "c", "Encarte de hortifruti, formato quadrado\nTomate kg 6,99\nBanana prata kg 5,49",
                             encarte=True))
    (out / "resultado.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nresumo salvo em {out / 'resultado.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
