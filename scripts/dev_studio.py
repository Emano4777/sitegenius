"""Servidor local SÓ do Studio, para desenvolvimento e validação visual.

Não importa o app.py (que conecta no banco de produção). Usa SQLite em instance/.

    python scripts/dev_studio.py              # http://127.0.0.1:5055/studio/
    python -m workers.acer_agent --no-comfy   # em outro terminal, com workers/.env de dev
"""
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, redirect  # noqa: E402

os.environ.setdefault("SG_STUDIO_DEV_USER_ID", "1")
os.environ.pop("VERCEL", None)

from studio import init_app  # noqa: E402

app = Flask(__name__, instance_path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance"))
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
init_app(app)


@app.get("/")
def home():
    return redirect("/studio/")


if __name__ == "__main__":
    # Somente loopback; nunca 0.0.0.0.
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5055")), debug=False)
