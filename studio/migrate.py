"""Cria as tabelas sg_* (idempotente). Não altera nenhuma tabela existente.

    python -m studio.migrate            # mostra o SQL
    python -m studio.migrate --apply    # aplica em SG_STUDIO_DATABASE_URL
"""
import os
import sys

from .db import Database, init_schema


def main(argv):
    url = os.getenv("SG_STUDIO_DATABASE_URL", "").strip()
    if not url:
        print("Defina SG_STUDIO_DATABASE_URL.", file=sys.stderr)
        return 2
    db = Database(url)
    statements = db.schema_statements()
    if "--apply" not in argv:
        print(";\n".join(s.strip() for s in statements) + ";")
        print("\n-- nada aplicado; use --apply", file=sys.stderr)
        return 0
    init_schema(db)
    print(f"OK: {len(statements)} comandos aplicados (CREATE ... IF NOT EXISTS).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
