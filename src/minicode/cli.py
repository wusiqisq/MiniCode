"""终端输入、单次模型问答和退出处理。"""

from minicode.config import ConfigError, load_config
from minicode.llm import ModelError, ask_model


def main() -> None:
    """接收输入，直到用户退出、关闭输入或中断程序。"""
    print("欢迎使用 MiniCode。")
    print("输入 /exit 退出。")
    print("当前为单次问答，输入内容将发送到配置的模型服务，不保留对话历史。")

    while True:
        try:
            user_input = input("你 > ").strip()
            if not user_input:
                continue
            if user_input == "/exit":
                print("再见！")
                return

            config = load_config()
            print("MiniCode > 正在等待模型回答……")
            answer = ask_model(user_input, config)
            print(f"MiniCode > {answer}")
        except (ConfigError, ModelError) as error:
            print(f"MiniCode > {error}")
        except EOFError:
            print("\n再见！")
            return
        except KeyboardInterrupt:
            print("\n已中断，程序退出。")
            return
