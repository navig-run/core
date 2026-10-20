"""navig.agent.ollama_native — the native-API client the sweep pins its models through.

It exists for two properties the OpenAI-compatible provider cannot give: ``num_ctx`` actually
reaching the model (the /v1 endpoint ignores it, silently truncating long prompts) and a
per-call timeout long enough for a 32B model on CPU (the provider caps every call at 45 s).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from navig.agent import ollama_native
from navig.agent.ollama_native import OllamaError, chat, list_models


class _Resp:
    def __init__(self, status: int, payload: dict[str, Any] | None = None, text: str = ""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)  # type: ignore[arg-type]


def test_chat_uses_the_native_api_and_sends_num_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_post(url: str, json: dict[str, Any], timeout: Any) -> _Resp:
        seen.update(url=url, payload=json, timeout=timeout)
        return _Resp(
            200,
            {
                "model": "qwen2.5-coder:7b",
                "message": {"content": "```python\nx = 1\n```"},
                "prompt_eval_count": 120,
                "eval_count": 40,
                "eval_duration": 4_000_000_000,
                "load_duration": 1_000_000_000,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(ollama_native, "ollama_base_url", lambda: "http://127.0.0.1:11434")

    reply = chat(
        "qwen2.5-coder:7b", [{"role": "user", "content": "fix"}], num_ctx=8192, timeout=1800
    )

    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert seen["payload"]["options"]["num_ctx"] == 8192
    assert seen["payload"]["stream"] is False
    assert seen["timeout"].read == 1800  # the caller's budget, not a fixed 45 s cap
    assert reply.content.startswith("```python")
    assert reply.output_tokens == 40 and reply.output_tok_per_s == pytest.approx(10.0)
    assert reply.load_s == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (_Resp(200, {"message": {"content": "   "}}), "empty reply"),
        (_Resp(404, text="model not found"), "404"),
    ],
)
def test_chat_never_returns_a_failure_as_a_reply(
    monkeypatch: pytest.MonkeyPatch, response: _Resp, match: str
) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response)
    with pytest.raises(OllamaError, match=match):
        chat("m", [{"role": "user", "content": "x"}], base_url="http://h:1")


def test_chat_timeout_is_a_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(*_a: Any, **_k: Any) -> _Resp:
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(httpx, "post", slow)
    with pytest.raises(OllamaError, match="did not answer within 5s"):
        chat("m", [{"role": "user", "content": "x"}], timeout=5, base_url="http://h:1")


def test_list_models_maps_names_to_sizes_and_reports_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "models": [{"name": "qwen2.5-coder:32b", "size": 19 * 2**30}, {"name": "", "size": 1}]
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, payload))
    assert list_models(base_url="http://h:1") == {"qwen2.5-coder:32b": 19 * 2**30}

    def down(*_a: Any, **_k: Any) -> _Resp:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", down)
    with pytest.raises(OllamaError, match="not reachable"):
        list_models(base_url="http://h:1")
