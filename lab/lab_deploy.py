"""Deploy de TESTE do Vercel (não é produção, sem alias) com variáveis só deste deploy: banco do laboratório, só a
credencial de worker do laboratório e imagens guardadas no próprio banco do laboratório. As variáveis do projeto não
mudam. A URL do deploy vai para secrets/lab_url.txt. Uso: python lab/lab_deploy.py"""
import re
import subprocess
from pathlib import Path

from lab_setup import SECRETS

ROOT = Path(__file__).resolve().parents[1]


def main():
    db = (SECRETS / "lab_db_url.txt").read_text(encoding="utf-8").strip()
    worker = (SECRETS / "lab_worker_secret.txt").read_text(encoding="utf-8").strip()
    flask = (SECRETS / "lab_flask_secret.txt").read_text(encoding="utf-8").strip()
    env = {"SG_STUDIO_DATABASE_URL": db, "SG_WORKERS": f"acerlab:render:{worker}", "SG_CLOUDINARY_URL": "",
           "FLASK_SECRET_KEY": flask, "SG_INTERPRETER_MODE": "rules", "SG_DAILY_JOB_QUOTA": "100000"}
    cmd = ["vercel", "deploy", "--yes", "--target", "preview"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, shell=True)
    url = next((m for m in re.findall(r"https://[\w.-]+\.vercel\.app", r.stdout + r.stderr)), None)
    if r.returncode != 0 or not url:
        raise SystemExit("deploy falhou: " + (r.stderr or r.stdout)[-600:].replace(db, "***").replace(worker, "***"))
    (SECRETS / "lab_url.txt").write_text(url, encoding="utf-8")
    print("deploy do laboratório pronto:", url)


if __name__ == "__main__":
    main()
