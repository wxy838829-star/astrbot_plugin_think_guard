"""think_guard —— 挡住「开头的内部推理」漏进聊天正文。

和 Go 版 ``leadingThought`` 同一套规则，落到 AstrBot 的三个拦截点上：

1. ``on_llm_response``：模型刚回完，先洗一遍最终响应（非流式的主要出口）；
2. ``on_decorating_result``：消息真的要发出去之前，再洗一遍消息链
   （兜住流式结束、第三方 runner、以及别的插件拼进来的文本）；
3. provider 实例补丁：把 ``text_chat_stream`` 的 chunk 逐块过滤，
   对应 Go 版在 SSE 帧里过滤 ``delta.content`` 的位置。

三条路都只处理「开头显式标签」：``<analysis>`` / ``<thinking>`` /
``<think>`` / 带 "primary request and intent" 的 ``<summary>``。正文里
出现的标签不动，无标签的普通回复一个字符不改。
"""

from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star

from .thinkguard.chain import filter_chain, filter_llm_response, response_has_tool_call
from .thinkguard.provider_patch import patch_all, unpatch_all

DEFAULT_CONFIG = {
    "enabled": True,
    "tags": ["<analysis>", "<thinking>", "<think>"],
    "strip_summary": True,
    "patch_providers": True,
    "handle_llm_response": True,
    "handle_result_chain": True,
    "debug": False,
}


class ThinkGuard(Star):
    """think_guard —— 挡住开头的内部推理漏进正文。

    规则：只吞开头显式标签（<analysis>/<thinking>/<think>）和内部 <summary>，
    正文中间的同名标签与无标签回复都不动。
    """

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config
        self._patched: list = []
        self._stats = {"llm_response": 0, "chain": 0, "chunk": 0, "dropped_chars": 0}

    # ------------------------------------------------------------ 配置读取

    def _cfg(self, key: str, default=None):
        cfg = getattr(self, "config", None)
        if cfg is None:
            return DEFAULT_CONFIG.get(key, default)
        try:
            value = cfg.get(key, default)
        except Exception:
            return DEFAULT_CONFIG.get(key, default)
        return DEFAULT_CONFIG.get(key, default) if value is None else value

    def _guard_cfg(self) -> dict:
        tags = self._cfg("tags") or DEFAULT_CONFIG["tags"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        return {
            "tags": [str(t) for t in tags],
            "strip_summary": bool(self._cfg("strip_summary", True)),
        }

    # ------------------------------------------------------------ provider 补丁

    async def _ensure_patched(self) -> None:
        if not self._cfg("enabled", True) or not self._cfg("patch_providers", True):
            return
        try:
            providers = self.context.get_all_providers()
        except Exception as exc:  # pragma: no cover - 依赖宿主实现
            logger.debug(f"[think_guard] 取 provider 列表失败: {exc}")
            return
        done = set(map(id, self._patched))
        for prov in providers or []:
            if id(prov) in done:
                continue
            if patch_all([prov], self._guard_cfg()):
                self._patched.append(prov)
        if self._patched and self._cfg("debug", False):
            logger.info(f"[think_guard] 已接管 {len(self._patched)} 个 provider 的流式出口")

    async def initialize(self) -> None:
        await self._ensure_patched()

    # ------------------------------------------------------------ 拦截点 1：LLM 响应

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        if not self._cfg("enabled", True) or not self._cfg("handle_llm_response", True):
            return
        if resp is None or getattr(resp, "is_chunk", False):
            return
        if response_has_tool_call(resp):
            return
        try:
            changed, dropped = filter_llm_response(resp, **self._guard_cfg())
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[think_guard] 响应过滤异常，已跳过: {exc}")
            return
        if changed:
            self._stats["llm_response"] += 1
            self._stats["dropped_chars"] += dropped
            if self._cfg("debug", False):
                logger.info(f"[think_guard] 响应开头吞掉 {dropped} 字符")

    # ------------------------------------------------------------ 拦截点 2：发出去之前

    @filter.on_decorating_result(priority=-1000000)
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        if not self._cfg("enabled", True) or not self._cfg("handle_result_chain", True):
            return
        result = event.get_result()
        chain = getattr(result, "chain", None) if result is not None else None
        if not chain:
            return
        if not any(isinstance(c, Plain) for c in chain):
            return
        try:
            changed, dropped = filter_chain(chain, **self._guard_cfg())
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[think_guard] 消息链过滤异常，已跳过: {exc}")
            return
        if not changed:
            return
        self._stats["chain"] += 1
        self._stats["dropped_chars"] += dropped
        if not any((getattr(c, "text", "") or "").strip() for c in chain if isinstance(c, Plain)):
            # 整条只剩被吞掉的推理：不要发一条空气泡
            kept = [c for c in chain if not isinstance(c, Plain)]
            chain[:] = kept or [Plain("（这条回复只有内部推理，已拦下）")]
        if self._cfg("debug", False):
            logger.info(f"[think_guard] 消息链开头吞掉 {dropped} 字符")

    # ------------------------------------------------------------ 指令

    @filter.command("thinkguard")
    async def thinkguard_cmd(self, event: AstrMessageEvent, action: str = ""):
        """thinkguard —— 查看状态 / 重载 provider 补丁。"""
        action = (action or "").strip().lower()
        if action in ("reload", "重载", "刷新"):
            await self._ensure_patched()
            yield event.plain_result(
                f"已检查 provider：当前接管 {len(self._patched)} 个。"
            )
            return
        enabled = "开" if self._cfg("enabled", True) else "关"
        yield event.plain_result(
            "\n".join(
                [
                    f"think_guard 状态：{enabled}",
                    f"标签：{' '.join(self._guard_cfg()['tags'])}",
                    f"吞内部 <summary>：{'是' if self._cfg('strip_summary', True) else '否'}",
                    f"流式块过滤：{'是' if self._cfg('patch_providers', True) else '否'}",
                    f"接管 provider：{len(self._patched)} 个",
                    f"累计拦截：响应 {self._stats['llm_response']} 次 / "
                    f"消息 {self._stats['chain']} 次 / "
                    f"共 {self._stats['dropped_chars']} 字符",
                ]
            )
        )

    # ------------------------------------------------------------ 卸载

    async def terminate(self) -> None:
        unpatch_all(self._patched)
        self._patched.clear()
