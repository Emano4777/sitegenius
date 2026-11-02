"""Segurança do Studio: assinatura de workers, rate limit, imagens e downloads."""
import hashlib
import hmac
import io
import ipaddress
import socket
from urllib.parse import urlsplit

import requests
from PIL import Image, UnidentifiedImageError

from .db import now

# ---------------------------------------------------------------- workers (HMAC)

SIGNATURE_WINDOW_S = 300


def canonical_request(timestamp, nonce, method, path, body):
    body_hash = hashlib.sha256(body or b"").hexdigest()
    return f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n{body_hash}".encode()


def sign(secret, timestamp, nonce, method, path, body):
    return hmac.new(secret, canonical_request(timestamp, nonce, method, path, body), hashlib.sha256).hexdigest()


def verify_worker_request(conn, workers, headers, method, path, body):
    """Devolve (worker_id, worker) se a assinatura for válida, senão (None, motivo)."""
    worker_id = headers.get("X-SG-Worker", "")
    timestamp = headers.get("X-SG-Timestamp", "")
    nonce = headers.get("X-SG-Nonce", "")
    signature = headers.get("X-SG-Signature", "")
    worker = workers.get(worker_id)
    if not worker or not timestamp or not signature or not (16 <= len(nonce) <= 80):
        return None, "credenciais ausentes"
    try:
        ts = int(timestamp)
    except ValueError:
        return None, "timestamp inválido"
    if abs(now() - ts) > SIGNATURE_WINDOW_S:
        return None, "timestamp fora da janela"
    expected = sign(worker["secret"], timestamp, nonce, method, path, body)
    if not hmac.compare_digest(expected, signature):
        return None, "assinatura inválida"
    # Anti-replay: cada nonce só vale uma vez dentro da janela.
    conn.run("DELETE FROM sg_nonces WHERE created_at < ?", (now() - 2 * SIGNATURE_WINDOW_S,))
    if conn.one("SELECT nonce FROM sg_nonces WHERE nonce = ?", (f"{worker_id}:{nonce}",)):
        return None, "nonce repetido"
    conn.run("INSERT INTO sg_nonces (nonce, created_at) VALUES (?, ?)", (f"{worker_id}:{nonce}", now()))
    return worker_id, worker


# ---------------------------------------------------------------- rate limit


def rate_limited(conn, bucket, limit, window_s):
    """Janela fixa guardada no banco (funciona entre instâncias serverless)."""
    current = now()
    window_start = current - current % window_s
    row = conn.one("SELECT window_start, count FROM sg_rate WHERE bucket = ?", (bucket,))
    if row is None:
        conn.run("INSERT INTO sg_rate (bucket, window_start, count) VALUES (?, ?, 1)", (bucket, window_start))
        return False
    if row["window_start"] != window_start:
        conn.run("UPDATE sg_rate SET window_start = ?, count = 1 WHERE bucket = ?", (window_start, bucket))
        return False
    if row["count"] >= limit:
        return True
    conn.run("UPDATE sg_rate SET count = count + 1 WHERE bucket = ?", (bucket,))
    return False


# ---------------------------------------------------------------- imagens


class ImageRejected(ValueError):
    pass


ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def sanitize_image(data, max_bytes, max_pixels, max_side):
    """Valida pelo conteúdo (não pela extensão) e recodifica a imagem.

    A recodificação remove EXIF/metadados e qualquer conteúdo extra embutido.
    Devolve (bytes, mime, largura, altura).
    """
    if not data:
        raise ImageRejected("arquivo vazio")
    if len(data) > max_bytes:
        raise ImageRejected(f"arquivo maior que {max_bytes // (1024 * 1024)} MB")
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise ImageRejected("formato não permitido (use JPG, PNG ou WEBP)")
            if probe.width * probe.height > max_pixels:
                raise ImageRejected("imagem com resolução grande demais")
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            img = img.convert("RGBA" if has_alpha else "RGB")
            img.thumbnail((max_side, max_side), Image.LANCZOS)
            out = io.BytesIO()
            if has_alpha:
                img.save(out, "PNG", optimize=True)
                mime = "image/png"
            else:
                img.save(out, "JPEG", quality=90, optimize=True)
                mime = "image/jpeg"
            return out.getvalue(), mime, img.width, img.height
    except ImageRejected:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise ImageRejected("arquivo não é uma imagem válida") from exc


# ---------------------------------------------------------------- downloads (SSRF)

# Somente fontes com licença conhecida. Novas fontes exigem revisão de licença.
ALLOWED_IMAGE_HOSTS = {
    "images.openfoodfacts.org",
    "static.openfoodfacts.org",
    "images.openbeautyfacts.org",
    "static.openbeautyfacts.org",
    "images.openproductsfacts.org",
    "static.openproductsfacts.org",
}


class UnsafeUrl(ValueError):
    pass


def _host_is_public(host):
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrl("host não resolvido") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def check_url(url, allowed_hosts=ALLOWED_IMAGE_HOSTS):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise UnsafeUrl("somente https")
    if parts.username or parts.password or parts.port not in (None, 443):
        raise UnsafeUrl("URL não permitida")
    host = parts.hostname.lower()
    if host not in allowed_hosts:
        raise UnsafeUrl("fonte de imagem não permitida")
    if not _host_is_public(host):
        raise UnsafeUrl("endereço interno bloqueado")
    return url


def safe_download(url, max_bytes, allowed_hosts=ALLOWED_IMAGE_HOSTS, session=None, max_redirects=3):
    http = session or requests
    for _ in range(max_redirects + 1):
        check_url(url, allowed_hosts)
        resp = http.get(
            url,
            timeout=(5, 15),
            stream=True,
            allow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            url = requests.compat.urljoin(url, resp.headers.get("Location", ""))
            resp.close()
            continue
        if resp.status_code != 200:
            resp.close()
            raise UnsafeUrl(f"download falhou ({resp.status_code})")
        chunks, total = [], 0
        for chunk in resp.iter_content(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                resp.close()
                raise UnsafeUrl("imagem remota grande demais")
            chunks.append(chunk)
        return b"".join(chunks)
    raise UnsafeUrl("redirecionamentos demais")


USER_AGENT = "SiteGenius-Studio/0.1 (+https://sitegenius.com.br)"
