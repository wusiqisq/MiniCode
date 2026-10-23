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


def test_successful_record_can_remove_old_turns_atomically():
    session = Session()
    session.record_turn("旧", "回答")
    session.record_turn("较新", "回答")
    new = [{"role": "user", "content": "当前"}, {"role": "assistant", "content": "完成"}]
    session.record_messages(new, discard_oldest_turns=1)
    new[0]["content"] = "外部修改"
    assert session.history_turns() == [
        [{"role": "user", "content": "较新"}, {"role": "assistant", "content": "回答"}],
        [{"role": "user", "content": "当前"}, {"role": "assistant", "content": "完成"}],
    ]
    copy = session.history_turns()
    copy[0].clear()
    assert len(session.history_turns()[0]) == 2


def test_invalid_discard_does_not_mutate_session():
    import pytest

    session = Session()
    session.record_turn("旧", "回答")
    before = session.history_turns()
    for count in (-1, True, 1.5, 2):
        with pytest.raises(ValueError):
            session.record_messages([], discard_oldest_turns=count)
        assert session.history_turns() == before
