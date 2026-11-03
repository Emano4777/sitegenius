"""Laboratório do Studio reaproveitando a infraestrutura de produção, totalmente isolado dela.

O que reaproveita: o MESMO código do servidor (fila sg_jobs, status, armazenamento, autenticação HMAC dos workers) e
o MESMO agente do Acer (workers/acer_agent.py), sem mudar uma linha.
O que separa: um schema e um papel de banco próprios (sitegenius_lab), um deploy de teste do Vercel que não é
produção (sem alias), uma credencial de worker só do laboratório e uma conta de teste só do laboratório.
Assim, o agente de produção nunca vê um trabalho do laboratório, e os testes não contam nas cotas, no painel nem
no histórico reais.

Uso (na PE0, uma vez):  python lab/lab_setup.py
Segredos vão para scratchpad/secrets (nunca no Git, nunca impressos).
"""
import os
import re
import secrets
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SECRETS = Path(os.environ.get("SG_LAB_SECRETS", r"C:\Users\windows\AppData\Local\Temp\claude\C--Users-windows-Downloads-sitegenius-rebuild"
                                                r"\bf29e511-e568-4d8f-af51-6cc897b0dba5\scratchpad\secrets"))
ROLE = SCHEMA = "sitegenius_lab"


def create_role_and_schema():
    out = SECRETS / "lab_db_url.txt"
    if out.exists():
        return out.read_text(encoding="utf-8").strip()
    env = Path(r"C:\Users\Public\dns-ecommerce\.env").read_text(encoding="utf-8-sig")
    admin_url = re.search(r"^DATABASE_URL=(.+)$", env, re.M).group(1).strip().strip("\"'")
    m = re.match(r"postgres(?:ql)?://([^:]+)\.([a-z0-9]+):[^@]+@([^:/]+):(\d+)/(\w+)", admin_url)
    _, ref, host, _, dbname = m.groups()
    c = psycopg2.connect(admin_url, connect_timeout=15)
    cur = c.cursor()
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (ROLE,))
    if cur.fetchone():
        raise SystemExit("o papel sitegenius_lab já existe, mas a URL não está em secrets: nada feito")
    pwd = secrets.token_urlsafe(32)
    cur.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS CONNECTION LIMIT 8 PASSWORD %s")
                .format(sql.Identifier(ROLE)), (pwd,))
    cur.execute(sql.SQL("GRANT {} TO CURRENT_USER WITH SET TRUE, INHERIT FALSE").format(sql.Identifier(ROLE)))
    cur.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(sql.Identifier(SCHEMA), sql.Identifier(ROLE)))
    cur.execute(sql.SQL("ALTER ROLE {} SET search_path = {}").format(sql.Identifier(ROLE), sql.Identifier(SCHEMA)))
    cur.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '15s'").format(sql.Identifier(ROLE)))
    cur.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(SCHEMA)))
    c.commit()
    url = f"postgresql://{ROLE}.{ref}:{pwd}@{host}:6543/{dbname}?sslmode=require"
    out.write_text(url, encoding="utf-8")
    return url


def init_tables_and_user(url):
    """Tabelas pelo mesmo código do servidor (studio.db) e a conta de teste do laboratório."""
    from studio import accounts, db  # noqa: PLC0415
    database = db.Database(url)
    db.init_schema(database)
    pw_file = SECRETS / "lab_user_pass.txt"
    if not pw_file.exists():
        conn = db.Conn(database)
        pw = secrets.token_urlsafe(18) + "Aa1!"
        accounts.create_user(conn, "Laboratório Klein", "lab.klein@sitegenius.com.br", pw, daily_limit=100000, must_change=False)
        conn.commit()
        conn.close()
        pw_file.write_text(pw, encoding="utf-8")


def main():
    SECRETS.mkdir(parents=True, exist_ok=True)
    url = create_role_and_schema()
    init_tables_and_user(url)
    for name, size in (("lab_worker_secret.txt", 32), ("lab_flask_secret.txt", 48)):
        p = SECRETS / name
        if not p.exists():
            p.write_text(secrets.token_urlsafe(size), encoding="utf-8")
    print("laboratório pronto: schema, papel, tabelas, conta de teste e segredos (em", SECRETS, ")")


if __name__ == "__main__":
    main()
