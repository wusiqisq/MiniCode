"""最小终端交互；模型和工具将在后续阶段接入。"""


def main() -> None:
    """接收输入，直到用户退出、关闭输入或中断程序。"""
    print("欢迎使用 MiniCode。")
    print("输入 /exit 退出。")

    while True:
        try:
            user_input = input("你 > ").strip()
        except EOFError:
            print("\n再见！")
            return
        except KeyboardInterrupt:
            print("\n已中断，程序退出。")
            return

        if not user_input:
            continue
        if user_input == "/exit":
            print("再见！")
            return

        print("MiniCode > 已收到输入，模型功能尚未接入。")
