import asyncio
from types import SimpleNamespace

from system_monitor import HealthEvaluator, SystemMonitor, SystemMonitorTask


class FakePsutil:
    def __init__(self):
        self.network = SimpleNamespace(bytes_recv=1_000, bytes_sent=500)

    def cpu_percent(self, interval=None): return 12.4
    def virtual_memory(self): return SimpleNamespace(percent=38.1, used=12, total=32)
    def disk_usage(self, _path): return SimpleNamespace(percent=21.0, used=21, total=100)
    def net_io_counters(self): return self.network
    def boot_time(self): return 100.0


class FakeProcess:
    def __init__(self, pid, rss=0, children=None, fails=False):
        self.pid = pid
        self._rss = rss
        self._children = children or []
        self._fails = fails

    def children(self, recursive=False): return self._children
    def memory_info(self):
        if self._fails:
            raise RuntimeError("process exited")
        return SimpleNamespace(rss=self._rss)


class ProcessPsutil(FakePsutil):
    def __init__(self, root):
        super().__init__()
        self.root = root

    def Process(self, _pid): return self.root


def test_payload_contains_real_metric_contract_and_network_delta():
    fake = FakePsutil()
    ticks = iter([10.0, 12.0])
    monitor = SystemMonitor(psutil_module=fake, clock=lambda: next(ticks), wall_clock=lambda: 200.0,
                            gpu_runner=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    first = monitor.collect()
    fake.network = SimpleNamespace(bytes_recv=3_000, bytes_sent=1_500)
    second = monitor.collect()
    assert first["available"] is True
    assert 0 <= first["cpu"]["percent"] <= 100
    assert first["memory"] == {"percent": 38.1, "used_bytes": 12, "total_bytes": 32}
    assert first["disk"]["percent"] == 21.0
    assert second["network"] == {"download_bps": 1000.0, "upload_bps": 500.0}
    assert second["uptime_seconds"] == 100
    assert second["gpu"]["available"] is False


def test_gpu_payload_is_real_when_nvidia_smi_is_available():
    result = SimpleNamespace(stdout="NVIDIA Test, 42, 1024, 8192, 55\n")
    monitor = SystemMonitor(psutil_module=FakePsutil(), clock=lambda: 10, wall_clock=lambda: 200,
                            gpu_runner=lambda *a, **k: result)
    gpu = monitor.collect()["gpu"]
    assert gpu["available"] is True
    assert gpu["percent"] == 42
    assert gpu["temperature_c"] == 55
    assert gpu["memory_total_bytes"] == 8192 * 1024 * 1024


def test_application_memory_sums_unique_live_processes():
    shared = FakeProcess(12, 300)
    exited = FakeProcess(13, fails=True)
    root = FakeProcess(10, 100, [FakeProcess(11, 200), shared, shared, exited])
    monitor = SystemMonitor(psutil_module=ProcessPsutil(root), application_root_pid=10,
                            clock=lambda: 10, wall_clock=lambda: 200,
                            gpu_runner=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    application = monitor.collect()["application"]
    assert application == {"memory_bytes": 600, "process_count": 3, "memory_scope": "full_process_tree"}


def test_application_memory_reports_unavailable_when_root_is_gone():
    fake = FakePsutil()
    fake.Process = lambda _pid: (_ for _ in ()).throw(RuntimeError("gone"))
    monitor = SystemMonitor(psutil_module=fake, application_root_pid=999)
    assert monitor._application_memory() == {
        "memory_bytes": None, "process_count": 0, "memory_scope": "unavailable"
    }


def test_health_requires_sustained_critical_samples():
    health = HealthEvaluator(critical_samples=3)
    assert health.evaluate(99, 20, 20)[0] == "warning"
    assert health.evaluate(10, 20, 20)[0] == "healthy"
    assert [health.evaluate(99, 20, 20)[0] for _ in range(3)] == ["warning", "warning", "critical"]
    assert health.evaluate(10, 88, 20) == ("warning", "Memória alta")


def test_collect_safe_never_propagates_metric_errors():
    fake = FakePsutil()
    fake.cpu_percent = lambda interval=None: (_ for _ in ()).throw(RuntimeError("sensor failed"))
    payload = SystemMonitor(psutil_module=fake, wall_clock=lambda: 200).collect_safe()
    assert payload["available"] is False
    assert payload["cpu"] is None
    assert payload["gpu"]["available"] is False


def test_task_start_is_idempotent_and_stop_cancels_single_task():
    async def scenario():
        emitted = []
        monitor = SimpleNamespace(collect_safe=lambda: {"available": True})
        owner = SystemMonitorTask(monitor, emitted.append, interval=60)
        first = owner.start()
        assert owner.start() is first
        for _ in range(20):
            if emitted:
                break
            await asyncio.sleep(0.01)
        assert emitted == [{"available": True}]
        await owner.stop()
        assert first.cancelled()
        assert owner.task is None
    asyncio.run(scenario())


def test_task_survives_emit_errors():
    async def scenario():
        calls = 0
        monitor = SimpleNamespace(collect_safe=lambda: {"available": True})

        def fail_emit(_payload):
            nonlocal calls
            calls += 1
            raise RuntimeError("socket temporarily unavailable")

        owner = SystemMonitorTask(monitor, fail_emit, interval=0.01)
        task = owner.start()
        await asyncio.sleep(0.05)
        assert calls >= 2
        assert not task.done()
        await owner.stop()
    asyncio.run(scenario())
