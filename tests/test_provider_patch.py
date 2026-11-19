from astrbot.api.provider import LLMResponse
from astrbot_plugin_think_guard.thinkguard import provider_patch


class FakeProvider:
    """模拟 provider 的流式出口：先吐思考，再吐正文，最后给完整响应。"""

    def __init__(self, stream_pieces, final_text):
        self.stream_pieces = stream_pieces
        self.final_text = final_text
        self.calls = 0

    async def text_chat_stream(self, **kwargs):
        self.calls += 1
        for piece in self.stream_pieces:
            yield LLMResponse(
                "assistant", completion_text=piece, is_chunk=True
            )
        yield LLMResponse("assistant", completion_text=self.final_text)

    async def text_chat(self, **kwargs):
        return LLMResponse("assistant", completion_text=self.final_text)


async def _drain(agen):
    out = []
    async for r in agen:
        out.append(("chunk" if r.is_chunk else "final", r.completion_text))
    return out


def test_stream_chunks_filtered_and_final_clean():
    import asyncio

    prov = FakeProvider(
        ["<analysis>", "内心戏", "</analysis>", "你好", "，世界"],
        "<analysis>内心戏</analysis>你好，世界",
    )
    assert provider_patch.patch_provider(prov, {}) is True
    rows = asyncio.run(_drain(prov.text_chat_stream()))
    text = "".join(t for _, t in rows)
    assert "内心戏" not in text
    assert "你好，世界" in text
    final = [t for kind, t in rows if kind == "final"][0]
    assert "内心戏" not in final


def test_plain_stream_untouched():
    import asyncio

    prov = FakeProvider(["普通", "回复"], "普通回复")
    provider_patch.patch_provider(prov, {})
    rows = asyncio.run(_drain(prov.text_chat_stream()))
    chunks = "".join(t for kind, t in rows if kind == "chunk")
    assert chunks == "普通回复"


def test_patch_is_idempotent_and_reversible():
    import asyncio

    prov = FakeProvider(["<think>x</think>ok"], "<think>x</think>ok")
    original = prov.text_chat_stream
    assert provider_patch.patch_provider(prov, {}) is True
    assert provider_patch.patch_provider(prov, {}) is False
    assert provider_patch.unpatch_provider(prov) is True
    assert prov.text_chat_stream.__func__ is FakeProvider.text_chat_stream
    rows = asyncio.run(_drain(prov.text_chat_stream()))
    assert "x" in "".join(t for _, t in rows)


def test_non_stream_chat_filtered():
    import asyncio

    prov = FakeProvider([], "<summary>primary request and intent: x</summary>答案")
    provider_patch.patch_provider(prov, {})
    resp = asyncio.run(prov.text_chat())
    assert resp.completion_text.strip() == "答案"
