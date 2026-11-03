"""离线模型配合真实本地检查，验证有限修复的次数、权限、结论及异常交接。"""

import json
import os
import sys

import httpx
import pytest

from minicode.config import Config
from minicode.repair import RepairInterrupted, run_repair
from minicode.session import Session
from minicode.tools.command import CommandError
from test_agent_command import assert_closed, response
from test_repair_demo import edit_call, repair_project, tool_call


pytestmark = pytest.mark.skipif(os.name != "posix", reason="命令接口仅支持 POSIX")


def execute(
    root,
    bodies=(),
    *,
    decisions=None,
    session=None,
    test_argv=None,
    **options,
):
    """输入临时项目、预设响应和授权序列，运行真实修复，返回结果、会话、HTTP 请求及授权记录。

    options 可覆盖修复与资源上限；指定响应或授权耗尽即失败，防止隐式额外重试。
    模型仅用 MockTransport，命令和文件操作真实执行。
    """
    replies = iter(bodies)
    choices = iter(decisions) if decisions is not None else None
    requests, approvals = [], []
    session = session if session is not None else Session()

    def serve(request):
        """记录实际 HTTP 请求，并返回下一模拟响应或抛指定异常，不发出真实网络请求。"""
        requests.append(json.loads(request.content))
        body = next(replies)
        if isinstance(body, BaseException):
            raise body
        return httpx.Response(body) if isinstance(body, int) else httpx.Response(200, json=body)

    def approve(value):
        """记录命令或编辑快照并取本次批准；异常值模拟用户中断，批准不继承。"""
        approvals.append(value)
        answer = next(choices) if choices is not None else True
        if isinstance(answer, BaseException):
            raise answer
        return answer

    result = run_repair(
        "修复加法，保持检查脚本不变。",
        session,
        Config(api_key="test-key"),
        root,
        editable_paths=("calculator.py",),
        test_argv=test_argv if test_argv is not None else [sys.executable, "-B", "check_calculator.py"],
        edit_confirm=approve,
        command_confirm=approve,
        transport=httpx.MockTransport(serve),
        **options,
    )
    for request in requests:
        assert_closed(request["messages"])
        names = {tool["function"]["name"] for tool in request["tools"]}
        assert names == {"read_file", "search_project", "edit_file"}
    assert_closed(session.messages_for("下一问"))
    return result, session, requests, approvals


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_fixed_check_drives_repairs_until_passed(
    repair_project,
    attempts,
):
    """临时样例在第 1/2/3 轮修好；每轮都复查，最终真实退出码为零且检查脚本不变。"""
    checks_before = (repair_project / "check_calculator.py").read_bytes()
    bodies = []
    old = "return a - b"
    for index in range(attempts):
        new = "return a + b" if index == attempts - 1 else f"return a - b + {index + 1}"
        bodies.extend([
            response([edit_call(f"edit{index}", old_text=old, new_text=new)]),
            response(text="本轮修改完成。"),
        ])
        old = new
    result, session, requests, approvals = execute(repair_project, bodies)
    assert result.status == "passed" and result.repair_attempts == attempts
    assert [check.outcome for check in result.verifications] == ["failed"] * attempts + ["passed"]
    assert [check.returncode for check in result.verifications] == [1] * attempts + [0]
    assert result.model_calls == attempts * 2 and result.tool_calls == attempts
    assert len(approvals) == 1 + attempts * 2
    assert result.written_paths == ("calculator.py",) and not result.uncertain_paths
    assert len(session.history_turns()) == 1
    assert "FAILED (failures=2)" in json.dumps(requests[0]["messages"])
    assert "Ran 3 tests" in result.verifications[-1].stderr
    assert "指定检查通过" in result.message
    assert (repair_project / "check_calculator.py").read_bytes() == checks_before
    assert not (repair_project / "__pycache__").exists()


def test_baseline_passes_without_model_or_edits(repair_project):
    """临时源码预先修好后直接报告基线通过；不请求模型、不消耗修复轮次。"""
    path = repair_project / "calculator.py"
    path.write_text(path.read_text().replace("return a - b", "return a + b"))
    result, _, requests, approvals = execute(repair_project)
    assert result.status == "passed" and result.repair_attempts == 0
    assert not requests and not result.written_paths and len(approvals) == 1


def test_model_claim_cannot_override_real_failure(repair_project):
    """模拟模型三次谎称通过但不修改；真实非零检查使三轮耗尽，不能继续第四轮或报告成功。"""
    bodies = [response(text="所有测试均已通过，不需要修改。") for _ in range(3)]
    result, session, requests, approvals = execute(repair_project, bodies)
    assert result.status == "stopped" and result.repair_attempts == 3
    assert len(requests) == 3 and len(approvals) == 4
    assert all(check.outcome == "failed" for check in result.verifications)
    assert "达到修复轮数上限" in result.message
    assert session.history_turns()[0][-1]["content"].startswith("MiniCode 程序记录：任务未完成")
    assert "return a - b" in (repair_project / "calculator.py").read_text()


@pytest.mark.parametrize("decisions,expected_calls,changed", [
    ([False], 0, False),
    ([True, False], 1, False),
    ([True, True, False], 2, True),
])
def test_denial_stops_entire_repair(
    repair_project,
    decisions,
    expected_calls,
    changed,
):
    """分别拒绝基线、编辑与复查；不绕过授权、不续问，拒绝前的真实写入保留。"""
    bodies = [response([edit_call("fix")]), response(text="已修改。")]
    result, session, requests, approvals = execute(repair_project, bodies, decisions=decisions)
    assert result.status == "stopped" and len(requests) == expected_calls
    assert len(approvals) == len(decisions)
    assert ("return a + b" in (repair_project / "calculator.py").read_text()) == changed
    assert result.written_paths == (("calculator.py",) if changed else ())
    assert len(session.history_turns()) == 1
    if changed:
        assert result.verifications[-1].outcome == "denied"


def test_model_cannot_replace_command_or_edit_check_script(repair_project):
    """即便模型请求未提供的命令或修改测试，执行层拒绝；仅固定检查及允许源码进入确认。"""
    checks_before = (repair_project / "check_calculator.py").read_bytes()
    bodies = [response([
        tool_call("cmd", "run_command", argv=[sys.executable, "-c", "open('marker','w').write('bad')"]),
        tool_call("test", "edit_file", relative_path="check_calculator.py", old_text="self.assertEqual(add(2, 3), 5)", new_text="pass"),
        edit_call("fix"),
    ]), response(text="修改完毕。")]
    result, _, requests, approvals = execute(repair_project, bodies)
    assert result.status == "passed" and len(approvals) == 3
    assert not (repair_project / "marker").exists()
    assert (repair_project / "check_calculator.py").read_bytes() == checks_before
    tools = [json.loads(m["content"]) for m in requests[1]["messages"] if m["role"] == "tool"]
    assert [tool["status"] for tool in tools] == ["error", "error", "success"]


@pytest.mark.parametrize("failure", [503, httpx.ReadTimeout("secret"), {"choices": []}])
@pytest.mark.parametrize("after_edit", [False, True])
def test_model_failure_stops_and_preserves_all_handoff(
    repair_project,
    failure,
    after_edit,
):
    """编辑前后发生 HTTP/网络/协议错误时不再复查或重试；保留旧历史、基线失败和实际修改。"""
    session = Session()
    session.record_turn("旧问题" * 10000, "旧回答")
    old = session.history_turns()[0]
    bodies = [response([edit_call("fix")]), failure] if after_edit else [failure]
    result, session, requests, _ = execute(
        repair_project,
        bodies,
        session=session,
        max_context_bytes=12000,
    )
    assert result.status == "stopped" and len(result.verifications) == 1
    assert session.history_turns()[0] == old and len(session.history_turns()) == 2
    assert result.written_paths == (("calculator.py",) if after_edit else ())
    assert result.model_calls == len(bodies) and len(requests) == len(bodies)
    assert "尚未复查" in result.message and "secret" not in result.message
    saved = str(session.history_turns()[-1])
    assert "FAILED (failures=2)" in saved and "任务未完成" in saved


@pytest.mark.parametrize("limit", ["models", "tools", "context"])
def test_agent_limits_stop_repair_without_extra_check(
    repair_project,
    limit,
):
    """模型轮内部达到调用或上下文限制时立即停止整体修复，不能换新一轮绕过限制。"""
    options = {}
    if limit == "models":
        options["max_model_calls"] = 1
    elif limit == "tools":
        options["max_tool_calls"] = 1
    else:
        options["max_context_bytes"] = 1
    bodies = [response([edit_call("one"), edit_call("two")])]
    result, _, requests, approvals = execute(repair_project, bodies, **options)
    assert result.status == "stopped" and result.repair_attempts == 1
    assert len(result.verifications) == 1 and len(approvals) == 1
    assert not result.written_paths
    assert len(requests) == (0 if limit == "context" else 1)


@pytest.mark.parametrize("mode", ["timeout", "start_error", "collection_error", "truncated"])
def test_verification_boundaries(
    repair_project,
    monkeypatch,
    mode,
):
    """真实超时、启动失败、模拟启动后收集错误和真实输出截断均生成准确结论，不自动修复异常状态。"""
    argv = [sys.executable, "-c", "import time; time.sleep(3)"]
    if mode == "start_error":
        argv = ["minicode-nonexistent-test-command"]
    elif mode == "collection_error":
        def fail_collection(
            *args,
            **kwargs,
        ):
            """接收固定检查调用，模拟已启动后的收集异常，验证 unknown 交接。"""
            raise CommandError("收集失败", started=True)

        monkeypatch.setattr("minicode.repair.run_command", fail_collection)
    elif mode == "truncated":
        argv = [sys.executable, "-c", "print('x'*50000)"]
    result, _, requests, _ = execute(repair_project, test_argv=argv, timeout_seconds=1)
    assert not requests and result.repair_attempts == 0
    expected = {"timeout": "timed_out", "start_error": "error", "collection_error": "unknown", "truncated": "passed"}
    assert result.verifications[-1].outcome == expected[mode]
    assert result.status == ("passed" if mode == "truncated" else "stopped")
    if mode == "truncated":
        assert result.verifications[-1].output_truncated and "输出已截断" in result.message


@pytest.mark.parametrize("decisions,bodies,expected,written", [
    ([KeyboardInterrupt()], [], "interrupted", False),
    ([True, KeyboardInterrupt()], [response([edit_call("fix")])], "failed", False),
    ([True, True, KeyboardInterrupt()], [response([edit_call("fix")]), response()], "interrupted", True),
    ([True, True], [response([edit_call("fix")]), KeyboardInterrupt()], "failed", True),
])
def test_interrupt_saves_repair_result(
    repair_project,
    decisions,
    bodies,
    expected,
    written,
):
    """基线确认、编辑确认、复查确认与模型续问中断均携带完整交接，不把未执行检查报告为通过。"""
    session = Session()
    with pytest.raises(RepairInterrupted) as error:
        execute(repair_project, bodies, decisions=decisions, session=session)
    result = error.value.result
    assert result.status == "stopped" and result.verifications[-1].outcome == expected
    assert result.written_paths == (("calculator.py",) if written else ())
    assert result.model_calls == len(bodies)
    assert len(session.history_turns()) == 1
    assert_closed(session.messages_for("下一问"))


def test_command_running_interrupt_records_unknown(
    repair_project,
    monkeypatch,
):
    """实际启动检查后在收集阶段中断，保存 unknown；底层清理返回后不允许模型继续。"""
    def interrupt(
        self,
        timeout=None,
    ):
        """输入选择器等待参数，在真实命令运行后模拟中断，无正常返回值。"""
        raise KeyboardInterrupt

    monkeypatch.setattr("minicode.tools.command.selectors.DefaultSelector.select", interrupt)
    with pytest.raises(RepairInterrupted) as error:
        execute(repair_project, test_argv=[sys.executable, "-c", "import time; time.sleep(3)"])
    assert error.value.result.verifications[-1].outcome == "unknown"


def test_approved_edit_interrupt_keeps_uncertain_path(
    repair_project,
    monkeypatch,
):
    """批准编辑后在写入窗口中断，控制器保留 uncertain 路径，不声称已写入或重新运行检查。"""
    def interrupt_apply(
        project_root,
        preview,
        *,
        confirm,
    ):
        """输入合法预览并实际请求一次批准，再模拟写入窗口中断，无正常返回值。"""
        assert confirm(preview) is True
        raise KeyboardInterrupt

    monkeypatch.setattr("minicode.tools.registry.apply_edit", interrupt_apply)
    with pytest.raises(RepairInterrupted) as error:
        execute(repair_project, [response([edit_call("fix")])])
    result = error.value.result
    assert result.uncertain_paths == ("calculator.py",) and not result.written_paths
    assert len(result.verifications) == 1 and "写入结果未确认" in result.message


def test_missing_callbacks_default_to_denial(repair_project):
    """调用方未提供确认接口时，基线默认拒绝，不执行模型或文件修改。"""
    result = run_repair(
        "修复",
        Session(),
        Config(api_key="test-key"),
        repair_project,
        editable_paths=("calculator.py",),
        test_argv=[sys.executable, "-B", "check_calculator.py"],
    )
    assert result.status == "stopped" and result.verifications[0].outcome == "denied"
    assert result.model_calls == result.repair_attempts == 0


def test_interrupt_during_check_archive_does_not_duplicate_execution(
    repair_project,
    monkeypatch,
):
    """实际检查完成后归档工作历史时中断，报告保留唯一已知结果，不重复统计命令执行。"""
    original = Session.record_messages
    interrupted = False

    def archive_then_interrupt(
        self,
        messages,
        **options,
    ):
        """透传保存，首次单独归档验证结果后模拟中断；其余保存正常返回 None。"""
        nonlocal interrupted
        original(self, messages, **options)
        if not interrupted and len(messages) == 1 and "固定验证结果" in messages[0].get("content", ""):
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(Session, "record_messages", archive_then_interrupt)
    with pytest.raises(RepairInterrupted) as error:
        execute(repair_project)
    assert len(error.value.result.verifications) == 1
    assert error.value.result.verifications[0].outcome == "failed"


def test_command_removes_project_root_stops_before_model(repair_project):
    """检查命令真实移动自身项目目录后非零退出，程序保留检查记录并停止，不调用模型或抛初始化堆栈。"""
    argv = [sys.executable, "-c", "import os; os.rename(os.getcwd(), os.getcwd() + '-moved'); raise SystemExit(1)"]
    result, session, requests, _ = execute(repair_project, test_argv=argv)
    assert result.status == "stopped" and not requests
    assert result.repair_attempts == 0 and "目录已不存在" in result.message
    assert result.verifications[0].returncode == 1 and session.history_turns()


def test_mutating_original_argv_does_not_change_fixed_command(repair_project):
    """确认时修改调用方参数列表不能更换基线或复查命令；真实两次检查仍使用冻结快照。"""
    argv = [sys.executable, "-B", "check_calculator.py"]
    choices = []

    def approve(request):
        """记录实际快照，篡改外部列表后批准，验证不会执行改后的命令。"""
        choices.append(request.argv)
        argv[:] = ["minicode-not-a-real-command"]
        return True

    result = run_repair(
        "检查",
        Session(),
        Config(api_key="test-key"),
        repair_project,
        editable_paths=("calculator.py",),
        test_argv=argv,
        command_confirm=approve,
        max_repair_attempts=1,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response())),
    )
    assert result.status == "stopped" and len(choices) == 2
    assert choices[0] == choices[1] == (sys.executable, "-B", "check_calculator.py")


@pytest.mark.parametrize("options", [
    {"max_repair_attempts": 0}, {"max_repair_attempts": True}, {"max_model_calls": -1},
    {"max_tool_calls": False}, {"max_context_bytes": 1.5}, {"timeout_seconds": 0},
    {"relative_cwd": ".."},
])
def test_invalid_options_have_no_side_effects(
    repair_project,
    options,
):
    """非法次数、目录或超时在首条命令前失败，不修改传入会话。"""
    session = Session()
    with pytest.raises(ValueError):
        execute(repair_project, session=session, **options)
    assert not session.history_turns()


@pytest.mark.parametrize("paths", [(), ("../calculator.py",), ("check-missing.py",), (".env",), ["calculator.py"]])
def test_invalid_editable_paths_rejected_before_check(
    repair_project,
    paths,
):
    """空名单、越界、缺失、敏感及错误容器类型在检查前拒绝，确认回调不得调用。"""
    def forbidden(request):
        """若非法输入进入命令确认，直接使测试失败，无正常返回值。"""
        pytest.fail("无效编辑范围不能执行检查")

    with pytest.raises(ValueError):
        run_repair(
            "修复",
            Session(),
            Config(api_key="test-key"),
            repair_project,
            editable_paths=paths,
            test_argv=[sys.executable, "-B", "check_calculator.py"],
            command_confirm=forbidden,
        )
