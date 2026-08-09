import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from incidents import CodeRepairProvider, IncidentDispatcher, IncidentService, IncidentStore
from integrations import IntegrationManager


def service(tmp_path):
    return IncidentService(IncidentStore(tmp_path / "incidents.json"))


def emit(svc, **overrides):
    values = dict(source="gemini", component="live", category="provider_error", error_code="1011",
                  title="Live failure", safe_summary="Provider connection closed", severity="medio",
                  metadata={"exception_class": "ConnectionClosed"})
    values.update(overrides)
    return svc.collect(**values)


@pytest.mark.parametrize("level", ["grave", "medio", "leve"])
def test_creates_each_public_severity(tmp_path, level):
    assert emit(service(tmp_path), severity=level)["severity"] == level


def test_invalid_severity_rejected_and_fingerprint_is_deterministic(tmp_path):
    svc = service(tmp_path)
    with pytest.raises(ValueError): emit(svc, severity="urgent")
    meta = {"exception_class": "X"}
    assert svc.fingerprint("A", "B", "C", meta) == svc.fingerprint("A", "B", "C", meta)


def test_deduplication_counts_and_preserves_first_seen(tmp_path):
    svc = service(tmp_path); first = emit(svc); second = emit(svc)
    assert first["incident_id"] == second["incident_id"]
    assert second["occurrence_count"] == 2 and second["first_seen"] == first["first_seen"]
    assert second["last_seen"] >= first["last_seen"]


def test_resolution_reappearance_and_filters_ordering(tmp_path):
    svc = service(tmp_path)
    low = emit(svc, source="backend", error_code="L", severity="leve")
    high = emit(svc, source="database", error_code="H", severity="grave")
    svc.store.transition(low["incident_id"], "resolvido", "Recovered")
    assert svc.store.get(low["incident_id"])["resolved_at"]
    reopened = emit(svc, source="backend", error_code="L", severity="leve")
    assert reopened["status"] == "reaberto" and reopened["resolved_at"] is None
    assert svc.store.list(status="abertos")[0]["incident_id"] == high["incident_id"]
    assert len(svc.store.list(severity="leve", status="todos")) == 1


def test_sanitizes_secrets_private_content_and_paths(tmp_path):
    item = emit(service(tmp_path), safe_summary="Bearer token123 password=hunter2 api_key=AIza" + "A" * 30 + " C:\\Users\\private\\file",
                metadata={"password": "secret", "payload": "client finance", "exception_class": "Safe"})
    raw = json.dumps(item)
    assert "token123" not in raw and "hunter2" not in raw and "AIza" not in raw
    assert "private\\file" not in raw and "client finance" not in raw
    assert item["metadata"] == {"exception_class": "Safe"}


def test_normal_denial_and_isolated_retry_do_not_create_incidents(tmp_path):
    svc = service(tmp_path)
    assert emit(svc, event_type="confirmation_denied") is None
    assert emit(svc, event_type="success") is None
    assert emit(svc, event_type="retry", metadata={"retry_count": 1}) is None
    assert emit(svc, event_type="retry", metadata={"retry_count": 20})["severity"] == "medio"


def test_persistence_and_basic_concurrency(tmp_path):
    svc = service(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: emit(svc), range(30)))
    reopened = service(tmp_path).store.list(status="todos")
    assert len(reopened) == 1 and reopened[0]["occurrence_count"] == 30


def test_ui_and_veronica_manager_methods_share_store_and_missing_detail(tmp_path):
    manager = IntegrationManager(telemetry_path=tmp_path/"t.json", events_path=tmp_path/"e.json",
        preferences_path=tmp_path/"p.json", incidents_path=tmp_path/"i.json", env_path=tmp_path/".env", api_key="x")
    item = manager.incidents.collect(source="tools", component="gateway", category="gateway_rejection",
        error_code="invalid", title="Rejected", safe_summary="Invalid request", severity="medio")
    ui = manager.list_system_incidents(); ai = manager.get_incident_details(item["incident_id"])
    assert ui["incidents"][0] == ai["incident"] and ui["source_of_truth"] == ai["source_of_truth"]
    assert manager.get_incident_details("missing")["found"] is False


def test_old_null_data_is_tolerated(tmp_path):
    path = tmp_path/"i.json"; path.write_text('[{"incident_id":"old","severity":"leve","status":"novo","last_seen":null}]')
    assert IncidentStore(path).list(status="abertos")[0]["incident_id"] == "old"


def test_repair_contract_never_applies_without_provider_or_approval():
    provider = CodeRepairProvider()
    assert provider.apply({}, approved=False) == {"applied": False, "reason": "approval_required"}
    assert provider.apply({}, approved=True) == {"applied": False, "reason": "repair_provider_unavailable"}


def test_dispatcher_keeps_slow_persistence_off_caller_and_flushes(tmp_path, monkeypatch):
    svc = service(tmp_path); original = svc.store._write
    def slow(records):
        import time
        time.sleep(.08); original(records)
    monkeypatch.setattr(svc.store, "_write", slow)
    dispatcher = IncidentDispatcher(svc, max_queue=4)
    import time
    started = time.perf_counter()
    assert dispatcher.submit(source="synthetic", component="live", category="provider_error",
        error_code="SLOW", title="Synthetic", safe_summary="Synthetic", severity="medio")
    assert (time.perf_counter() - started) < .03
    dispatcher.shutdown(flush=True)
    assert svc.store.list(status="todos")[0]["error_code"] == "SLOW"


def test_dispatcher_store_failure_is_isolated_and_full_queue_never_blocks(tmp_path, monkeypatch):
    svc = service(tmp_path)
    monkeypatch.setattr(svc, "collect", lambda **kwargs: (_ for _ in ()).throw(OSError("synthetic")))
    dispatcher = IncidentDispatcher(svc, max_queue=1)
    payload = dict(source="synthetic", component="live", category="provider_error",
        error_code="E", title="Synthetic", safe_summary="Synthetic", severity="leve")
    for _ in range(100): dispatcher.submit(**payload)
    dispatcher.shutdown(flush=True)
    assert dispatcher.failure_count >= 1 and dispatcher.dropped_count >= 0


def test_normal_telemetry_does_not_enqueue_incident(tmp_path, monkeypatch):
    manager = IntegrationManager(telemetry_path=tmp_path/"t.json", events_path=tmp_path/"e.json",
        preferences_path=tmp_path/"p.json", incidents_path=tmp_path/"i.json", env_path=tmp_path/".env", api_key="x")
    calls = []
    monkeypatch.setattr(manager.incident_dispatcher, "submit", lambda **kwargs: calls.append(kwargs))
    manager.record_usage(None, request_type="live", success=True)
    assert calls == [] and not (tmp_path/"i.json").exists()
    manager.shutdown()


@pytest.mark.parametrize("count", [0, 1, 2, 5])
def test_llm_summary_counts_and_bounds_small_lists(tmp_path, count):
    svc = service(tmp_path)
    for index in range(count):
        emit(svc, source=f"synthetic-{index}", error_code=f"E{index}", severity="medio")
    result = svc.list_system_incidents_for_llm()
    assert result["total"] == result["returned"] == count
    assert result["limit"] == IncidentService.DEFAULT_LLM_LIST_LIMIT
    assert result["has_more"] is False
    assert result["counts_by_severity"] == {"grave": 0, "medio": count, "leve": 0}


def test_llm_summary_limits_one_hundred_incidents_and_preserves_order(tmp_path):
    svc = service(tmp_path)
    for index in range(100):
        emit(svc, source=f"synthetic-{index}", error_code=f"E{index}",
             severity=("grave" if index == 99 else "leve"))
    result = svc.list_system_incidents_for_llm()
    assert result["total"] == 100 and result["returned"] == 5 and result["has_more"] is True
    assert result["limit"] == IncidentService.DEFAULT_LLM_LIST_LIMIT
    assert result["incidents"][0]["severity"] == "grave"
    maximum = svc.list_system_incidents_for_llm(limit=999)
    assert maximum["limit"] == IncidentService.MAX_LLM_LIST_LIMIT
    assert len(maximum["incidents"]) == IncidentService.MAX_LLM_LIST_LIMIT


@pytest.mark.parametrize("requested", [None, "invalid", 0, -5])
def test_llm_summary_normalizes_invalid_limits(tmp_path, requested):
    svc = service(tmp_path)
    emit(svc)
    result = svc.list_system_incidents_for_llm(limit=requested)
    expected = 1 if requested in (0, -5) else IncidentService.DEFAULT_LLM_LIST_LIMIT
    assert result["limit"] == expected


def test_llm_list_and_detail_use_separate_allowlists(tmp_path):
    svc = service(tmp_path)
    item = emit(svc, safe_summary="summary " + "x" * 800,
                metadata={"exception_class": "SyntheticError", "password": "fake", "payload": "fake"})
    records = json.loads(svc.store.path.read_text(encoding="utf-8"))
    records[0].update(diagnosis="diagnosis " + "d" * 800, metadata={"password": "fake-secret", "stack": "raw"})
    svc.store.path.write_text(json.dumps(records), encoding="utf-8")
    listed = svc.list_system_incidents_for_llm()["incidents"][0]
    detailed = svc.get_incident_details_for_llm(item["incident_id"])["incident"]
    assert set(listed) == set(IncidentService.SUMMARY_FIELDS)
    assert not {"safe_summary", "diagnosis", "metadata", "error_code", "first_seen"} & set(listed)
    assert set(detailed) <= set(IncidentService.DETAIL_FIELDS)
    assert "metadata" not in detailed and "fingerprint" not in detailed
    assert len(detailed["diagnosis"]) <= 500 and "fake-secret" not in json.dumps(detailed)


def test_llm_payload_contract_has_only_bounded_summary_and_sanitized_detail(tmp_path):
    svc = service(tmp_path)
    item = emit(svc)
    records = json.loads(svc.store.path.read_text(encoding="utf-8"))
    records[0].update(
        stack="raw-stack", payload={"password": "fake-password"}, messages=["private"],
        metadata={"password": "fake-password", "stack": "raw-stack"},
        diagnosis="Observed failure. Hypothesis: synthetic cause.",
    )
    svc.store.path.write_text(json.dumps(records), encoding="utf-8")

    listed = svc.list_system_incidents_for_llm()
    detailed = svc.get_incident_details_for_llm(item["incident_id"])

    assert set(listed) == {
        "total", "returned", "limit", "has_more", "counts_by_severity",
        "incidents", "source_of_truth", "response_instruction",
    }
    assert set(listed["incidents"][0]) == set(IncidentService.SUMMARY_FIELDS)
    assert set(detailed["incident"]) <= set(IncidentService.DETAIL_FIELDS)
    serialized_list = json.dumps(listed)
    serialized_detail = json.dumps(detailed)
    for forbidden in ("raw-stack", "fake-password", '"metadata"', '"payload"', '"messages"'):
        assert forbidden not in serialized_list
        assert forbidden not in serialized_detail


def test_llm_follow_up_uses_summary_id_for_sanitized_detail_without_memory(tmp_path):
    svc = service(tmp_path); item = emit(svc)
    summary_id = svc.list_system_incidents_for_llm()["incidents"][0]["incident_id"]
    detail = svc.get_incident_details_for_llm(summary_id)
    assert summary_id == item["incident_id"] and detail["found"] is True
