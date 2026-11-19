"""把过滤状态机挂到 provider 的流式出口上。

Go 版在 SSE 帧里逐个 ``delta.content`` 过滤；AstrBot 的等价位置是
``Provider.text_chat_stream`` 的 yield 点——再往后框架就已经把 chunk 交给
平台发出去了，插件只能在更前面拦。

这里用实例级 monkey-patch：只改 provider 实例，不动类，卸载时删掉实例属性
即可完全还原。
"""

from __future__ import annotations

import inspect
from typing import Any

from .chain import filter_llm_response, response_has_tool_call
from .stream import DEFAULT_TAGS, SUMMARY_MARKER, LeadingThought

_CHUNK_MARK = "__thinkguard_chunk_guard"

_PATCH_FLAG = "__thinkguard_patched"
_ORIGINAL_ATTRS = (
    "text_chat",
    "text_chat_stream",
)


def _plain_chain(resp: Any):
    chain = getattr(resp, "result_chain", None)
    if chain is None:
        return None
    return getattr(chain, "chain", None)


def _rewrite_chunk(resp: Any, text: str) -> None:
    """把 chunk 的文字换掉，保留其它组件（图片、JSON 之类）。"""
    chain = _plain_chain(resp)
    if chain is not None:
        replaced = False
        for comp in chain:
            if hasattr(comp, "text"):
                comp.text = text
                replaced = True
                break
        if replaced:
            return
        try:
            from astrbot.api.message_components import Plain  # 延迟导入

            chain.insert(0, Plain(text))
            return
        except Exception:
            pass
    try:
        resp.completion_text = text
    except Exception:
        pass


def _chunk_text(resp: Any) -> str:
    chain = _plain_chain(resp)
    if chain:
        for comp in chain:
            text = getattr(comp, "text", None)
            if text:
                return text
    try:
        return resp.completion_text or ""
    except Exception:
        return ""


def _make_guard(cfg: dict) -> LeadingThought:
    return LeadingThought(
        cfg.get("tags") or DEFAULT_TAGS,
        strip_summary=bool(cfg.get("strip_summary", True)),
        summary_marker=cfg.get("summary_marker") or SUMMARY_MARKER,
    )


def wrap_stream(stream_fn, cfg: dict):
    """包一层异步生成器：逐块过滤，空块直接不往下发。"""

    async def _wrapped(*args, **kwargs):
        guard = _make_guard(cfg)
        async for resp in stream_fn(*args, **kwargs):
            if getattr(resp, "is_chunk", False):
                original = _chunk_text(resp)
                if not original:
                    yield resp
                    continue
                out = guard.push(original, final=False)
                if out != original:
                    _rewrite_chunk(resp, out)
                if not out and not guard.visible:
                    # 整块都是被吞掉的开头推理，不必让下游看见空 delta
                    continue
                yield resp
                continue

            # 流末尾的完整响应：整段重新过一遍（final=True），顺带兜住
            # 那些不按 chunk 输出、只在末尾给全文的 provider。
            _sanitize_final(resp, cfg)
            yield resp

    _wrapped.__wrapped__ = stream_fn
    return _wrapped


def wrap_chat(chat_fn, cfg: dict):
    async def _wrapped(*args, **kwargs):
        resp = await chat_fn(*args, **kwargs)
        _sanitize_final(resp, cfg)
        return resp

    _wrapped.__wrapped__ = chat_fn
    return _wrapped


def _sanitize_final(resp: Any, cfg: dict) -> None:
    if resp is None:
        return
    if response_has_tool_call(resp):
        # 有工具调用时正文通常是空的，别动它
        return
    filter_llm_response(
        resp,
        tags=cfg.get("tags") or DEFAULT_TAGS,
        strip_summary=bool(cfg.get("strip_summary", True)),
        summary_marker=cfg.get("summary_marker") or SUMMARY_MARKER,
    )


def patch_provider(provider: Any, cfg: dict) -> bool:
    """给单个 provider 实例装上过滤。返回是否真的装上了。"""
    if provider is None or getattr(provider, _PATCH_FLAG, False):
        return False

    originals: dict[str, Any] = {}
    stream = getattr(provider, "text_chat_stream", None)
    if callable(stream):
        originals["text_chat_stream"] = stream
        setattr(provider, "text_chat_stream", wrap_stream(stream, cfg))

    chat = getattr(provider, "text_chat", None)
    if callable(chat) and inspect.iscoroutinefunction(chat):
        originals["text_chat"] = chat
        setattr(provider, "text_chat", wrap_chat(chat, cfg))

    if not originals:
        return False

    setattr(provider, "__thinkguard_originals", originals)
    setattr(provider, _PATCH_FLAG, True)
    return True


def unpatch_provider(provider: Any) -> bool:
    if provider is None or not getattr(provider, _PATCH_FLAG, False):
        return False
    originals = getattr(provider, "__thinkguard_originals", {}) or {}
    for name, fn in originals.items():
        try:
            setattr(provider, name, fn)
        except Exception:
            pass
    for name in _ORIGINAL_ATTRS:
        if name not in originals and name in provider.__dict__:
            try:
                delattr(provider, name)
            except Exception:
                pass
    try:
        delattr(provider, "__thinkguard_originals")
    except Exception:
        pass
    setattr(provider, _PATCH_FLAG, False)
    return True


def patch_all(providers, cfg: dict) -> int:
    return sum(1 for p in providers or [] if patch_provider(p, cfg))


def unpatch_all(providers) -> int:
    return sum(1 for p in providers or [] if unpatch_provider(p))
