"""Cria (ou promove) um administrador do SiteGenius Studio direto no banco.

    python -m studio.admin_cli criar-admin --email voce@exemplo.com --nome "Seu Nome" [--saida arquivo.txt]

Usa SG_STUDIO_DATABASE_URL. Gera uma senha provisória (troca obrigatória no primeiro acesso) e a mostra
uma única vez, ou a grava em --saida. Se o e-mail já existir, só promove a administrador e gera nova senha.
"""
import argparse
import os
import sys

from . import accounts
from .db import Conn, Database


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("criar-admin")
    create.add_argument("--email", required=True)
    create.add_argument("--nome", required=True)
    create.add_argument("--saida", help="grava a senha provisória neste arquivo em vez de mostrar")
    args = parser.parse_args(argv)

    url = os.getenv("SG_STUDIO_DATABASE_URL", "").strip()
    if not url:
        print("Defina SG_STUDIO_DATABASE_URL.", file=sys.stderr)
        return 2
    conn = Conn(Database(url))
    try:
        email = accounts.normalize_email(args.email)
        existing = conn.one("SELECT id FROM sg_users WHERE email = ?", (email,))
        if existing:
            conn.run("UPDATE sg_users SET is_admin = 1, active = 1 WHERE id = ?", (existing["id"],))
            temp = accounts.admin_reset_password(conn, existing["id"])
            action = "promovido a administrador"
        else:
            temp = accounts.generate_temp_password()
            accounts.create_user(conn, args.nome, email, temp, is_admin=True, must_change=True)
            action = "criado"
        conn.commit()
    finally:
        conn.close()
    if args.saida:
        with open(args.saida, "w", encoding="utf-8") as fh:
            fh.write(f"SiteGenius Studio - administrador {email}\nSenha provisória: {temp}\n"
                     "Troque no primeiro acesso e apague este arquivo.\n")
        print(f"Administrador {email} {action}. Senha provisória gravada em {args.saida}.")
    else:
        print(f"Administrador {email} {action}. Senha provisória (mostrada só agora): {temp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
