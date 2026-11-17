def test_execute_is_idempotent(pipeline):
    p, task_id = pipeline
    planned = p.plan(task_id)
    first = p.execute(task_id, dry_run=False)
    assert first["mode"] == "local"
    assert first["stats"]["created"] == planned["stats"]["planned"]
    second = p.execute(task_id, dry_run=False)
    assert second["stats"]["created"] == 0
    assert second["stats"]["skipped"] == planned["stats"]["planned"]
    assert p.store.count_keys(task_id) == planned["stats"]["planned"]


def test_dry_run_creates_nothing(pipeline):
    p, task_id = pipeline
    p.plan(task_id)
    result = p.execute(task_id, dry_run=True)
    assert result["status"] == "ok"
    assert result["stats"]["created"] == 0
    assert result["mode"] == "dry_run"
    assert p.store.count_keys(task_id) == 0
