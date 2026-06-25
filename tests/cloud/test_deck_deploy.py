"""Tests for the pure-Python deck deployer (Workers Static Assets, no wrangler).

The Cloudflare HTTP flow is mocked — we validate the orchestration: verify →
account → subdomain → existence → upload-session → bucket upload → PUT → enable,
plus the manifest hashing.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from navig.cloud import deck_deploy as dd
from navig.cloud import lighthouse_deploy as ld


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _ok(result):
    return {"success": True, "errors": [], "result": result}


class FakeCF:
    """Scripted Cloudflare API for both deck_deploy and lighthouse_deploy."""

    def __init__(self, subdomain="sub", exists=False):
        self.subdomain = subdomain
        self.exists = exists
        self.calls: list[tuple[str, str]] = []
        self.uploaded_buckets: list[list[str]] = []
        self.put_metadata: dict | None = None

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        if "/user/tokens/verify" in url:
            return FakeResp(200, _ok({"status": "active"}))
        if url.endswith("/accounts"):
            return FakeResp(200, _ok([{"id": "acc123", "name": "Me"}]))
        if "/workers/subdomain" in url:
            return FakeResp(200, _ok({"subdomain": self.subdomain}))
        if "/workers/scripts/" in url:  # script_exists
            return FakeResp(200 if self.exists else 404, _ok(None))
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        if "/assets-upload-session" in url:
            manifest = json.loads(kw["data"]).get("manifest", {})
            hashes = [v["hash"] for v in manifest.values()]
            # Ask for ALL hashes back in one bucket so the upload path runs.
            return FakeResp(200, _ok({"jwt": "session-jwt", "buckets": [hashes] if hashes else []}))
        if "/workers/assets/upload" in url:
            self.uploaded_buckets.append(list(kw.get("files", {}).keys()))
            return FakeResp(200, _ok({"jwt": "completion-jwt"}))
        if "/subdomain" in url:  # enable_workers_dev
            return FakeResp(200, _ok({"enabled": True}))
        raise AssertionError(f"unexpected POST {url}")

    def put(self, url, **kw):
        self.calls.append(("PUT", url))
        files = kw.get("files", {})
        self.put_metadata = json.loads(files["metadata"][1]) if "metadata" in files else None
        return FakeResp(200, _ok({"id": "navig-deck"}))


@pytest.fixture
def out_dir(tmp_path):
    d = tmp_path / "out"
    (d / "_next" / "static").mkdir(parents=True)
    (d / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    (d / "404.html").write_text("<html>nope</html>", encoding="utf-8")
    (d / "_next" / "static" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return d


def test_build_manifest_hashes_and_paths(out_dir):
    manifest, files_by_hash = dd.build_manifest(out_dir)
    assert set(manifest) == {"/index.html", "/404.html", "/_next/static/app.js"}
    raw = (out_dir / "index.html").read_bytes()
    expected = hashlib.sha256(raw).hexdigest()[:32]
    assert manifest["/index.html"]["hash"] == expected
    assert manifest["/index.html"]["size"] == len(raw)
    # files_by_hash round-trips to the same content.
    path, data, ct = files_by_hash[expected]
    assert data == raw and ct == "text/html"


def test_deploy_full_flow(out_dir, monkeypatch):
    fake = FakeCF(subdomain="studio-2bf", exists=False)
    monkeypatch.setattr(dd, "requests", fake)
    monkeypatch.setattr(ld, "requests", fake)

    result = dd.deploy(out_dir, token="tok", worker_name="navig-deck")

    assert result.url == "https://navig-deck.studio-2bf.workers.dev"
    assert result.account_id == "acc123"
    assert result.created is True
    assert result.files == 3
    # All three assets were uploaded in the bucket.
    assert sum(len(b) for b in fake.uploaded_buckets) == 3
    # The Worker was deployed with the assets completion jwt + ASSETS binding.
    assert fake.put_metadata["assets"]["jwt"] == "completion-jwt"
    assert fake.put_metadata["bindings"][0]["type"] == "assets"


def test_deploy_skips_upload_when_no_buckets(out_dir, monkeypatch):
    fake = FakeCF()

    # Session reports nothing missing → completion jwt is the session jwt, no upload.
    def post(url, **kw):
        fake.calls.append(("POST", url))
        if "/assets-upload-session" in url:
            return FakeResp(200, _ok({"jwt": "already-have-it", "buckets": []}))
        if "/subdomain" in url:
            return FakeResp(200, _ok({"enabled": True}))
        raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(dd, "requests", fake)
    monkeypatch.setattr(ld, "requests", fake)
    monkeypatch.setattr(fake, "post", post)

    result = dd.deploy(out_dir, token="tok")
    assert result.url.endswith(".workers.dev")
    assert fake.put_metadata["assets"]["jwt"] == "already-have-it"
    assert fake.uploaded_buckets == []  # nothing uploaded


def test_deploy_missing_out_dir_raises(tmp_path):
    with pytest.raises(ld.DeployError, match="build output"):
        dd.deploy(tmp_path / "nope", token="tok")
