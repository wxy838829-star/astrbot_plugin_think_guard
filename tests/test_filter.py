from astrbot_plugin_think_guard.thinkguard.chain import filter_chain, filter_llm_response
from astrbot_plugin_think_guard.thinkguard.stream import LeadingThought, sanitize_leading


def _feed(chunks, **kw):
    g = LeadingThought(**kw)
    out = [g.push(c, final=False) for c in chunks]
    out.append(g.flush())
    return "".join(out)


def test_tag_split_across_chunks():
    # "<thi" + "nking>" 这种切法不能被当成正文
    assert _feed(["<thi", "nking>秘密</thi", "nking>\n\n你好"]).strip() == "你好"


def test_partial_tag_then_plain():
    # 和 Go 版一致：切了一半的标签后面接正文，说明它不是标签，原样放行
    assert _feed(["<th", "这是正文"]).strip() == "<th这是正文"


def test_no_tag_untouched():
    text = "普通回复，一个字符都不该变。"
    assert sanitize_leading(text) == text


def test_leading_whitespace_before_tag():
    assert _feed(["\n\n  <analysis>想</analysis>答案"]).strip() == "答案"


def test_internal_summary_only():
    s = "<summary>primary request and intent: x</summary>正文"
    assert sanitize_leading(s).strip() == "正文"


def test_normal_summary_kept():
    s = "<summary>今天天气不错</summary>正文"
    assert s in sanitize_leading(s)


def test_mid_text_tag_kept():
    s = "看这个例子：<think>abc</think> 懂了吗"
    assert sanitize_leading(s) == s


def test_unclosed_tag_swallows_rest():
    assert _feed(["<thinking>只有推理，没有答案"]).strip() == ""


def test_chunk_after_visible_passthrough():
    g = LeadingThought()
    assert g.push("正文", final=False) == "正文"
    assert g.push("<think>x</think>", final=False) == "<think>x</think>"


def test_chain_strips_first_plain_only():
    from astrbot.api.message_components import Plain

    chain = [Plain("<think>内部</think>你好"), Plain("<think>不要动</think>")]
    changed, dropped = filter_chain(chain)
    assert changed is True and dropped > 0
    assert chain[0].text == "你好"
    assert chain[1].text == "<think>不要动</think>"


def test_chain_without_tag_unchanged():
    from astrbot.api.message_components import Plain

    chain = [Plain("纯正文")]
    changed, dropped = filter_chain(chain)
    assert changed is False and dropped == 0
    assert chain[0].text == "纯正文"


def test_llm_response_object():
    from astrbot.api.provider import LLMResponse

    resp = LLMResponse("assistant", completion_text="<analysis>想</analysis>答案")
    changed, _ = filter_llm_response(resp)
    assert changed is True
    assert resp.completion_text.strip() == "答案"


def test_llm_response_no_chain_text_field():
    from astrbot.api.provider import LLMResponse

    resp = LLMResponse("assistant")
    resp._completion_text = "<think>x</think>ok"
    changed, _ = filter_llm_response(resp)
    assert changed is True
    assert resp.completion_text.strip() == "ok"


def test_summary_split_across_chunks():
    # "<su" + "mmary>" + 内部标记，跨块也要识别
    assert _feed(["<su", "mmary>primary request and intent: x</su", "mmary>正文"]).strip() == "正文"


def test_plain_tag_prefix_flush_at_end():
    # 流结束还停在 "<thi"，final 放行，不能把用户内容吃掉
    assert _feed(["<thi"]).strip() == "<thi"
