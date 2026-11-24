"""Clients for Gradient Bench LLM.

HttpClient  talks to the running Flask app (default http://127.0.0.1:5052),
            which is how GBL's own page talks to it. Works across machines.
FolderClient uses the gradient_llm package directly on the campaign folder
            when both apps share one Python environment and GBL's page is not
            running (its /api/state would otherwise be the source of truth).

Both expose the same four calls the bridge needs:
    state()                       -> dict (GBL /api/state payload)
    pending()                     -> dict | None (the run in flight)
    upload(path, original_name)   -> dict (pending after upload)
    read()                        -> dict (pending after the model read it)   [optional]
"""
from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol


class GBLError(RuntimeError):
    pass


class Client(Protocol):
    def state(self) -> dict[str, Any]: ...
    def pending(self) -> dict[str, Any] | None: ...
    def upload(self, path: str | Path, original_name: str | None = None) -> dict[str, Any]: ...
    def read(self, timeout_s: float = 600) -> dict[str, Any]: ...


# --------------------------------------------------------------------------- HTTP
Transport = Callable[[str, str, bytes | None, dict[str, str]], tuple[int, bytes]]


def _urllib_transport(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise GBLError(f"cannot reach Gradient Bench LLM at {url}: {e.reason}") from e


class HttpClient:
    def __init__(self, base_url: str = "http://127.0.0.1:5052", transport: Transport | None = None):
        self.base = base_url.rstrip("/")
        self._t = transport or _urllib_transport

    def _call(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        status, raw = self._t(method, self.base + path, body, headers or {})
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            raise GBLError(f"GBL {path} returned non-JSON (HTTP {status}): {raw[:200]!r}")
        if status >= 400 or data.get("ok") is False:
            raise GBLError(f"GBL {path}: {data.get('error') or f'HTTP {status}'}")
        return data

    def _json(self, path: str, obj: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(obj or {}).encode()
        return self._call("POST", path, body, {"Content-Type": "application/json"})

    def state(self) -> dict[str, Any]:
        return self._call("GET", "/api/state")

    def pending(self) -> dict[str, Any] | None:
        st = self.state()
        if st.get("no_campaign"):
            return None
        return st.get("pending")

    def upload(self, path: str | Path, original_name: str | None = None) -> dict[str, Any]:
        p = Path(path)
        name = original_name or p.name
        boundary = "----wingpc" + uuid.uuid4().hex
        ctype = mimetypes.guess_type(name)[0] or "text/plain"
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
            f"Content-Type: {ctype}\r\n\r\n".encode(),
            p.read_bytes(), b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        data = self._call("POST", "/api/upload", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return data.get("pending") or {}

    def _job(self, path: str, timeout_s: float) -> dict[str, Any]:
        job = self._json(path)["job"]
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            j = self._call("GET", f"/api/jobs/{job['id']}")["job"]
            if j["state"] == "done":
                return j.get("result") or {}
            if j["state"] == "error":
                raise GBLError(f"GBL job {path} failed: {j.get('error')}")
            time.sleep(0.5)
        raise GBLError(f"GBL job {path} did not finish in {timeout_s:.0f}s")

    def read(self, timeout_s: float = 600) -> dict[str, Any]:
        return self._job("/api/read", timeout_s).get("pending") or {}

    def suggest(self, timeout_s: float = 600) -> dict[str, Any]:
        return self._job("/api/suggest", timeout_s).get("pending") or {}

    def record(self, verdict: str | None = None, note: str = "") -> dict[str, Any]:
        return self._json("/api/record", {"verdict": verdict, "note": note})


# --------------------------------------------------------------------------- folder
class FolderClient:
    """Direct use of the gradient_llm package on a campaign folder.

    Only for the case where GBL's web app is NOT running against the same
    folder (two writers of state.json would race)."""

    def __init__(self, campaign_dir: str | Path):
        try:
            from gradient_llm import loop as L            # type: ignore
            from gradient_llm.store import campaign as C  # type: ignore
        except ImportError as e:
            raise GBLError("gradient_llm is not importable here; install Gradient Bench LLM in this environment "
                           "or use the HTTP client") from e
        self._L, self._C = L, C
        self.c = C.open_campaign(campaign_dir)

    def state(self) -> dict[str, Any]:
        return {"campaign": self.c.summary(), "pending": self.pending(),
                "next": self._L.next_step(self.c).__dict__, "limits": self.c.limits(), "info": self.c.info()}

    def pending(self) -> dict[str, Any] | None:
        p = self.c.pending()
        if p is None:
            return None
        return {**p, "step": self._L.pending_step(p)}

    def upload(self, path: str | Path, original_name: str | None = None) -> dict[str, Any]:
        return self._L.add_upload(self.c, path, original_name or Path(path).name)

    def read(self, timeout_s: float = 600) -> dict[str, Any]:
        raise GBLError("FolderClient cannot call the model; use the HTTP client or GBL's page for the read step")
