"""Lightweight, non-persistent host telemetry for the Veronica status panel."""

from __future__ import annotations

import asyncio
import getpass
import inspect
import os
import platform
import socket
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import psutil


def _system_volume() -> str:
    anchor = Path(os.environ.get("SystemRoot", Path.cwd().anchor or os.sep)).anchor
    return anchor or os.sep


class HealthEvaluator:
    """Debounce critical load while still surfacing useful warnings immediately."""

    def __init__(self, critical_samples: int = 3):
        self._critical_samples = critical_samples
        self._recent_critical = deque(maxlen=critical_samples)

    def evaluate(self, cpu: float, memory: float, disk: float) -> tuple[str, str]:
        critical_now = disk >= 97 or memory >= 95 or cpu >= 95
        self._recent_critical.append(critical_now)
        sustained_critical = len(self._recent_critical) == self._critical_samples and all(self._recent_critical)
        if sustained_critical:
            if disk >= 97:
                return "critical", "Disco quase cheio"
            if memory >= 95:
                return "critical", "Memória crítica"
            return "critical", "Carga crítica"
        if disk >= 90:
            return "warning", "Disco quase cheio"
        if memory >= 85:
            return "warning", "Memória alta"
        if cpu >= 85:
            return "warning", "Carga elevada"
        return "healthy", "Tudo certo"


class SystemMonitor:
    def __init__(
        self,
        *,
        psutil_module=psutil,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        gpu_runner: Callable[..., Any] = subprocess.run,
        gpu_interval: float = 5.0,
        application_root_pid: int | None = None,
    ):
        self._psutil = psutil_module
        self._clock = clock
        self._wall_clock = wall_clock
        self._gpu_runner = gpu_runner
        self._gpu_interval = gpu_interval
        configured_root = application_root_pid or os.environ.get("ADA_ELECTRON_PID")
        self._application_root_pid = int(configured_root) if configured_root else None
        self._last_network = None
        self._last_network_at = None
        self._last_gpu_at = None
        self._gpu_cache = self._unavailable_gpu()
        self._health = HealthEvaluator()

    def _application_memory(self) -> dict[str, Any]:
        """Sum RSS once for every live process in Veronica's known process tree."""
        root_pid = self._application_root_pid or os.getpid()
        scope = "full_process_tree" if self._application_root_pid else "backend_only"
        try:
            root = self._psutil.Process(root_pid)
            processes = [root, *root.children(recursive=True)]
        except Exception:
            return {"memory_bytes": None, "process_count": 0, "memory_scope": "unavailable"}

        seen: set[int] = set()
        total = 0
        for process in processes:
            try:
                if process.pid in seen:
                    continue
                rss = int(process.memory_info().rss)
                seen.add(process.pid)
                total += rss
            except Exception:
                # A renderer may exit between tree discovery and RSS collection.
                continue
        if not seen:
            return {"memory_bytes": None, "process_count": 0, "memory_scope": "unavailable"}
        return {"memory_bytes": total, "process_count": len(seen), "memory_scope": scope}

    @staticmethod
    def _unavailable_gpu() -> dict[str, Any]:
        return {
            "available": False,
            "name": None,
            "percent": None,
            "memory_used_bytes": None,
            "memory_total_bytes": None,
            "temperature_c": None,
        }

    def _gpu_status(self, now: float) -> dict[str, Any]:
        if self._last_gpu_at is not None and now - self._last_gpu_at < self._gpu_interval:
            return self._gpu_cache
        self._last_gpu_at = now
        try:
            result = self._gpu_runner(
                [
                    "nvidia-smi",
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            row = result.stdout.strip().splitlines()[0]
            name, usage, used_mib, total_mib, temperature = (value.strip() for value in row.split(",", 4))
            self._gpu_cache = {
                "available": True,
                "name": name,
                "percent": float(usage),
                "memory_used_bytes": int(float(used_mib) * 1024 * 1024),
                "memory_total_bytes": int(float(total_mib) * 1024 * 1024),
                "temperature_c": float(temperature),
            }
        except (FileNotFoundError, IndexError, OSError, subprocess.SubprocessError, ValueError):
            self._gpu_cache = self._unavailable_gpu()
        return self._gpu_cache

    def collect(self) -> dict[str, Any]:
        now = self._clock()
        cpu = float(self._psutil.cpu_percent(interval=None))
        memory = self._psutil.virtual_memory()
        disk = self._psutil.disk_usage(_system_volume())
        network = self._psutil.net_io_counters()
        elapsed = now - self._last_network_at if self._last_network_at is not None else 0
        download_bps = upload_bps = 0.0
        if self._last_network is not None and elapsed > 0:
            download_bps = max(0.0, (network.bytes_recv - self._last_network.bytes_recv) / elapsed)
            upload_bps = max(0.0, (network.bytes_sent - self._last_network.bytes_sent) / elapsed)
        self._last_network = network
        self._last_network_at = now
        status, status_text = self._health.evaluate(cpu, float(memory.percent), float(disk.percent))
        return {
            "timestamp": datetime.fromtimestamp(self._wall_clock(), timezone.utc).isoformat(),
            "available": True,
            "overall_status": status,
            "status_text": status_text,
            "cpu": {"percent": round(cpu, 1)},
            "memory": {
                "percent": round(float(memory.percent), 1),
                "used_bytes": int(memory.used),
                "total_bytes": int(memory.total),
            },
            "disk": {
                "percent": round(float(disk.percent), 1),
                "used_bytes": int(disk.used),
                "total_bytes": int(disk.total),
                "mount": _system_volume(),
            },
            "network": {
                "download_bps": round(download_bps, 1),
                "upload_bps": round(upload_bps, 1),
            },
            "application": self._application_memory(),
            "uptime_seconds": max(0, int(self._wall_clock() - self._psutil.boot_time())),
            "system": {
                "hostname": socket.gethostname(),
                "os": f"{platform.system()} {platform.release()}",
                "user": getpass.getuser(),
            },
            "gpu": self._gpu_status(now),
            "battery": None,
            "fans": None,
        }

    def collect_safe(self) -> dict[str, Any]:
        try:
            return self.collect()
        except Exception:
            return {
                "timestamp": datetime.fromtimestamp(self._wall_clock(), timezone.utc).isoformat(),
                "available": False,
                "overall_status": "unavailable",
                "status_text": "Dados temporariamente indisponíveis",
                "cpu": None,
                "memory": None,
                "disk": None,
                "network": None,
                "application": {"memory_bytes": None, "process_count": 0, "memory_scope": "unavailable"},
                "uptime_seconds": None,
                "system": None,
                "gpu": self._unavailable_gpu(),
                "battery": None,
                "fans": None,
            }


class SystemMonitorTask:
    """Own exactly one background collection task for all Socket.IO clients."""

    def __init__(self, monitor: SystemMonitor, emit: Callable[[dict[str, Any]], Awaitable[None] | None], interval=1.0):
        self.monitor = monitor
        self.emit = emit
        self.interval = interval
        self.task: asyncio.Task | None = None
        self.latest: dict[str, Any] | None = None

    def start(self) -> asyncio.Task:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run(), name="system-monitor")
        return self.task

    async def stop(self) -> None:
        if self.task is None:
            return
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        self.task = None

    async def _run(self) -> None:
        while True:
            try:
                self.latest = await asyncio.to_thread(self.monitor.collect_safe)
                result = self.emit(self.latest)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:
                # Telemetry must never take down the backend or its own future samples.
                pass
            await asyncio.sleep(self.interval)
