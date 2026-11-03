"""终端输入、多轮模型问答和退出处理。"""

import json
from pathlib import Path

# 加载后，input() 才能在交互终端中编辑当前行、回调本进程的输入历史。
# 不读取或写入历史文件；缺少此可选标准库模块的平台仍能使用基本输入。
try:
    import readline  # noqa: F401
except ImportError:
    pass

from minicode.agent import AgentInterrupted, run_agent
from minicode.config import ConfigError, load_config
from minicode.session import Session
from minicode.permissions import confirm_command, confirm_edit, _display_text
from minicode.repair import RepairInterrupted, run_repair


def _repair_input() -> tuple[str, tuple[str, ...], list[str]]:
    """读取修复目标及两组 JSON 参数，返回目标、允许文件和命令；格式错误抛 ValueError，不执行操作。"""
    print('有限修复最多 3 轮；每次测试超时 30 秒，工作目录为项目根目录。')
    print('只把源码加入允许编辑名单，不包含测试脚本；例如 ["calculator.py"]。')
    goal = input("修复目标 > ").strip()
    try:
        paths = json.loads(input("允许编辑的文件（JSON 字符串列表） > "))
        argv = json.loads(input("测试命令（JSON 参数列表） > "))
    except (ValueError, RecursionError):
        raise ValueError("文件和命令必须是 JSON 字符串列表，例如 [\"calculator.py\"]。") from None
    if not goal or any(
        not isinstance(values, list) or not values
        or any(not isinstance(item, str) or not item.strip() for item in values)
        for values in (paths, argv)
    ):
        raise ValueError("目标不能为空，文件和命令必须是非空 JSON 字符串列表。")
    return goal, tuple(paths), argv


def main() -> None:
    """运行终端交互，协调配置、会话和 Agent Loop。

    输入：无函数参数；从标准输入读取问题，使用启动目录作为项目根目录。
    输出：无返回值；向终端展示数据提示、模型结果、限制和错误信息。
    副作用：加载配置并调用模型，编辑和命令须逐次确认；/repair 使用固定测试与有限轮数；
        /clear 只清历史、不撤销副作用。
    退出命令、EOF 或 Ctrl+C 结束程序；配置错误展示后允许继续输入。
    """
    print("欢迎使用 MiniCode。")
    print("输入 /exit 退出，/clear 清空对话历史，/repair 启动固定测试与有限修复。")
    project_root = Path.cwd().resolve()
    print(f"项目根目录：{project_root}")
    print("当前问题、保留的对话与程序交接、工具请求和读取的文件内容（包括搜索片段）将发送到配置的模型服务。")
    print("编辑请求、替换文本和处理结果也会作为历史发送到配置的模型服务；停止不等于撤销已完成的写入。")
    print("仅对可信项目使用；默认敏感路径排除不能识别所有秘密。历史仅保存在本次运行内存中。")
    print("编辑会先展示差异并逐次确认，默认拒绝；已写入的修改不会因停止或清空历史自动撤销。")
    print("命令参数、标准输出和标准错误将发送到模型服务；不要在参数中放密钥。")
    print("命令先展示参数、工作目录和超时并逐次确认，默认拒绝；shell=False 和工作目录不是沙箱。")
    print("命令可能修改文件或访问网络，失败、超时、停止及清空历史都不会撤销其副作用。")
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
            if user_input == "/repair":
                try:
                    goal, paths, argv = _repair_input()
                    repair_result = run_repair(
                        goal,
                        session,
                        load_config(),
                        project_root,
                        editable_paths=paths,
                        test_argv=argv,
                        edit_confirm=confirm_edit,
                        command_confirm=confirm_command,
                    )
                except ValueError as error:
                    print(f"MiniCode > 修复输入或配置无效：{_display_text(str(error))}")
                    continue
                print(f"MiniCode > {_display_text(repair_result.message)}")
                continue

            config = load_config()
            print("MiniCode > 正在等待模型回答……")
            result = run_agent(
                user_input,
                session,
                config,
                project_root,
                edit_confirm=confirm_edit,
                command_confirm=confirm_command,
            )
            if result.omitted_history_turns:
                action = "成功后已移除这些本地旧历史。" if result.status == "completed" else "未完成，本地旧历史仍保留。"
                print(f"MiniCode > 上下文限制：请求省略了 {result.omitted_history_turns} 轮较早对话；{action}")
            if result.status == "completed" and result.written_paths:
                print(f"MiniCode > 已确认写入：{_display_text(json.dumps(result.written_paths, ensure_ascii=False))}")
            if result.status == "completed" and result.commands:
                records = json.dumps([record.as_dict() for record in result.commands], ensure_ascii=False)
                print(f"MiniCode > 命令交接：{_display_text(records)}；请根据退出码判断执行结果。")
            print(f"MiniCode > {result.message}")
        except ConfigError as error:
            print(f"MiniCode > {error}")
        except EOFError:
            print("\n再见！")
            return
        except AgentInterrupted as error:
            print(f"MiniCode > {error.result.message}")
            print("\n已中断，程序退出。")
            return
        except RepairInterrupted as error:
            print(f"MiniCode > {_display_text(error.result.message)}")
            print("\n已中断，程序退出。")
            return
        except KeyboardInterrupt:
            print("\n已中断，程序退出。")
            return
