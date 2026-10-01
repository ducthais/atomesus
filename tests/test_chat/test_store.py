from src.chat import store


def test_exchange_is_saved_as_question_then_reply(tmp_path):
    path = str(tmp_path / "runs.sqlite3")

    store.save_exchange(path, "run-1", "minh", "ask", "Vì sao dừng?", "Dừng ở phase resolve")

    messages = store.list_messages(path, "run-1")
    assert [(m.role, m.author, m.content) for m in messages] == [
        ("user", "minh", "Vì sao dừng?"),
        ("assistant", store.AGENT_AUTHOR, "Dừng ở phase resolve"),
    ]
    assert messages[0].at.tzinfo is not None


def test_count_questions_counts_only_user_asks_of_that_run(tmp_path):
    path = str(tmp_path / "runs.sqlite3")
    store.save_exchange(path, "run-1", "minh", "new_request", "Tạo pipeline", "Đã tạo")
    store.save_exchange(path, "run-1", "an", "ask", "Bao nhiêu dòng?", "30")
    store.save_exchange(path, "run-2", "an", "ask", "?", "!")

    assert store.count_questions(path, "run-1") == 1
    assert store.list_messages(path, "missing") == []
