# 思考外露拦截 · astrbot_plugin_think_guard

把「开头的内部推理」挡在聊天正文之外。规则照搬 Go 版 `leadingThought`，但接在 AstrBot 的三个位置上。

## 为什么不是简单正则

`<think>` 这类标签经常被 SSE 切成 `"<thi"` + `"nking>"`，一次性正则会漏，也会把正文中间的同名标签误删。所以这里用**逐块状态机**：

- 只有回复**开头**出现 `</analysis>` `</thinking>` `</think>` 才吞；
- 开头带 `<summary>` 且内含 `primary request and intent` 才当内部摘要吞掉；
- 可见性一旦成立就直通，不再回头删改；
- 跨块被切断的标签（`"<thi"` 后接 `"nking>"`）能拼回来。

## 三个拦截点

| 位置 | 作用 |
| --- | --- |
| `on_llm_response` | 最终响应，主要覆盖非流式与第三方 runner |
| `on_decorating_result` | 发出去前的消息链，兜住拼进来的文本 |
| provider 补丁 | 接管 `text_chat_stream`，chunk 级过滤，流式也不漏 |

provider 补丁只改实例属性，`terminate()` 时还原，不动类。

## 配置

`_conf_schema.json` 里：`enabled`、`tags`、`strip_summary`、`patch_providers`、`handle_llm_response`、`handle_result_chain`、`debug`。默认全开，`debug` 关。

## 指令

- `/thinkguard` 看状态与累计拦截量
- `/thinkguard reload` 重装 provider 补丁（改了流式开关后用）

## 安装

面板 → 插件 → 上传 `astrbot_plugin_think_guard.zip`，或直接把目录丢进 `data/plugins/`。

## 测试

```bash
pip install pytest
python -m pytest tests -q
```

`tests/` 用真实 `astrbot` 的 `LLMResponse` / `MessageChain` / `Plain`，覆盖标签切块、纯正文不变、中间标签不删、provider 补丁可逆。
