"""Busca de imagens de produto pelo SearXNG local: conferência de produto, EAN, resolução e reserva com navegador."""
import io
import sys
import types

from PIL import Image

import workers.hostgator_tools as tools


def _jpeg(color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (600, 800), color).save(buf, "JPEG")
    return buf.getvalue()


RESULTS = [
    {"title": "Café Melitta Tradicional 500g", "img_src": "https://a.com/melitta.jpg", "url": "https://a.com/p1", "resolution": "800 x 800"},
    {"title": "Café Pilão Tradicional 500g", "img_src": "https://b.com/pilao-mini.jpg", "url": "https://b.com/p2", "resolution": "120 x 120"},
    {"title": "Café Pilão Tradicional Pacote 500g", "img_src": "https://c.com/pilao.jpg", "url": "https://c.com/p3", "resolution": "1000 x 1000"},
    {"title": "Café Pilão Tradicional 500g 7896089012019", "img_src": "https://d.com/pilao-ean.jpg", "url": "https://d.com/p4", "resolution": "900 x 900"},
    {"title": "Café Pilão Tradicional 500g vácuo", "img_src": "https://e.com/pilao2.jpg", "url": "https://e.com/p5", "resolution": ""},
]


class FakeSite:
    def __init__(self):
        self.calls = []

    def call(self, method, path, body=None, timeout=30):
        self.calls.append((path, body))
        return {"asset_id": "a1"} if path.endswith("/candidate") else {"ok": True}


def test_gtin_check_digit():
    assert tools.gtin_valid("7896089012019") and tools.gtin_valid("7893500020158")
    assert not tools.gtin_valid("7896089012018") and not tools.gtin_valid("123")


def test_searx_candidates_filter_and_ean_first(monkeypatch):
    monkeypatch.setattr(tools, "searx", lambda q, cat="general", timeout=15: (RESULTS, [["duckduckgo", "CAPTCHA"]] and ["duckduckgo: CAPTCHA"]))
    cands, notes = tools.search_product_searx("Café Pilão Tradicional 500g", ean="7896089012019")
    imgs = [c["imagem"] for c in cands]
    assert "https://b.com/pilao-mini.jpg" not in imgs          # resolução baixa
    assert imgs[0] == "https://d.com/pilao-ean.jpg"             # EAN confirmado vem primeiro
    assert notes == ["duckduckgo: CAPTCHA"]


def test_websearch_uses_local_search_and_skips_browser(monkeypatch):
    monkeypatch.setattr(tools, "searx", lambda q, cat="general", timeout=15: (RESULTS, []))
    monkeypatch.setattr(tools, "allowed", lambda url: True)
    monkeypatch.setattr(tools, "_download", lambda url: _jpeg())
    launched = []
    fake_pw = types.ModuleType("playwright.sync_api")
    fake_pw.sync_playwright = lambda: launched.append(1)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_pw)
    site = FakeSite()
    tools.process_websearch(site, {"id": "j1", "payload": {"produtos": [{"nome": "Café Pilão Tradicional 500g", "ean": "7896089012019"}]}})
    assert not launched  # SearXNG trouxe opções suficientes: navegador não abre
    sent = [b for p, b in site.calls if p.endswith("/candidate")]
    assert sent and all("melitta" not in b["imagem"] for b in sent)  # marca errada nunca vira opção
    assert sent[0]["nivel"] == "exato" and sent[0]["motivo"] == "EAN confere"
    done = [b for p, b in site.calls if p.endswith("/complete")][0]
    assert done["websearch"][0]["opcoes"]


def test_websearch_falls_back_to_browser_when_local_search_fails(monkeypatch):
    monkeypatch.setattr(tools, "searx", lambda q, cat="general", timeout=15: ([], ["busca local: URLError"]))
    used = []

    class Ctx:
        def __enter__(self):
            class B:
                def new_page(self, **k):
                    class P:
                        def set_default_timeout(self, t):
                            pass
                    return P()

                def close(self):
                    pass

            class PW:
                chromium = types.SimpleNamespace(launch=lambda **k: B())
            return PW()

        def __exit__(self, *a):
            return False
    fake_pw = types.ModuleType("playwright.sync_api")
    fake_pw.sync_playwright = lambda: Ctx()
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_pw)
    monkeypatch.setattr(tools, "search_product", lambda page, q: (used.append(q) or [], ["Carrefour: bloqueou o acesso automático (respeitado)"]))
    site = FakeSite()
    tools.process_websearch(site, {"id": "j2", "payload": {"produtos": [{"nome": "Arroz Tio João 5kg"}]}})
    assert used == ["Arroz Tio João 5kg"]
    done = [b for p, b in site.calls if p.endswith("/complete")][0]
    assert any("busca local" in n for n in done["notas"])


def test_storage_get_retries_slow_tls_once(monkeypatch):
    import requests

    from studio import storage
    calls = []

    class R:
        content = b"ok"

        def raise_for_status(self):
            pass

    def fake_get(url, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise requests.ConnectTimeout("handshake timed out")
        return R()
    monkeypatch.setattr(storage, "_credentials", lambda: {"cloud_name": "x", "api_key": "k", "api_secret": "s"})
    monkeypatch.setattr(storage.requests, "get", fake_get)
    assert storage.get("chave", "image/jpeg") == b"ok"
    assert len(calls) == 2 and calls[0] == (5, 12)


def test_photo_download_network_timeout_never_breaks_chat(app, monkeypatch):
    import requests

    import studio.routes as routes
    monkeypatch.setattr(routes, "safe_download", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectTimeout("tls")))
    with app.app_context():
        assert routes._download_candidate("u1", {"image_url": "https://images.openfoodfacts.org/x.jpg"}) is None


def test_websearch_dedupes_same_page(monkeypatch):
    dup = [{"title": "Biscoito Oreo Original 90g", "img_src": f"https://f.com/oreo{i}.jpg", "url": "https://f.com/oreo", "resolution": "800 x 800"} for i in range(2)]
    dup.append({"title": "Biscoito Oreo Original Recheado 90g", "img_src": "https://g.com/o.jpg", "url": "https://g.com/o", "resolution": "800 x 800"})
    monkeypatch.setattr(tools, "searx", lambda q, cat="general", timeout=15: (dup, []))
    monkeypatch.setattr(tools, "allowed", lambda url: True)
    monkeypatch.setattr(tools, "_download", lambda url: _jpeg())
    fake_pw = types.ModuleType("playwright.sync_api")
    fake_pw.sync_playwright = lambda: None
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_pw)
    site = FakeSite()
    tools.process_websearch(site, {"id": "j3", "payload": {"produtos": [{"nome": "Biscoito Oreo Original 90g"}]}})
    pages = [b["pagina"] for p, b in site.calls if p.endswith("/candidate")]
    assert pages == ["https://f.com/oreo", "https://g.com/o"]
