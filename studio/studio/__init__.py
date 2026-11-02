"""SiteGenius Studio: chat de criação de encartes com IA local.

Uso no app Flask existente:
    from studio import init_app as init_studio
    init_studio(app)
"""
import os

from .config import StudioConfig
from .db import Database, close_conn, init_schema, resolve_database_url

INSECURE_SECRET_KEYS = {"minha-chave-segura", "", None}


def init_app(app, config=None):
    config = config or StudioConfig.from_env()
    state = {"config": config, "db": None, "insecure_reason": ""}
    app.extensions["sg_studio"] = state

    if config.is_vercel and app.secret_key in INSECURE_SECRET_KEYS:
        # Com a secret_key pública, qualquer pessoa forja a sessão: o Studio fica desligado.
        state["insecure_reason"] = "defina FLASK_SECRET_KEY no ambiente"
    try:
        url = resolve_database_url(config, app.instance_path)
    except RuntimeError as exc:
        state["insecure_reason"] = state["insecure_reason"] or str(exc)
        url = None
    if url:
        state["db"] = Database(url)
        # Tabelas são criadas automaticamente só no SQLite local;
        # no Postgres de produção use: python -m studio.migrate
        if state["db"].is_sqlite or os.getenv("SG_STUDIO_AUTO_MIGRATE") == "1":
            init_schema(state["db"])

    from .routes import bp

    app.register_blueprint(bp)
    app.teardown_appcontext(close_conn)
    return app
