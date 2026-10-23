"""按 JSON 字节限制模型上下文，只按完整历史轮次裁剪。"""

import json

from minicode.tools.registry import tool_definitions


MAX_CONTEXT_BYTES = 128 * 1024
_HISTORY_NOTICE = "较早的部分对话已因上下文限制省略；不要假定仍记得被省略的内容。"


class ContextLimitError(ValueError):
    """当前轮本身超限，不能通过移除历史解决。"""


def context_bytes(messages: list[dict]) -> int:
    """计算一次请求中消息与工具说明占用的 JSON 字节数。

    输入：messages 是已准备发送的消息列表，包括 system、user、assistant、tool。
    输出：messages 和当前工具说明组成的紧凑 UTF-8 JSON 的字节数（整数）。
    不修改消息；不包含模型名称等其他请求参数，也不是 token 计数。
    """
    return len(
        json.dumps(
            {"messages": messages, "tools": tool_definitions()},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def select_context(
    system_prompt: str,
    history_turns: list[list[dict]],
    current_turn: list[dict],
    max_bytes: int = MAX_CONTEXT_BYTES,
    *,
    history_omitted: bool = False,
) -> tuple[list[dict], int]:
    """选择预算内的完整消息，必要时按整轮省略最早历史。

    输入：
        system_prompt：应用规则文本，放在请求的第一条 system 消息中。
        history_turns：按成功轮次分组的旧历史，每个元素是一轮消息列表。
        current_turn：当前问题及本轮已产生的 assistant/tool 消息，必须完整保留。
        max_bytes：消息和工具说明的 JSON 字节上限，默认 128 KiB。
        history_omitted：本轮此前是否已省略旧历史；只能使用参数名传入。
    输出：(messages, dropped)，分别是本次请求消息和本次移除的旧轮次数。
    异常：上限不是正整数时抛出 ValueError；当前轮仍超限时抛出 ContextLimitError。
    不修改输入；保留最新历史的连续后缀，省略提示本身也计入预算。
    """
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("上下文上限必须是正整数。")
    # dropped 是本次尝试省略的最早轮次数；从 0 开始，先尝试保留全部历史。
    for dropped in range(len(history_turns) + 1):
        content = system_prompt
        if history_omitted or dropped:
            content += _HISTORY_NOTICE
        # 应用规则始终保留，只选择旧历史的后缀；本轮消息不参与裁剪。
        messages = [{"role": "system", "content": content}]
        for turn in history_turns[dropped:]:
            messages.extend(turn)
        messages.extend(current_turn)
        # 返回二元组，调用方可用 messages, dropped = select_context(...) 分别接收。
        if context_bytes(messages) <= max_bytes:
            return messages, dropped
    raise ContextLimitError(
        f"当前问题或本轮工具消息超过上下文上限（{max_bytes} 字节），未发送本次请求。"
        "请缩短问题或缩小读取、搜索范围；/clear 只能清空旧历史。"
    )
