"""只在本次程序运行期间保存成功整轮的问答和工具消息。"""

from copy import deepcopy


class Session:
    """管理对话历史；失败的轮次不写入历史。"""

    def __init__(self) -> None:
        self._messages: list[dict] = []

    def messages_for(self, prompt: str) -> list[dict]:
        """构造含嵌套工具请求的深拷贝，当前问题尚未写入历史。"""
        return deepcopy(self._messages) + [{"role": "user", "content": prompt}]

    def record_messages(self, messages: list[dict]) -> None:
        """保存调用方已完成的整轮副本，包含工具请求和结果。"""
        self._messages.extend(deepcopy(messages))

    def record_turn(self, prompt: str, answer: str) -> None:
        """保留纯文本问答接口。"""
        self.record_messages([
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer},
        ])

    def clear(self) -> None:
        """清空本地历史，不向模型发送请求。"""
        self._messages.clear()
