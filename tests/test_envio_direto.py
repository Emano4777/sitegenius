"""Envio direto ao Cloudinary de imagens grandes (4K): autorização presa ao trabalho, conferência no armazenamento."""
import requests

from studio import storage
from tests.test_studio_api import prepared_conversation
from workers import acer_agent


def _job(user, render_worker):
    conv, _, _ = prepared_conversation(user)
    user.post(f"/studio/api/conversations/{conv}/jobs")
    return render_worker.claim()


def test_direct_upload_is_bound_to_job_and_verified(user, render_worker, monkeypatch):
    job = _job(user, render_worker)
    stored = {}
    monkeypatch.setattr(storage, "signed_upload", lambda asset_id: {"url": "https://api.cloudinary.com/v1_1/x/image/upload",
                                                                    "fields": {"public_id": f"pasta/{asset_id}", "signature": "s"}})
    monkeypatch.setattr(storage, "resource", lambda asset_id: stored.get(asset_id))

    class Fake:
        status_code = 200

        def raise_for_status(self):
            pass
    sent = {}

    def fake_post(url, data, files, timeout):
        sent["public_id"] = data["public_id"]
        aid = data["public_id"].split("/")[-1]
        stored[aid] = {"key": data["public_id"], "format": "png", "width": 3840, "height": 2160, "bytes": 9_000_000, "etag": "e"}
        return Fake()
    monkeypatch.setattr(requests, "post", fake_post)
    out = render_worker.complete_direct(job["id"], b"\x89PNG" + b"0" * 10)
    assert out["ok"] and sent["public_id"].endswith(out["asset_id"])
    view = user.get(f"/studio/api/jobs/{job['id']}").get_json()
    assert view["job"]["status"] == "done"


def test_direct_upload_rejects_foreign_or_bad_images(user, render_worker, monkeypatch):
    job = _job(user, render_worker)
    monkeypatch.setattr(storage, "signed_upload", lambda asset_id: {"url": "u", "fields": {}})
    r = render_worker._json("POST", f"/jobs/{job['id']}/upload-direct", {}).json()
    bad = render_worker.request("POST", f"/jobs/{job['id']}/complete-direct", b'{"asset_id": "outro-asset"}')
    assert bad.status_code == 403  # só o asset autorizado para ESTE trabalho
    monkeypatch.setattr(storage, "resource", lambda a: {"key": "k", "format": "gif", "width": 100, "height": 100, "bytes": 10, "etag": ""})
    gif = render_worker.request("POST", f"/jobs/{job['id']}/complete-direct", ('{"asset_id": "%s"}' % r["asset_id"]).encode())
    assert gif.status_code == 422  # formato fora da lista
    monkeypatch.setattr(storage, "resource", lambda a: {"key": "k", "format": "png", "width": 9000, "height": 9000, "bytes": 10, "etag": ""})
    huge = render_worker.request("POST", f"/jobs/{job['id']}/complete-direct", ('{"asset_id": "%s"}' % r["asset_id"]).encode())
    assert huge.status_code == 422  # dimensões acima do limite


def test_direct_upload_unavailable_falls_back(user, render_worker, monkeypatch):
    job = _job(user, render_worker)
    monkeypatch.setattr(storage, "signed_upload", lambda asset_id: None)  # sem Cloudinary
    assert render_worker.complete_direct(job["id"], b"x") is None  # o agente usa o envio normal


def test_workflow_final_size_placeholders():
    wf = {"1": {"inputs": {"text": "{{PROMPT}}", "width": "{{WIDTH}}", "height": "{{HEIGHT}}", "seed": "{{SEED}}"}},
          "2": {"inputs": {"width": "{{FINAL_WIDTH}}", "height": "{{FINAL_HEIGHT}}"}}}
    out = acer_agent.fill_workflow(wf, "p", 1, 1344, 768, final=(3840, 2160))
    assert out["1"]["inputs"]["width"] == 1344 and out["2"]["inputs"] == {"width": 3840, "height": 2160}
    assert acer_agent.fill_workflow(wf, "p", 1, 512, 512)["2"]["inputs"] == {"width": 512, "height": 512}
