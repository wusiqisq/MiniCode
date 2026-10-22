from minicode.session import Session


def test_pending_question_does_not_change_history():
    session = Session()
    assert session.messages_for("未完成的问题") == [
        {"role": "user", "content": "未完成的问题"}
    ]
    assert session.messages_for("重试的问题") == [
        {"role": "user", "content": "重试的问题"}
    ]


def test_history_preserves_complete_turns_and_order():
    session = Session()
    session.record_turn("我在学列表", "列表是可变序列")
    session.record_turn("给我练习", "写一个列表求和函数")
    assert session.messages_for("给我提示") == [
        {"role": "user", "content": "我在学列表"},
        {"role": "assistant", "content": "列表是可变序列"},
        {"role": "user", "content": "给我练习"},
        {"role": "assistant", "content": "写一个列表求和函数"},
        {"role": "user", "content": "给我提示"},
    ]


def test_request_copy_cannot_modify_saved_messages():
    session = Session()
    session.record_turn("原问题", "原回答")
    request = session.messages_for("新问题")
    request[0]["content"] = "外部修改"
    request.clear()
    assert session.messages_for("另一个问题") == [
        {"role": "user", "content": "原问题"},
        {"role": "assistant", "content": "原回答"},
        {"role": "user", "content": "另一个问题"},
    ]


def test_clear_and_new_sessions_start_empty():
    session = Session()
    session.record_turn("旧问题", "旧回答")
    assert Session().messages_for("新问题") == [{"role": "user", "content": "新问题"}]
    session.clear()
    session.clear()
    assert session.messages_for("新问题") == [{"role": "user", "content": "新问题"}]
