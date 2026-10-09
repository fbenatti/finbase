from types import SimpleNamespace

from app.services.scheduler_service import _queue_reclassification_schedule


def test_scheduled_reclassification_keeps_forecast_pipeline_settings():
    tasks = []
    session = SimpleNamespace(add=tasks.append, flush=lambda: None)
    schedule = SimpleNamespace(id=4, connection_ids=[2], config_json={
        "destination_table_ids": [1], "billing_periods": ["202610"],
        "period_mode": "selected", "forecast_config_ids": [9],
        "accuracy": False, "shared_cost": True, "gold_rule_ids": [4],
    })
    _queue_reclassification_schedule(schedule, session)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_type == "reclassification"
    assert task.billing_periods == ["202610"]
    assert task.result_json["scheduled_pipeline"] is True
    assert task.result_json["forecast_config_ids"] == [9]
    assert task.result_json["gold_rule_ids"] == [4]
    assert task.result_json["shared_cost"] is True
