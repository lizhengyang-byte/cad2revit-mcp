def test_full_cycle_produces_report(pipeline, config):
    p, task_id = pipeline
    detected = p.detect(task_id)
    first_column = next(e for e in detected["elements"] if e["type"] == "column")
    resolved = p.resolve(task_id, [{
        "element_id": first_column["id"],
        "action": "modify",
        "params": {"material": "C35"},
        "reason": "设计说明要求 C35",
    }])
    updated = next(e for e in resolved["elements"] if e["id"] == first_column["id"])
    assert updated["params"]["material"] == "C35"
    assert resolved["summary"]["applied"] == 1

    p.plan(task_id)
    p.execute(task_id)
    checked = p.check(task_id)
    assert checked["metrics"]["auto_ratio"] > 0.9

    report = p.report(task_id)
    text = report["report"]
    for section in ("图纸体检结论", "构件统计与自动完成比例", "疑点与裁决记录",
                    "执行失败明细", "待人工复核清单"):
        assert section in text
