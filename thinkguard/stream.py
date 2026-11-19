"""按块（chunk）安全的思考标签过滤状态机。

这个文件是 Go 版 ``leadingThought`` 的 Python 移植，外加两处加固：

1. ``flush()``：流结束时把残留缓冲吐出来，避免最后一段被丢掉；
2. ``<summary>`` 前缀也参与“等一下再说”的判断，这样标签被切成
   ``"<su"`` / ``"mmary>"`` 两块时不会被误判成正文。

原则不变：**只吞掉开头显式带标签的内部推理**。没有标签的普通回复
一个字符都不改，可见性一旦成立就直通，不再回头删改。
"""

from __future__ import annotations

from collections.abc import Iterable

DEFAULT_TAGS: tuple[str, ...] = ("<analysis>", "<thinking>", "<think>")

SUMMARY_TAG = "<summary>"
SUMMARY_END = "</summary>"
SUMMARY_MARKER = "primary request and intent"

_LEADING_WS = " \r\n\t\ufeff"


def _closing(open_tag: str) -> str:
    return "</" + open_tag[1:]


class LeadingThought:
    """流式逐块过滤开头的显式思考标签。

    每个流（或每个 choice / 每条响应）都要新建一个实例，状态跨 chunk 保留。
    """

    def __init__(
        self,
        tags: Iterable[str] = DEFAULT_TAGS,
        *,
        strip_summary: bool = True,
        summary_marker: str = SUMMARY_MARKER,
    ) -> None:
        self.tags = tuple(t.lower() for t in tags)
        self.strip_summary = strip_summary
        self.summary_marker = summary_marker.lower()
        self.pending = ""
        self.end_tag: str | None = None
        self.visible = False
        self.saw_tag = False
        self.saw_internal_summary = False
        self.dropped_chars = 0

    # ------------------------------------------------------------------ api

    def push(self, text: str, final: bool = False) -> str:
        """喂入一个 chunk，返回这一块里可以放出去的可见文本。"""
        if self.visible:
            return text

        text = self.pending + (text or "")
        self.pending = ""

        while True:
            if self.end_tag:
                lower = text.lower()
                idx = lower.find(self.end_tag)
                if idx >= 0:
                    self.dropped_chars += idx + len(self.end_tag)
                    text = text[idx + len(self.end_tag) :]
                    self.end_tag = None
                    continue
                keep = _partial_suffix_len(lower, (self.end_tag,))
                if keep:
                    self.pending = text[len(text) - keep :]
                    self.dropped_chars += len(text) - keep
                else:
                    self.dropped_chars += len(text)
                return ""

            trimmed = text.lstrip(_LEADING_WS)
            lower = trimmed.lower()

            if self.strip_summary and lower.startswith(SUMMARY_TAG):
                if self.summary_marker in lower:
                    self.saw_tag = True
                    self.saw_internal_summary = True
                    self.dropped_chars += len(SUMMARY_TAG)
                    text = trimmed[len(SUMMARY_TAG) :]
                    self.end_tag = SUMMARY_END
                    continue
                # 有 <summary> 但不是内部摘要格式，按普通内容放行。

            if self.strip_summary and not final and _is_tag_prefix(lower, SUMMARY_TAG):
                # "<su" / "<summ" 这种，先等标签收完再判断是不是内部摘要
                if not self.tags:
                    self.pending = text
                    return ""
                if not any(lower.startswith(t) for t in self.tags):
                    self.pending = text
                    return ""

            entered = False
            for tag in self.tags:
                if lower.startswith(tag):
                    self.saw_tag = True
                    text = trimmed[len(tag) :]
                    self.end_tag = _closing(tag)
                    entered = True
                    break
                if not final and _is_tag_prefix(lower, tag):
                    self.pending = text
                    return ""
            if entered:
                continue

            self.visible = True
            if self.saw_tag:
                return trimmed
            return text

    def flush(self) -> str:
        """流结束时调用：把还没吐出去的残留按 final 规则放行。"""
        tail = self.push("", final=True)
        if self.pending:
            tail += self.pending
            self.pending = ""
        return tail

    # ----------------------------------------------------------------- meta

    @property
    def dropped_anything(self) -> bool:
        return self.dropped_chars > 0 or self.saw_tag


def _is_tag_prefix(lower: str, tag: str) -> bool:
    """``lower`` 是不是某个标签的开头（可能被切断在 chunk 边界）。"""
    if not lower:
        return True
    return tag.startswith(lower)


def _partial_suffix_len(lower: str, tags: Iterable[str]) -> int:
    """尾部多长是某个标签的前缀（用于跨 chunk 拼接）。"""
    tags = tuple(tags)
    if not tags:
        return 0
    max_len = max(len(t) for t in tags)
    for n in range(min(len(lower), max_len - 1), 0, -1):
        suffix = lower[-n:]
        if not suffix.startswith("<"):
            continue
        for tag in tags:
            if tag != suffix and tag.startswith(suffix):
                return n
    return 0


def sanitize_leading(text: str, **kwargs) -> str:
    """一次性（非流式）过滤：整段文本走同一套规则。"""
    guard = LeadingThought(**kwargs)
    return guard.push(text or "", final=True) + (guard.pending or "")
