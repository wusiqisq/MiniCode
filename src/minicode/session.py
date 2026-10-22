"""只在本次程序运行期间保存成功的问答。"""


class Session:
    """管理对话历史；失败的请求不写入历史。"""

    def __init__(self) -> None:
        self._messages: list[dict[str, str]] = []

    def messages_for(self, prompt: str) -> list[dict[str, str]]:
        """构造请求副本，当前问题尚未写入历史。"""
        return [message.copy() for message in self._messages] + [
            {"role": "user", "content": prompt}
        ]

    def record_turn(self, prompt: str, answer: str) -> None:
        """模型成功回答后，按顺序保存完整的一问一答。"""
        self._messages.extend([
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer},
        ])

    def clear(self) -> None:
        """清空本地历史，不向模型发送请求。"""
        self._messages.clear()
