"""终端输入、多轮模型问答和退出处理。"""

import argparse
from importlib.metadata import PackageNotFoundError, version
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


def entrypoint(argv: list[str] | None = None) -> int:
    """解析命令行并启动会话；返回正常退出码 0，参数无效由 argparse 以退出码 2 结束。

    输入：argv 为不含程序名的参数列表，缺省读取终端参数。两个路径选项均相对于启动目录，
        支持 ~；项目必须是已有目录，显式配置必须是已有普通文件。帮助和版本不读配置。
    副作用：向 main 传递解析后的绝对路径；不改变当前目录，不打印配置内容。
    """
    parser = argparse.ArgumentParser(
        prog="minicode",
        description="MiniCode：在指定项目中使用终端编程助手。",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help="显示启动参数帮助并退出")
    try:
        installed_version = version("minicode")
    except PackageNotFoundError:
        installed_version = "未安装（请先安装项目）"
    parser.add_argument("--version", action="version", version=f"MiniCode {installed_version}", help="显示版本并退出")
    parser.add_argument("--project", metavar="目录", help="操作的项目目录，默认为启动目录")
    parser.add_argument("--env-file", metavar="文件", help="模型配置文件，默认读取目标项目的 .env；环境变量优先")
    args = parser.parse_args(argv)
    if args.project == "":
        parser.error("--project 不能为空。")
    try:
        project_root = Path(args.project or Path.cwd()).expanduser().resolve(strict=True)
        if not project_root.is_dir():
            parser.error("--project 必须是已有目录。")
    except (OSError, RuntimeError, ValueError):
        parser.error("--project 路径无效、缺失或不可访问。")
    env_file = None
    if args.env_file is not None:
        try:
            env_file = Path(args.env_file).expanduser().resolve(strict=True)
            if not env_file.is_file():
                parser.error("--env-file 必须是已有普通文件。")
        except (OSError, RuntimeError, ValueError):
            parser.error("--env-file 路径无效、缺失或不可访问。")
    main(project_root=project_root, env_file=env_file)
    return 0


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


def _progress(message: str) -> None:
    """输入程序进度文字，转义控制字符后展示到终端；无返回值，不修改会话或确认权限。"""
    print(f"MiniCode > {_display_text(message)}", flush=True)


def _show_help() -> None:
    """显示会话命令、资源限制和安全说明；无返回值，不读取配置、不进入模型历史。"""
    print("/help：显示本帮助；/clear：清空对话历史；/exit：退出；Ctrl+C：中断并退出。")
    print("/repair：指定允许编辑的源码与固定测试命令，最多修复 3 轮，每次编辑/检查分别确认。")
    print('普通输入：向模型提问，例如“只读取 calculator.py，解释它的用途”。')
    print('修复输入示例：["calculator.py"]；["python", "-B", "check_calculator.py"]。')
    print("每轮模型最多 8 次、工具最多 16 次、连续工具失败最多 3 次；达到限制表示未完成。")
    print("每次消息和工具说明最多 128 KiB，可能按整轮省略旧历史；不计算 token。")
    print("问题、历史、文件内容、编辑参数、命令和输出可能发送到模型服务；不要输入密钥。")
    print("仅用于可信项目；目录和 shell=False 不是沙箱，批准的命令可访问外部资源。")
    print("拒绝不执行后续操作；/clear、停止和退出不回滚文件或命令影响，退出后不恢复历史。")
    print("启动参数：minicode --help；运行目标项目测试时，应指定装有该项目依赖的 Python。")


def main(
    *,
    project_root: Path | None = None,
    env_file: Path | None = None,
) -> None:
    """运行终端交互，协调配置、会话和 Agent Loop。

    输入：project_root/env_file 为入口解析后的路径，缺省仍使用启动目录及其 .env；
        从标准输入读取问题。不改变进程当前目录，所有工具显式接收项目根目录。
    输出：无返回值；向终端展示数据提示、模型结果、限制和错误信息。
    副作用：加载配置并调用模型，编辑和命令须逐次确认；/repair 使用固定测试与有限轮数；
        /clear 只清历史、不撤销副作用。
    退出命令、EOF 或 Ctrl+C 结束程序；配置错误展示后允许继续输入。
    """
    config_options = {}
    if project_root is not None:
        config_options["project_root"] = project_root
    if env_file is not None:
        config_options["env_file"] = env_file
    print("欢迎使用 MiniCode。")
    print("输入 /help 查看帮助，/exit 退出，/clear 清空历史，/repair 启动有限修复。")
    project_root = project_root if project_root is not None else Path.cwd().resolve()
    print(f"项目根目录：{_display_text(str(project_root))}")
    config_path = env_file if env_file is not None else project_root / ".env"
    print(f"模型配置文件：{_display_text(str(config_path))}（已有环境变量优先）")
    print("当前问题、保留的对话与程序交接、工具请求和读取的文件内容（包括搜索片段）将发送到配置的模型服务。")
    print("编辑请求、替换文本和处理结果，命令参数、标准输出和标准错误将发送到模型服务；不要输入密钥。")
    print("仅对可信项目使用；默认敏感路径排除不能识别所有秘密。历史仅保存在本次运行内存中。")
    print("编辑先展示差异，命令先展示参数/目录/超时，均逐次确认，默认拒绝；工作目录和 shell=False 不是沙箱。")
    print("停止、超时和清空历史不会撤销其副作用。每次消息和工具说明最多 128 KiB；详见 /help。")
    session = Session()

    while True:
        try:
            user_input = input("你 > ").strip()
            if not user_input:
                continue
            if user_input == "/exit":
                print("再见！")
                return
            if user_input == "/help":
                _show_help()
                continue
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
                        load_config(**config_options),
                        project_root,
                        editable_paths=paths,
                        test_argv=argv,
                        edit_confirm=confirm_edit,
                        command_confirm=confirm_command,
                        on_progress=_progress,
                    )
                except ValueError as error:
                    print(f"MiniCode > 修复输入或配置无效：{_display_text(str(error))}")
                    continue
                print(f"MiniCode > {_display_text(repair_result.message)}")
                continue

            if user_input.startswith("/"):
                print("MiniCode > 未知会话命令，请输入 /help 查看用法。")
                continue

            config = load_config(**config_options)
            print("MiniCode > 正在等待模型回答……")
            result = run_agent(
                user_input,
                session,
                config,
                project_root,
                edit_confirm=confirm_edit,
                command_confirm=confirm_command,
                on_progress=_progress,
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
