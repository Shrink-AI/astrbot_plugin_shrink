from shrink.services.execution_status import timeout_reply


def test_no_invented_failure_cause():
    text = timeout_reply([], 100)
    assert "100" in text and "超时" in text
    assert "网页" not in text and "网络" not in text


def test_submitted_is_not_completed():
    text = timeout_reply([{"name": "any_plugin", "execution": {
        "version": 1, "status": "submitted", "task_id": "abc"}}], 100)
    assert "submitted" in text and "abc" in text and "单步完成" in text


def test_saved_file_keeps_confirmed_result():
    text = timeout_reply([{"name": "arbitrary_save", "execution": {
        "version": 1, "status": "completed", "receipt_id": "receipt-42"}}], 100)
    assert "receipt-42" in text and "completed" in text


def test_legacy_prose_does_not_prove_execution():
    text = timeout_reply([{"name": "bothouse_save_file_to_desktop",
                          "result": "已保存到你的电脑桌面：demo.md（6 字节）"}], 100)
    assert 'demo.md' not in text and '还没确认' in text


def test_tool_evidence_survives_record_truncation():
    import json
    from shrink.services.skills import tool_step
    step = tool_step('unknown_plugin', {}, json.dumps({
        'result': 'x' * 5000, 'execution': {'version': 1, 'status': 'submitted', 'task_id': 'job-2'}}))
    assert step['execution']['task_id'] == 'job-2'
    assert 'job-2' in timeout_reply([step], 100)


def test_unknown_contract_does_not_expose_arbitrary_results():
    from shrink.services.tool_evidence import execution_evidence
    assert execution_evidence({'execution': {'version': 2, 'status': 'completed'}}) is None
    assert execution_evidence({'execution': {'version': 1, 'status': 'invented'}}) is None
    assert execution_evidence('not-json') is None


def test_arbitrary_tool_output_is_not_repeated():
    text = timeout_reply([{"name": "astrbot_execute_python", "result": "secret=private"}], 100)
    assert "private" not in text and "还没确认" in text


