"""Armazenamento das imagens do Studio.

Com SG_CLOUDINARY_URL (cloudinary://api_key:api_secret@cloud_name) as imagens vão para o Cloudinary
como "authenticated": não há URL pública; o Studio baixa por URL assinada depois de conferir o dono.
Sem a variável (desenvolvimento/testes), ficam na coluna data do banco.
Variável própria do Studio: não interfere no cloudinary.config() global do app.py antigo.
"""
import logging
import os
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)
FOLDER = "sitegenius-studio"


def _credentials():
    raw = os.getenv("SG_CLOUDINARY_URL", "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme != "cloudinary" or not (parts.username and parts.password and parts.hostname):
        log.warning("SG_CLOUDINARY_URL inválida; usando o banco para as imagens")
        return None
    return {"cloud_name": parts.hostname, "api_key": parts.username, "api_secret": parts.password}


def enabled():
    return _credentials() is not None


def _ext(mime):
    return "png" if mime == "image/png" else "jpg"


def put(asset_id, data, mime):
    """Envia a imagem e devolve a chave (public_id); None se o Cloudinary não estiver configurado."""
    creds = _credentials()
    if not creds:
        return None
    import cloudinary.uploader

    result = cloudinary.uploader.upload(
        data,
        public_id=f"{FOLDER}/{asset_id}",
        type="authenticated",
        resource_type="image",
        format=_ext(mime),
        overwrite=False,
        **creds,
    )
    return result["public_id"]


def get(key, mime):
    creds = _credentials()
    if not creds:
        raise RuntimeError("imagem está no Cloudinary, mas SG_CLOUDINARY_URL não está configurada")
    import cloudinary.utils

    url, _ = cloudinary.utils.cloudinary_url(
        key, type="authenticated", resource_type="image", format=_ext(mime), sign_url=True, secure=True, **creds
    )
    # conexão curta + uma nova tentativa: um aperto de mão TLS lento não pode derrubar a conversa do cliente
    for attempt in range(2):
        try:
            resp = requests.get(url, timeout=(5, 12))
            resp.raise_for_status()
            return resp.content
        except (requests.ConnectionError, requests.Timeout):
            if attempt:
                raise
