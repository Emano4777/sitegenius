"""Configuração do SiteGenius Studio, lida somente de variáveis de ambiente.

Nenhum segredo deve ser escrito neste arquivo.
"""
import os
from dataclasses import dataclass, field


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _parse_workers(raw):
    """SG_WORKERS = "id:tipo:segredo,id2:tipo2:segredo2" (tipo = render | interpret)."""
    workers = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":", 2)
        if len(parts) != 3 or not all(parts):
            continue
        worker_id, kind, secret = parts
        if kind not in ("render", "interpret") or len(secret) < 32:
            # Segredo curto é recusado para evitar chaves fracas em produção.
            continue
        workers[worker_id] = {"kind": kind, "secret": secret.encode()}
    return workers



@dataclass
class StudioConfig:
    database_url: str = ""
    workers: dict = field(default_factory=dict)
    # "rules": interpretação local por regras; "queue": tarefas para o worker HostGator.
    interpreter_mode: str = "auto"
    interpret_timeout_s: int = 120
    max_upload_bytes: int = 4 * 1024 * 1024  # limite de corpo do Vercel é 4,5 MB
    max_image_pixels: int = 40_000_000
    max_image_side: int = 2048
    daily_job_quota: int = 20
    job_lease_s: int = 20 * 60
    max_attempts: int = 3
    dev_user_id: int = 0
    is_vercel: bool = False

    @classmethod
    def from_env(cls):
        is_vercel = bool(os.getenv("VERCEL"))
        # auto: usa a IA da HostGator quando o worker está online; rules: só regras; queue: sempre a fila.
        mode = os.getenv("SG_INTERPRETER_MODE", "auto").strip().lower()
        if mode not in ("rules", "queue", "auto"):
            mode = "auto"
        return cls(
            database_url=os.getenv("SG_STUDIO_DATABASE_URL", "").strip(),
            workers=_parse_workers(os.getenv("SG_WORKERS", "")),
            interpreter_mode=mode,
            interpret_timeout_s=_int("SG_INTERPRET_TIMEOUT_S", 120),
            daily_job_quota=_int("SG_DAILY_JOB_QUOTA", 20),
            # Login de desenvolvimento nunca vale dentro do Vercel.
            dev_user_id=0 if is_vercel else _int("SG_STUDIO_DEV_USER_ID", 0),
            is_vercel=is_vercel,
        )
