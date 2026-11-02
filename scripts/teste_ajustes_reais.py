"""Teste real: o modelo distingue ajuste (texto, cena ou os dois) de criação nova, sem frases prontas.

Mesmas variáveis de ambiente de scripts/teste_casos_reais.py. Uso: python scripts/teste_ajustes_reais.py --saida pasta
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from teste_casos_reais import api, login, session, wait_job  # noqa: E402
from teste_textos_reais import save, send  # noqa: E402

STEPS = [
    ("1_criacao", "Post quadrado para a minha empresa Emanuel Tecnologia, com o slogan \"Soluções em TI\" no rodapé, "
                  "escritório moderno ao fundo"),
    ("2_so_texto", "o nome ficou meio pequeno"),
    ("3_cena_e_texto", "não curti muito o fundo, deixa mais escuro e muda o slogan pra Suporte 24 horas"),
    ("4_so_texto_cor", "e se as letras fossem douradas?"),
    ("5_nova", "agora quero uma arte de pizzaria com Pizza Boa no topo"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--saida", default="resultados_ajustes")
    args = parser.parse_args()
    base = os.getenv("SG_TEST_BASE_URL", "https://www.sitegenius.com.br").rstrip("/")
    out = Path(args.saida)
    out.mkdir(exist_ok=True)
    s, csrf = session(base, os.getenv("SG_VERCEL_BYPASS", ""))
    csrf = login(s, base, csrf, os.environ["SG_TEST_EMAIL"], os.environ["SG_TEST_SENHA"])
    conv = api(s, base, csrf, "POST", "/conversations", json={})["id"]
    seen, rows = set(), []
    for name, text in STEPS:
        t0 = time.time()
        data, interp = send(s, base, csrf, conv, text)
        new = [j for j in data["jobs"] if j["id"] not in seen]
        row = {"passo": name, "pedido": text, "interpretacao_s": round(interp, 1),
               "resposta": data["mensagens"][-1]["texto"][:200]}
        if new:
            job = new[0]
            seen.add(job["id"])
            if job["status"] not in ("done", "failed"):
                job = wait_job(s, base, csrf, job["id"], limit_s=1200)
            row.update({"tipo": job["tipo"], "modo": job["modo"], "status": job["status"],
                        "total_s": round(time.time() - t0, 1), "arquivo": save(s, base, out, name, job)})
        print(json.dumps(row, ensure_ascii=False), flush=True)
        rows.append(row)
    (out / "ajustes.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
