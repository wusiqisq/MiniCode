"""在内存保存成功轮次，以及有写入影响或拒绝的完整停止交接。"""

from copy import deepcopy


class Session:
    """按轮次保存完整消息；由 Agent 决定成功或停止交接是否需要保留。"""

    def __init__(self) -> None:
        """创建一个没有历史的会话。

        输入：无额外参数。
        输出：无返回值；在当前对象上初始化按轮次分组的空历史列表。
        历史仅保存在内存，不读取磁盘或恢复旧会话。
        """
        self._turns: list[list[dict]] = []

    def messages_for(
        self,
        prompt: str,
    ) -> list[dict]:
        """将保留的历史和当前问题合成供调用方使用的扁平消息列表。

        输入：prompt 是尚未完成的当前用户问题。
        输出：历史深拷贝加一条 user 消息；修改返回值不会改变会话。
        此方法只组装消息，不保存当前问题，也不执行上下文裁剪。
        """
        return [message for turn in self.history_turns() for message in turn] + [
            {"role": "user", "content": prompt}
        ]

    def history_turns(self) -> list[list[dict]]:
        """取出按完整轮次（成功或停止交接）分组的历史副本。

        输入：无额外参数；读取当前会话的历史。
        输出：list[list[dict]]，外层每项是一轮，内层是该轮的有序消息。
        使用深拷贝，调用方修改嵌套工具参数也不会影响原历史。
        """
        return deepcopy(self._turns)

    def record_messages(
        self,
        messages: list[dict],
        *,
        discard_oldest_turns: int = 0,
    ) -> None:
        """保存完整的成功或停止交接轮次，同时提交调用方允许的旧历史移除。

        输入：messages 是完整的成功轮次或带程序停止说明的交接轮次；discard_oldest_turns 是移除的旧轮次数。
        输出：无返回值；更新当前会话，保存消息深拷贝。
        异常：移除数不是有效整数或超过已有轮数时抛出 ValueError，原历史不变。
        调用方负责先补齐工具请求对应的结果；只读失败通常不保存，有副作用的停止须交接。
        """
        if type(discard_oldest_turns) is not int or not 0 <= discard_oldest_turns <= len(self._turns):
            raise ValueError("移除轮次数必须在已保存的历史范围内。")
        saved = deepcopy(messages)
        self._turns = self._turns[discard_oldest_turns:] + [saved]

    def record_turn(
        self,
        prompt: str,
        answer: str,
    ) -> None:
        """用最简单的“问题＋回答”保存一个成功轮次。

        输入：prompt 是用户问题，answer 是最终文本回答。
        输出：无返回值；组装 user/assistant 两条消息后交给 record_messages 保存。
        适用于没有工具消息的调用，不额外裁剪历史。
        """
        self.record_messages(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": answer},
            ]
        )

    def clear(self) -> None:
        """清空当前会话的全部本地历史。

        输入：无额外参数。
        输出：无返回值；当前对象的历史变为空列表，允许重复调用。
        不发送网络请求，不能撤回模型服务此前已收到的数据。
        """
        self._turns.clear()
