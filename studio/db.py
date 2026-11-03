"""Acesso a dados do Studio.

Produção: Postgres (SG_STUDIO_DATABASE_URL=postgresql://...).
Desenvolvimento/testes: SQLite (SG_STUDIO_DATABASE_URL=sqlite:///caminho.db).
Todas as tabelas usam o prefixo sg_ e não tocam nas tabelas existentes do site.
Datas são epoch (segundos) e JSON é guardado como texto, para o SQL ser portátil.
"""
import json
import os
import sqlite3
import time
import uuid

from flask import current_app, g

SCHEMA = {
    "sg_conversations": """
        CREATE TABLE IF NOT EXISTS sg_conversations (
            id VARCHAR(36) PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title VARCHAR(160) NOT NULL,
            briefing TEXT,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL
        )""",
    "sg_messages": """
        CREATE TABLE IF NOT EXISTS sg_messages (
            id VARCHAR(36) PRIMARY KEY,
            conversation_id VARCHAR(36) NOT NULL,
            seq INTEGER NOT NULL,
            role VARCHAR(16) NOT NULL,
            content TEXT NOT NULL,
            attachments TEXT NOT NULL DEFAULT '[]',
            briefing TEXT,
            status VARCHAR(16) NOT NULL DEFAULT 'ok',
            created_at BIGINT NOT NULL
        )""",
    "sg_assets": """
        CREATE TABLE IF NOT EXISTS sg_assets (
            id VARCHAR(36) PRIMARY KEY,
            user_id INTEGER NOT NULL,
            kind VARCHAR(16) NOT NULL,
            mime VARCHAR(32) NOT NULL,
            size INTEGER NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            sha256 VARCHAR(64) NOT NULL,
            source_url TEXT,
            license TEXT,
            attribution TEXT,
            data {blob},
            storage_key TEXT,
            created_at BIGINT NOT NULL
        )""",
    "sg_jobs": """
        CREATE TABLE IF NOT EXISTS sg_jobs (
            id VARCHAR(36) PRIMARY KEY,
            user_id INTEGER NOT NULL,
            conversation_id VARCHAR(36),
            kind VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL,
            payload TEXT NOT NULL,
            result TEXT,
            error TEXT,
            progress INTEGER NOT NULL DEFAULT 0,
            status_message VARCHAR(200),
            worker_id VARCHAR(64),
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_until BIGINT,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL
        )""",
    "sg_rate": """
        CREATE TABLE IF NOT EXISTS sg_rate (
            bucket VARCHAR(120) PRIMARY KEY,
            window_start BIGINT NOT NULL,
            count INTEGER NOT NULL
        )""",
    "sg_nonces": """
        CREATE TABLE IF NOT EXISTS sg_nonces (
            nonce VARCHAR(80) PRIMARY KEY,
            created_at BIGINT NOT NULL
        )""",
    # Contas próprias do Studio (independentes do login antigo do SiteGenius).
    "sg_users": """
        CREATE TABLE IF NOT EXISTS sg_users (
            id {serial},
            email VARCHAR(254) NOT NULL UNIQUE,
            name VARCHAR(120) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            session_version INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL,
            last_login_at BIGINT
        )""",
    "sg_worker_status": """
        CREATE TABLE IF NOT EXISTS sg_worker_status (
            worker_id VARCHAR(64) PRIMARY KEY,
            kind VARCHAR(16) NOT NULL,
            last_seen BIGINT NOT NULL
        )""",
    # Resultados do --bench do Acer (tempos reais), usados nas estimativas enquanto não há gerações suficientes.
    "sg_benchmarks": """
        CREATE TABLE IF NOT EXISTS sg_benchmarks (
            id VARCHAR(36) PRIMARY KEY,
            worker_id VARCHAR(64) NOT NULL,
            size VARCHAR(16) NOT NULL,
            steps INTEGER,
            run INTEGER,
            comfy_s REAL,
            load_s REAL,
            encode_s REAL,
            sampling_s REAL,
            vae_s REAL,
            cold INTEGER NOT NULL DEFAULT 0,
            created_at BIGINT NOT NULL
        )""",
    # Catálogo de fotos verificadas (origem e autorização de uso registradas). user_id 0 = licença aberta, compartilhável.
    "sg_catalog": """
        CREATE TABLE IF NOT EXISTS sg_catalog (
            id VARCHAR(36) PRIMARY KEY,
            user_id INTEGER NOT NULL,
            ean VARCHAR(14),
            nome VARCHAR(120) NOT NULL,
            marca VARCHAR(80),
            variante VARCHAR(80),
            apresentacao VARCHAR(40),
            asset_id VARCHAR(36) NOT NULL,
            fonte VARCHAR(80),
            pagina TEXT,
            image_url TEXT,
            licenca VARCHAR(80),
            autorizacao VARCHAR(200),
            usos INTEGER NOT NULL DEFAULT 0,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL
        )""",
    "sg_password_resets": """
        CREATE TABLE IF NOT EXISTS sg_password_resets (
            token_hash VARCHAR(64) PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at BIGINT NOT NULL,
            used_at BIGINT,
            created_at BIGINT NOT NULL
        )""",
}

# Colunas acrescentadas depois da criação (migração idempotente; nunca apaga dados).
# Versão 2 dos jobs: criação livre (free_image) e encarte (ad_composite) com spec explícita.
ADDED_COLUMNS = {
    "sg_assets": [("storage_key", "TEXT")],
    # Contas gerenciadas pelo administrador: papel, limites de uso e troca de senha obrigatória.
    "sg_users": [
        ("is_admin", "INTEGER NOT NULL DEFAULT 0"),
        ("daily_limit", "INTEGER"),
        ("monthly_limit", "INTEGER"),
        ("must_change_password", "INTEGER NOT NULL DEFAULT 0"),
        ("created_by", "INTEGER"),
        ("notes", "VARCHAR(300)"),
    ],
    "sg_conversations": [("mode", "VARCHAR(16)"), ("spec", "TEXT"), ("encartes", "TEXT")],
    "sg_messages": [("job_id", "VARCHAR(36)")],
    "sg_jobs": [
        ("schema_version", "INTEGER"),
        ("gen_type", "VARCHAR(16)"),
        ("prompt_original", "TEXT"),
        ("prompt_visual", "TEXT"),
        ("negative_prompt", "TEXT"),
        ("style", "VARCHAR(32)"),
        ("aspect_ratio", "VARCHAR(8)"),
        ("seed", "BIGINT"),
        ("reference_ids", "TEXT"),
        ("render_mode", "VARCHAR(16)"),
        ("parent_job_id", "VARCHAR(36)"),
        ("timings", "TEXT"),
        ("started_at", "BIGINT"),
        ("finished_at", "BIGINT"),
        ("progress_info", "TEXT"),  # etapa/passo reais enviados pelo agente (o texto técnico fica em status_message)
    ],
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS sg_conv_user_idx ON sg_conversations (user_id, updated_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS sg_msg_conv_idx ON sg_messages (conversation_id, seq)",
    "CREATE INDEX IF NOT EXISTS sg_assets_user_idx ON sg_assets (user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS sg_jobs_queue_idx ON sg_jobs (kind, status, created_at)",
    "CREATE INDEX IF NOT EXISTS sg_jobs_user_idx ON sg_jobs (user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS sg_resets_user_idx ON sg_password_resets (user_id)",
    "CREATE INDEX IF NOT EXISTS sg_jobs_conv_idx ON sg_jobs (conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS sg_catalog_user_idx ON sg_catalog (user_id, updated_at)",
]


def now():
    return int(time.time())


def new_id():
    return str(uuid.uuid4())


class Database:
    def __init__(self, url):
        self.url = url
        self.is_sqlite = url.startswith("sqlite:///")

    def connect(self):
        if self.is_sqlite:
            conn = sqlite3.connect(self.url[len("sqlite:///"):], timeout=10)
            conn.row_factory = sqlite3.Row
            return conn
        import psycopg2

        return psycopg2.connect(self.url, connect_timeout=10)

    def schema_statements(self):
        blob = "BLOB" if self.is_sqlite else "BYTEA"
        serial = "INTEGER PRIMARY KEY AUTOINCREMENT" if self.is_sqlite else "SERIAL PRIMARY KEY"
        statements = [sql.format(blob=blob, serial=serial) for sql in SCHEMA.values()]
        if not self.is_sqlite:
            for table, columns in ADDED_COLUMNS.items():
                statements += [f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {kind}" for name, kind in columns]
            # Bancos criados antes do armazenamento externo (comando idempotente).
            statements.append("ALTER TABLE sg_assets ALTER COLUMN data DROP NOT NULL")
        return statements + INDEXES


class Conn:
    """Pequeno adaptador: SQL com '?' e linhas como dict nos dois bancos."""

    def __init__(self, db):
        self.db = db
        self.raw = db.connect()

    def _sql(self, sql):
        return sql if self.db.is_sqlite else sql.replace("?", "%s")

    def _cursor(self):
        if self.db.is_sqlite:
            return self.raw.cursor()
        from psycopg2.extras import RealDictCursor

        return self.raw.cursor(cursor_factory=RealDictCursor)

    def execute(self, sql, params=()):
        cur = self._cursor()
        cur.execute(self._sql(sql), tuple(params))
        return cur

    def one(self, sql, params=()):
        row = self.execute(sql, params).fetchone()
        return _row(row)

    def all(self, sql, params=()):
        return [_row(r) for r in self.execute(sql, params).fetchall()]

    def run(self, sql, params=()):
        """Executa escrita e devolve o número de linhas afetadas."""
        return self.execute(sql, params).rowcount

    def blob(self, data):
        if self.db.is_sqlite:
            return sqlite3.Binary(data)
        import psycopg2

        return psycopg2.Binary(data)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


def _row(row):
    if row is None:
        return None
    data = dict(row)
    for key, value in data.items():
        if isinstance(value, memoryview):
            data[key] = value.tobytes()
    return data


def resolve_database_url(config, instance_path):
    if config.database_url:
        return config.database_url
    if config.is_vercel:
        # Filesystem do Vercel é efêmero: sem banco configurado o Studio não funciona.
        raise RuntimeError("SG_STUDIO_DATABASE_URL não configurada")
    os.makedirs(instance_path, exist_ok=True)
    return "sqlite:///" + os.path.join(instance_path, "studio_dev.sqlite3")


def init_schema(db):
    conn = Conn(db)
    try:
        statements = db.schema_statements()
        tables = [sql for sql in statements if sql.lstrip().startswith("CREATE TABLE")]
        for sql in tables:
            conn.execute(sql)
        if db.is_sqlite:  # SQLite não tem ADD COLUMN IF NOT EXISTS
            for table, columns in ADDED_COLUMNS.items():
                existing = {r["name"] for r in conn.all(f"PRAGMA table_info({table})")}
                for name, kind in columns:
                    if name not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
        for sql in statements:
            if sql not in tables:
                conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


def get_conn():
    if "sg_conn" not in g:
        g.sg_conn = Conn(current_app.extensions["sg_studio"]["db"])
    return g.sg_conn


def close_conn(exc=None):
    conn = g.pop("sg_conn", None)
    if conn is None:
        return
    try:
        if exc is None:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value, default=None):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
