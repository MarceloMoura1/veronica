import asyncio
from types import SimpleNamespace

import server


def test_visual_socket_dispatch_calls_run_task_and_returns_success(monkeypatch):
    calls, emitted = [], []

    class Agent:
        async def run_task(self, prompt, update_callback=None):
            calls.append(prompt)
            await update_callback("image", "navigated")
            return {"ok": True, "result": "Example Domain"}

    async def emit(event, payload, **kwargs): emitted.append((event, payload, kwargs))
    monkeypatch.setattr(server, "audio_loop", SimpleNamespace(web_agent=Agent()))
    monkeypatch.setattr(server.sio, "emit", emit)
    result = asyncio.run(server.prompt_web_agent("sid-1", {"prompt": "Open example.com"}))
    assert result == {"ok": True, "result": "Example Domain"}
    assert calls == ["Open example.com"]
    assert [item[0] for item in emitted] == ["status", "browser_frame", "status"]
    assert all(item[2].get("room") == "sid-1" for item in emitted)


def test_visual_socket_dispatch_surfaces_classified_failure(monkeypatch):
    emitted = []

    class Agent:
        async def run_task(self, *_args, **_kwargs):
            return {"ok": False, "error": {"code": "provider_quota_exhausted"}}

    async def emit(event, payload, **kwargs): emitted.append((event, payload))
    monkeypatch.setattr(server, "audio_loop", SimpleNamespace(web_agent=Agent()))
    monkeypatch.setattr(server.sio, "emit", emit)
    result = asyncio.run(server.prompt_web_agent("sid-1", {"prompt": "fixture"}))
    assert result["ok"] is False
    assert emitted[-1] == ("error", {"msg": "Web Agent Error: provider_quota_exhausted"})
