"""终端输入、多轮模型问答和退出处理。"""

from minicode.config import ConfigError, load_config
from minicode.llm import ModelError, ask_model
from minicode.session import Session


def main() -> None:
    """接收输入，直到用户退出、关闭输入或中断程序。"""
    print("欢迎使用 MiniCode。")
    print("输入 /exit 退出，/clear 清空对话历史。")
    print("当前问题和此前成功的问答将发送到配置的模型服务；历史仅保存在本次运行内存中。")
    session = Session()

    while True:
        try:
            user_input = input("你 > ").strip()
            if not user_input:
                continue
            if user_input == "/exit":
                print("再见！")
                return
            if user_input == "/clear":
                session.clear()
                print("MiniCode > 已清空对话历史。")
                continue

            config = load_config()
            print("MiniCode > 正在等待模型回答……")
            messages = session.messages_for(user_input)
            answer = ask_model(messages, config)
            session.record_turn(user_input, answer)
            print(f"MiniCode > {answer}")
        except (ConfigError, ModelError) as error:
            print(f"MiniCode > {error}")
        except EOFError:
            print("\n再见！")
            return
        except KeyboardInterrupt:
            print("\n已中断，程序退出。")
            return
