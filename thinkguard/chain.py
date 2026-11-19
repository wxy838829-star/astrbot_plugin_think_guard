"""消息链层面的处理：把 ``Plain`` 开头的思考标签剪掉。

只做"开头"这一件事，理由和 stream.py 一致：中间的 ``<think>`` 有可能是
用户要求模型复述的内容，全局删除会误伤。
"""

from __future__ import annotations

from typing import Any

from .stream import DEFAULT_TAGS, SUMMARY_MARKER, LeadingThought


def _plain_text_get(comp: Any) -> str | None:
    return getattr(comp, "text", None)


def filter_chain(
    chain: Any,
    *,
    tags=DEFAULT_TAGS,
    strip_summary: bool = True,
    summary_marker: str = SUMMARY_MARKER,
) -> tuple[bool, int]:
    """就地过滤消息链开头的思考标签。

    返回 ``(changed, dropped_chars)``。只有最前面连续的 ``Plain`` 组件参与
    判断，遇到第一个非文本组件就停止（图片、At 之类不该被误删）。
    """
    if not chain:
        return False, 0

    guard = LeadingThought(tags, strip_summary=strip_summary, summary_marker=summary_marker)
    changed = False
    started = False

    for comp in chain:
        text = _plain_text_get(comp)
        if text is None:
            if not started:
                # 开头先出现非文本组件，说明这条消息不是"思考在前"的结构
                break
            continue
        original = text
        out = guard.push(text, final=True)
        if not started and guard.pending:
            out += guard.pending
            guard.pending = ""
        if out != original:
            comp.text = out
            changed = True
        if guard.visible:
            started = True
            break

    return changed, guard.dropped_chars


def filter_llm_response(
    resp: Any,
    *,
    tags=DEFAULT_TAGS,
    strip_summary: bool = True,
    summary_marker: str = SUMMARY_MARKER,
) -> tuple[bool, int]:
    """过滤 ``LLMResponse``（最终响应或单个 chunk）。

    同时兼顾两种存放方式：``result_chain`` 里的 ``Plain`` 组件，以及
    没有 chain 时的 ``_completion_text``。
    """
    chain = getattr(resp, "result_chain", None)
    if chain is not None and getattr(chain, "chain", None):
        return filter_chain(
            chain.chain,
            tags=tags,
            strip_summary=strip_summary,
            summary_marker=summary_marker,
        )

    text = getattr(resp, "_completion_text", "") or ""
    if not text:
        return False, 0
    guard = LeadingThought(tags, strip_summary=strip_summary, summary_marker=summary_marker)
    out = guard.push(text, final=True) + (guard.pending or "")
    if out != text:
        try:
            resp.completion_text = out
        except Exception:
            return False, 0
        return True, guard.dropped_chars
    return False, 0


def response_has_tool_call(resp: Any) -> bool:
    names = getattr(resp, "tools_call_name", None) or []
    return any(str(n).strip() for n in names)


def response_text(resp: Any) -> str:
    try:
        return resp.completion_text or ""
    except Exception:
        return ""
