"""Cliente HTTP assinado (HMAC) usado pelos workers para falar com o SiteGenius.

Somente conexões de saída: o worker busca tarefas, nada precisa estar exposto.
"""
import json
import os
import secrets
import time
from urllib.parse import urlsplit

import requests

from .security import sign


def vercel_bypass_header():
    """Deploys de preview protegidos no Vercel exigem o token de bypass de automação."""
    token = os.getenv("SG_VERCEL_BYPASS")
    return {"x-vercel-protection-bypass": token} if token else {}


class WorkerClient:
    def __init__(self, base_url, worker_id, secret, session=None, timeout=(10, 60)):
        parts = urlsplit(base_url)
        if parts.scheme != "https" and parts.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("SG_BASE_URL precisa ser https (http só para localhost)")
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id
        self.secret = secret.encode() if isinstance(secret, str) else secret
        self.http = session or requests.Session()
        self.timeout = timeout

    def headers(self, method, path, body):
        timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
        return {
            "X-SG-Worker": self.worker_id,
            "X-SG-Timestamp": timestamp,
            "X-SG-Nonce": nonce,
            "X-SG-Signature": sign(self.secret, timestamp, nonce, method, path, body),
            **vercel_bypass_header(),
        }

    def request(self, method, path, body=b"", content_type="application/json"):
        full_path = "/studio/worker" + path
        headers = self.headers(method, full_path, body)
        if body:
            headers["Content-Type"] = content_type
        resp = self.http.request(method, self.base_url + full_path, data=body or None, headers=headers, timeout=self.timeout)
        return resp

    def _json(self, method, path, payload):
        resp = self.request(method, path, json.dumps(payload).encode())
        resp.raise_for_status()
        return resp

    def claim(self):
        resp = self._json("POST", "/claim", {})
        return None if resp.status_code == 204 else resp.json()["job"]

    def progress(self, job_id, progress, message, timings=None, info=None):
        """message = texto técnico (só admin); info = etapa/passo reais (o servidor gera o texto do cliente)."""
        payload = {"progress": progress, "mensagem": message}
        if timings:
            payload["tempos"] = timings
        if info:
            payload.update({k: v for k, v in info.items() if k in ("etapa", "passo", "total", "restante_s")})
        self._json("POST", f"/jobs/{job_id}/progress", payload)

    def asset(self, asset_id):
        resp = self.request("GET", f"/assets/{asset_id}")
        resp.raise_for_status()
        return resp.content

    def complete_image(self, job_id, image_bytes):
        resp = self.request("POST", f"/jobs/{job_id}/complete", image_bytes, "image/jpeg")
        resp.raise_for_status()
        return resp.json()

    def complete_json(self, job_id, payload):
        return self._json("POST", f"/jobs/{job_id}/complete", payload).json()

    def report_benchmarks(self, rows):
        """Envia o resultado do --bench (tempos reais medidos no Acer) para as estimativas da tela."""
        return self._json("POST", "/benchmarks", {"linhas": rows[:60]}).json()

    def fail(self, job_id, message):
        self._json("POST", f"/jobs/{job_id}/fail", {"erro": str(message)[:300]})
