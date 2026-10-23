"""终端输入、多轮模型问答和退出处理。"""

from pathlib import Path

from minicode.agent import run_agent
from minicode.config import ConfigError, load_config
from minicode.session import Session


def main() -> None:
    """运行终端交互，协调配置、会话和 Agent Loop。

    输入：无函数参数；从标准输入读取问题，使用启动目录作为项目根目录。
    输出：无返回值；向终端展示数据提示、模型结果、限制和错误信息。
    副作用：普通问题会加载配置并调用模型，/clear 清空本地历史。
    退出命令、EOF 或 Ctrl+C 结束程序；配置错误展示后允许继续输入。
    """
    print("欢迎使用 MiniCode。")
    print("输入 /exit 退出，/clear 清空对话历史。")
    project_root = Path.cwd().resolve()
    print(f"项目根目录：{project_root}")
    print("当前问题、此前成功的问答、工具请求和读取的文件内容（包括搜索片段）将发送到配置的模型服务。")
    print("仅对可信项目使用；默认敏感路径排除不能识别所有秘密。历史仅保存在本次运行内存中。")
    print("每轮限制：模型 8 次、工具 16 次、连续工具失败 3 次；达到限制表示任务未完成。")
    print("上下文限制：每次消息和工具说明最多 128 KiB；超限按整轮裁剪旧历史，本轮仍超限则停止。")
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
            result = run_agent(
                user_input,
                session,
                config,
                project_root,
            )
            if result.omitted_history_turns:
                action = "成功后已移除这些本地旧历史。" if result.status == "completed" else "未完成，本地旧历史仍保留。"
                print(f"MiniCode > 上下文限制：请求省略了 {result.omitted_history_turns} 轮较早对话；{action}")
            print(f"MiniCode > {result.message}")
        except ConfigError as error:
            print(f"MiniCode > {error}")
        except EOFError:
            print("\n再见！")
            return
        except KeyboardInterrupt:
            print("\n已中断，程序退出。")
            return
