"""A small in-memory briq API on `httpx.MockTransport`: the routes `BriqSandbox` uses.

Answers have the shapes of the real API (the `briq-cloud` client reads them). A test sets
`on_exec` to say what a command prints, and reads `requests` to see what was sent.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from briq_cloud import Briq

KEY = "test-key"


def error(status: int, code: str, message: str) -> httpx.Response:
    body = {"error": {"code": code, "message": message, "requestId": "req_1"}}
    return httpx.Response(status, json=body, headers={"x-request-id": "req_1"})


@dataclass
class Out:
    stdout: str = ""
    stderr: str = ""
    code: int = 0
    timed_out: bool = False


@dataclass
class FakeBriq:
    literal_secrets: bool = True
    on_exec: Callable[[list[str], dict[str, Any]], Out] = lambda cmd, body: Out(stdout="ok\n")
    #: Paths whose write the API refuses, with the message of the refusal.
    write_refusals: dict[str, str] = field(default_factory=dict)
    requests: list[tuple[str, str, Any]] = field(default_factory=list)
    sandboxes: dict[str, dict[str, Any]] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    execs: dict[str, Out] = field(default_factory=dict)

    def client(self) -> Briq:
        return Briq(
            KEY, base_url="https://api.briq.test", transport=httpx.MockTransport(self.handle)
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode().split("?")[0]
        ctype = request.headers.get("content-type", "")
        body = (
            json.loads(request.content) if ctype.startswith("application/json") else request.content
        )
        self.requests.append((request.method, path, body))
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return error(401, "unauthorized", "Bad key")
        sandbox = r"/v1/briqs/([^/]+)"
        routes: list[tuple[str, str, Callable[..., httpx.Response]]] = [
            ("GET", r"/v1/quota", lambda r, b: self._quota()),
            ("POST", r"/v1/briqs", lambda r, b: self._create(b)),
            ("GET", sandbox, lambda r, b, id: self._get(id)),
            ("DELETE", sandbox, lambda r, b, id: self._destroy(id)),
            ("POST", sandbox + "/exec", lambda r, b, id: self._exec(id, b)),
            ("GET", sandbox + "/execs/([^/]+)", self._read_exec),
            (
                "DELETE",
                sandbox + "/execs/([^/]+)",
                lambda r, b, id, ex: httpx.Response(200, json={}),
            ),
            ("PUT", sandbox + "/files", lambda r, b, id: self._put(r, b, id)),
            ("GET", sandbox + "/files/raw", lambda r, b, id: self._raw(r, id)),
        ]
        for method, pattern, handler in routes:
            match = re.fullmatch(pattern, path)
            if request.method == method and match:
                return handler(request, body, *match.groups())
        return error(404, "not_found", "No such route")

    def sent(self, method: str, pattern: str) -> list[Any]:
        return [b for m, p, b in self.requests if m == method and re.fullmatch(pattern, p)]

    def _quota(self) -> httpx.Response:
        features = {"literalEnvSecrets": self.literal_secrets}
        return httpx.Response(200, json={"features": features, "team": {"id": "team_1"}})

    def _create(self, body: Any) -> httpx.Response:
        id = f"bq_{len(self.sandboxes) + 1}"
        self.sandboxes[id] = {
            "id": id,
            "name": body.get("name", "sandbox-1"),
            "image": body.get("image", "ghcr.io/briq-cloud/sandbox:latest"),
            "size": body.get("size", "medium"),
            "state": "running",
            "sleep": body.get("sleep", "30m"),
            "createdAt": "2026-11-06T10:00:00.000Z",
            "sleepsAt": None,
            "costCentsSoFar": 0.0,
            "error": None,
            "teamId": "team_1",
        }
        payload = {
            "briqs": [self.sandboxes[id]],
            "outputs": {},
            "recipe": "sandbox",
            "costLine": "",
        }
        return httpx.Response(201, json=payload)

    def _get(self, id: str) -> httpx.Response:
        if id not in self.sandboxes:
            return error(404, "not_found", "No such sandbox")
        return httpx.Response(200, json={"briq": self.sandboxes[id]})

    def _destroy(self, id: str) -> httpx.Response:
        if id not in self.sandboxes:
            return error(404, "not_found", "No such sandbox")
        row = self.sandboxes.pop(id)
        return httpx.Response(200, json={"briq": {**row, "state": "destroyed"}})

    def _exec(self, id: str, body: Any) -> httpx.Response:
        if id not in self.sandboxes:
            return error(404, "not_found", "No such sandbox")
        out = self.on_exec(body["cmd"], body)
        if body.get("async"):  # a command that may run over a minute is followed, not awaited
            ex_id = f"ex_{len(self.execs) + 1}"
            self.execs[ex_id] = out
            return httpx.Response(202, json={"exec": {"id": ex_id, "state": "running"}})
        payload = {"stdout": out.stdout, "stderr": out.stderr, "exitCode": out.code,
                   "timedOut": out.timed_out, "truncated": False, "costLine": ""}  # fmt: skip
        return httpx.Response(200, json=payload)

    def _read_exec(self, request: httpx.Request, body: Any, id: str, ex_id: str) -> httpx.Response:
        out = self.execs[ex_id]
        stream = request.url.params["stream"]
        text = out.stdout if stream == "stdout" else out.stderr
        read = {"stream": stream, "offset": 0, "data": text, "nextOffset": len(text),
                "bytes": len(text), "eof": True}  # fmt: skip
        state = "timed_out" if out.timed_out else "exited"
        exec_ = {"id": ex_id, "state": state, "exitCode": out.code, "read": read}
        return httpx.Response(200, json={"exec": exec_})

    def _put(self, request: httpx.Request, body: Any, id: str) -> httpx.Response:
        path = request.url.params["path"]
        if path in self.write_refusals:
            return error(500, "provider_error", self.write_refusals[path])
        self.files[path] = body
        digest = hashlib.sha256(body).hexdigest()
        stored = {"path": path, "bytes": len(body), "sha256": digest, "verified": True}
        return httpx.Response(200, json=stored)

    def _raw(self, request: httpx.Request, id: str) -> httpx.Response:
        path = request.url.params["path"]
        if path not in self.files:
            return error(404, "not_found", "read failed: No such file or directory")
        return httpx.Response(200, content=self.files[path])
