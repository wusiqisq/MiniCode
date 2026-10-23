"""终端输入、多轮模型问答和退出处理。"""

from pathlib import Path

from minicode.agent import run_agent
from minicode.config import ConfigError, load_config
from minicode.session import Session


def main() -> None:
    """接收输入，直到用户退出、关闭输入或中断程序。"""
    print("欢迎使用 MiniCode。")
    print("输入 /exit 退出，/clear 清空对话历史。")
    project_root = Path.cwd().resolve()
    print(f"项目根目录：{project_root}")
    print("当前问题、此前成功的问答、工具请求和读取的文件内容将发送到配置的模型服务。")
    print("仅对可信项目使用；默认敏感路径排除不能识别所有秘密。历史仅保存在本次运行内存中。")
    print("每轮限制：模型 8 次、工具 16 次、连续工具失败 3 次；达到限制表示任务未完成。")
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
            result = run_agent(user_input, session, config, project_root)
            print(f"MiniCode > {result.message}")
        except ConfigError as error:
            print(f"MiniCode > {error}")
        except EOFError:
            print("\n再见！")
            return
        except KeyboardInterrupt:
            print("\n已中断，程序退出。")
            return
