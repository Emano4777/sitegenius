"""Testes reais dos textos na imagem pelo Preview (precisa do Acer rodando o agente).

Casos: (1) "Emanuel Tecnologia"; (2) anúncio com título, subtítulo e preço; (3) texto no canto superior direito;
(4) vários textos com hierarquia; (5) sem texto; (6a) vertical para Instagram; (6b) quadrada; (7) ajuste só de texto.

Mesmas variáveis de ambiente de scripts/teste_casos_reais.py (SG_TEST_EMAIL, SG_TEST_SENHA, SG_VERCEL_BYPASS).
Uso: python scripts/teste_textos_reais.py [--casos 1,2,3,4,5,6a,6b,7] [--saida pasta]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from teste_casos_reais import api, login, session, wait_job  # noqa: E402

CASES = {
    "1": "Crie uma arte para a minha empresa com o nome Emanuel Tecnologia, estilo futurista, fundo escuro com luzes azuis",
    "2": ('Anúncio para Instagram de um fone de ouvido bluetooth preto sobre fundo elegante, título "Som Sem Limites", '
          'subtítulo "Graves potentes e 30 horas de bateria", preço R$ 199,90'),
    "3": 'Foto de uma praia tropical ao entardecer, formato paisagem, com o texto "Verão 2026" no canto superior direito',
    "4": ('Post quadrado para cafeteria com xícara de cappuccino e croissant numa mesa de madeira: título "Café da Manhã '
          'Especial", subtítulo "Cappuccino + croissant artesanal", de R$ 29,90 por R$ 22,90, botão "Peça já", '
          'contato: (11) 98888-7777'),
    "5": "Uma floresta de pinheiros com neblina ao amanhecer, luz suave, foto realista",
    "6a": ('Arte vertical para Instagram de academia, ambiente com halteres e luz dramática, "TREINE FORTE" no topo e '
           '"Matrículas abertas" no rodapé'),
    "6b": ('Arte quadrada elegante para spa com pedras quentes, velas e uma orquídea, com "Momento Zen" e '
           '"Massagens e terapias relaxantes"'),
}
# Conjunto v2: superfícies, textos sem aspas, símbolos brasileiros e imagem sem texto.
CASES.update({
    "p1": "Crie uma imagem de um escritório moderno com Emanuel Tecnologia escrito na parede atrás da mesa",
    "p2": 'Fachada de uma cafeteria vista de lado, com uma placa inclinada escrita "Café Aroma" na placa',
    "p3": 'Foto de produto de uma camiseta branca lisa em um cabide, com "Emanuel Tecnologia" na camiseta',
    "p4": "Faz um anúncio de uma casa moderna com piscina por 540 mil reais e coloca aceita financiamento",
    "p5": ("Crie um banner horizontal para loja de roupas com o título Super Ofertas, subtítulo Até 50% de desconto "
           "e coloca Compre já no rodapé"),
    "p6": 'Arte quadrada para açaiteria com "Açaí & Cia — nº 1 em São João" e "2 por R$ 9,99!"',
    "p7": "Uma vista aérea de uma cidade litorânea ao entardecer, foto realista",
})
CASES.update({
    "s1": "Uma pessoa usando uma camiseta preta com Emano Tec estampado no peito, foto realista",
    "s2": "Uma caneca branca com Emanuel Tecnologia na lateral, foto de produto",
    "s3": 'Fachada de loja vista de lado com uma placa inclinada escrita "Mercado Bom Preço" na placa',
})
EDIT = ("1", 'mude o nome para "Emanuel Tech" e coloque no topo')


def send(s, base, csrf, conv, texto):
    t0 = time.time()
    api(s, base, csrf, "POST", f"/conversations/{conv}/messages", json={"texto": texto, "anexos": [], "opcoes": {}})
    data = None
    for _ in range(80):
        data = api(s, base, csrf, "GET", f"/conversations/{conv}")
        if not any(m["status"] == "pending" for m in data["mensagens"]):
            break
        time.sleep(3)
    return data, time.time() - t0


def save(s, base, out, name, job):
    if not job.get("resultado_asset_id"):
        return None
    img = s.get(f"{base}/studio/api/assets/{job['resultado_asset_id']}?download=1", timeout=60)
    path = out / f"texto_{name}.jpg"
    path.write_bytes(img.content)
    return str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--casos", default="1,2,3,4,5,6a,6b,7")
    parser.add_argument("--saida", default="resultados_textos")
    args = parser.parse_args()
    base = os.getenv("SG_TEST_BASE_URL", "https://sitegenius-studio-preview.vercel.app").rstrip("/")
    out = Path(args.saida)
    out.mkdir(exist_ok=True)
    s, csrf = session(base, os.getenv("SG_VERCEL_BYPASS", ""))
    csrf = login(s, base, csrf, os.environ["SG_TEST_EMAIL"], os.environ["SG_TEST_SENHA"])
    wanted = args.casos.split(",")
    convs, pending, rows = {}, [], []
    # Envia todos os pedidos primeiro (a fila do gerador processa em ordem) e depois acompanha cada um.
    for name in [c for c in wanted if c in CASES]:
        convs[name] = api(s, base, csrf, "POST", "/conversations", json={})["id"]
        data, interp_s = send(s, base, csrf, convs[name], CASES[name])
        reply = data["mensagens"][-1]["texto"]
        print(f"[{name}] interpretação {interp_s:.1f}s: {reply[:160]}", flush=True)
        if data["jobs"]:
            pending.append((name, data["jobs"][0]["id"], interp_s))
        else:
            rows.append({"caso": name, "resultado": "sem geração", "resposta": reply})
    for name, job_id, interp_s in pending:
        job = wait_job(s, base, csrf, job_id, limit_s=3600)
        row = {"caso": name, "status": job["status"], "tamanho": job["tamanho"], "proporcao": job["proporcao"],
               "interpretacao_s": round(interp_s, 1), "fila_s": job["espera_s"], "geracao_s": job["geracao_s"],
               "tempos": job["tempos"], "prompt": job["prompt"], "arquivo": save(s, base, out, name, job)}
        print(json.dumps(row, ensure_ascii=False), flush=True)
        rows.append(row)
    if "7" in wanted and EDIT[0] in convs:
        data, took = send(s, base, csrf, convs[EDIT[0]], EDIT[1])
        job = data["jobs"][0]
        row = {"caso": "7", "tipo": job["tipo"], "status": job["status"], "resposta_s": round(took, 1),
               "resposta": data["mensagens"][-1]["texto"], "arquivo": save(s, base, out, "7", job)}
        print(json.dumps(row, ensure_ascii=False), flush=True)
        rows.append(row)
    (out / "resultado.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
