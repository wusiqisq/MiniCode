"""模型工具协议的离线验证，文件仅来自临时项目。"""

from copy import deepcopy
import json

import httpx
import pytest

from minicode.config import Config
from minicode.llm import ModelError, ask_model, request_model
from minicode.models import ModelReply, ToolCall
from minicode.tools import registry
from minicode.tools.registry import dispatch_tool_calls, tool_definitions


def tool_call(call_id="call_1", name="read_file", arguments='{"relative_path":"file.txt"}'):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def completion(calls, content=None, finish_reason="tool_calls"):
    return {"choices": [{
        "message": {"role": "assistant", "content": content, "tool_calls": calls},
        "finish_reason": finish_reason,
    }]}


def request_reply(body, *, use_tools=True):
    return request_model(
        [{"role": "user", "content": "读取文件"}], Config(api_key="test-key"),
        use_tools=use_tools,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    )


def test_tools_schema_and_text_response():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion(None, " 普通回答 ", "stop"))

    reply = request_model(
        [{"role": "user", "content": "你好"}], Config(api_key="test-key"),
        use_tools=True, transport=httpx.MockTransport(handler),
    )
    assert reply == ModelReply("普通回答")
    assert reply.as_message() == {"role": "assistant", "content": "普通回答"}
    schema = requests[0]["tools"][0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "read_file"
    assert schema["function"]["parameters"] == {
        "type": "object",
        "properties": {"relative_path": {"type": "string", "description": "相对于项目根目录的文件路径"}},
        "required": ["relative_path"], "additionalProperties": False,
    }
    assert requests[0]["tool_choice"] == "auto"
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert requests[0]["stream"] is False


def test_tool_definitions_are_fresh_and_registered():
    definitions = tool_definitions()
    assert {item["function"]["name"] for item in definitions} == set(registry._HANDLERS)
    definitions[0]["function"]["parameters"]["required"].clear()
    assert tool_definitions()[0]["function"]["parameters"]["required"] == ["relative_path"]


@pytest.mark.parametrize("content", [None, "", "我先查看文件。"])
def test_parse_and_rebuild_tool_message_without_executing(tmp_path, monkeypatch, content):
    def forbidden(*args):
        pytest.fail("解析模型回复不应执行工具")

    monkeypatch.setattr(registry, "read_file", forbidden)
    calls = [tool_call(), tool_call("call_2")]
    reply = request_reply(completion(calls, content))
    assert reply.content == content
    assert reply.tool_calls == (
        ToolCall("call_1", "read_file", '{"relative_path":"file.txt"}'),
        ToolCall("call_2", "read_file", '{"relative_path":"file.txt"}'),
    )
    assert reply.as_message() == {"role": "assistant", "content": content, "tool_calls": calls}
    reply.as_message()["tool_calls"][0]["function"]["name"] = "changed"
    assert reply.tool_calls[0].name == "read_file"


INVALID_CALLS = [
    None, [], {}, "bad", [None], [{}],
    [dict(tool_call(), id=None)], [dict(tool_call(), id="")],
    [dict(tool_call(), id=" ")], [dict(tool_call(), id=[])],
    [dict(tool_call(), type="other")], [dict(tool_call(), function=None)],
    [dict(tool_call(), function=[])], [dict(tool_call(), function={})],
    [tool_call(name="")], [tool_call(name=None)], [tool_call(name=[])],
    [tool_call(arguments={})], [tool_call(arguments=None)],
    [tool_call(), tool_call()], [tool_call(), {}],
]


@pytest.mark.parametrize("calls", INVALID_CALLS)
def test_malformed_tool_responses_rejected_before_dispatch(monkeypatch, calls):
    def forbidden(*args):
        pytest.fail("协议错误不应执行工具")

    monkeypatch.setattr(registry, "read_file", forbidden)
    with pytest.raises(ModelError):
        request_reply(completion(calls))


@pytest.mark.parametrize("field", ["id", "type", "function"])
def test_missing_call_fields_rejected(field):
    call = tool_call()
    del call[field]
    with pytest.raises(ModelError):
        request_reply(completion([call]))


@pytest.mark.parametrize("field", ["name", "arguments"])
def test_missing_function_fields_rejected(field):
    call = tool_call()
    del call["function"][field]
    with pytest.raises(ModelError):
        request_reply(completion([call]))


@pytest.mark.parametrize("content", [123, [], {}])
def test_invalid_tool_content_rejected(content):
    with pytest.raises(ModelError):
        request_reply(completion([tool_call()], content))


@pytest.mark.parametrize("reason", ["stop", "length", "aborted", "content_filter", None])
def test_inconsistent_or_incomplete_tool_response_rejected(reason):
    with pytest.raises(ModelError):
        request_reply(completion([tool_call()], "不能当作完整回答", reason))


@pytest.mark.parametrize("body", [
    {"choices": {}}, {"choices": "bad"},
    {"choices": [{"message": [], "finish_reason": "stop"}]},
    {"choices": [{"message": {"role": "tool", "content": "bad"}, "finish_reason": "stop"}]},
    completion({}, "text", "stop"),
])
def test_malformed_envelope_rejected(body):
    with pytest.raises(ModelError):
        request_reply(body)


def test_text_interface_rejects_unsolicited_tool_request():
    with pytest.raises(ModelError, match="未开放工具"):
        ask_model(
            [{"role": "user", "content": "你好"}], Config(api_key="test-key"),
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=completion([tool_call()]))),
        )


def test_ordered_dispatch_and_next_request_round_trip(tmp_path, monkeypatch):
    # 由测试明确发起第二次请求，不引入自动 Agent Loop。
    (tmp_path / "file.txt").write_bytes("中文\r\n".encode())
    (tmp_path / "empty.txt").write_text("")
    calls = [
        tool_call("one"), tool_call("two", arguments='{"relative_path":"missing"}'),
        tool_call("three", arguments='{"relative_path":"empty.txt"}'),
    ]
    messages = [{"role": "user", "content": "读取文件"}]
    original = deepcopy(messages)
    requests = []
    reads = []
    actual_read = registry.read_file

    def read(root, path):
        reads.append(path)
        return actual_read(root, path)

    monkeypatch.setattr(registry, "read_file", read)

    def handler(request):
        requests.append(json.loads(request.content))
        body = completion(calls) if len(requests) == 1 else completion(None, "完成", "stop")
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    reply = request_model(messages, Config(api_key="test-key"), use_tools=True, transport=transport)
    results = dispatch_tool_calls(tmp_path, reply.tool_calls)
    assert reads == ["file.txt", "missing", "empty.txt"]
    assert [result.tool_call_id for result in results] == ["one", "two", "three"]
    assert [result.status for result in results] == ["success", "error", "success"]
    tool_messages = [result.as_message() for result in results]
    assert [message["role"] for message in tool_messages] == ["tool"] * 3
    assert [message["tool_call_id"] for message in tool_messages] == ["one", "two", "three"]
    assert json.loads(tool_messages[0]["content"]) == {"status": "success", "content": "中文\r\n"}
    assert json.loads(tool_messages[1]["content"])["status"] == "error"
    assert json.loads(tool_messages[2]["content"]) == {"status": "success", "content": ""}
    followup = messages + [reply.as_message()] + tool_messages
    final = request_model(followup, Config(api_key="test-key"), use_tools=True, transport=transport)
    assert final == ModelReply("完成")
    assert requests[1]["messages"] == followup
    assert messages == original
    assert len(requests) == 2


@pytest.mark.parametrize("name,arguments", [
    ("delete_file", '{}'), ("read_file", '{bad-json'),
    ("read_file", '{"relative_path":"file.txt","project_root":"/"}'),
    ("read_file", '{"relative_path":".env"}'),
])
def test_invalid_tool_semantics_become_result_messages(tmp_path, name, arguments):
    # 协议结构合法，但工具或参数无效，应返回工具错误供后续模型理解。
    reply = request_reply(completion([tool_call(name=name, arguments=arguments)]))
    result, = dispatch_tool_calls(tmp_path, reply.tool_calls)
    assert result.status == "error"
    message = result.as_message()
    assert message["tool_call_id"] == "call_1"
    assert json.loads(message["content"])["status"] == "error"
