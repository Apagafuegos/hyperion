"""Host sampler: bounded in-memory history with monotonic-counter derivation.

Samples are held for roughly `history_seconds`; CPU utilization and network
rates are derived from successive /proc counter deltas, detecting resets so a
reboot or interface flap never produces a spurious negative rate or a
double-counted sample.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ..models import HostEvidence
from ..providers.host import HostProvider

logger = logging.getLogger("hyperion.host.sampler")

CpuJiffies = tuple[int | None, int | None, datetime]
Counters = dict[str, tuple[int, int]]


class HostSampler:
    """Owns the sampling loop and the history ring."""

    def __init__(
        self,
        provider: HostProvider,
        interval_seconds: float = 7.0,
        history_seconds: int = 1800,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._interval = interval_seconds
        self._history_seconds = history_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._samples: deque[HostEvidence] = deque()
        self._lock = threading.Lock()
        self._task: asyncio.Task[None] | None = None
        self._last_counters: tuple[Counters, datetime] | None = None
        self._last_cpu_jiffies: CpuJiffies | None = None

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self._loop())
        return self._task

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            started = asyncio.get_running_loop().time()
            try:
                evidence = await self._provider.observe()
                self.record(evidence)
            except Exception as exc:  # provider boundary; keep the last samples
                logger.error("host sample cycle failed: %s", exc)
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.0, self._interval - elapsed))

    def record(self, evidence: HostEvidence) -> None:
        """Derive rates against the previous sample, then store."""
        now = self._now()
        derived = self._derive(evidence, now)
        with self._lock:
            self._samples.append(derived)
            cutoff = now.timestamp() - self._history_seconds
            while self._samples and self._samples[0].observed_at.timestamp() < cutoff:
                self._samples.popleft()

    def current(self) -> HostEvidence | None:
        with self._lock:
            return self._samples[-1] if self._samples else None

    def history(self, window_seconds: int) -> list[HostEvidence]:
        """Return samples within the requested window, oldest first."""
        with self._lock:
            cutoff = self._now().timestamp() - window_seconds
            return [s for s in self._samples if s.observed_at.timestamp() >= cutoff]

    def _derive(self, evidence: HostEvidence, now: datetime) -> HostEvidence:
        """Derive CPU percent and interface rates from the prior counters.

        Counter resets are detected as a decrease in the raw value; the delta
        is treated as unknown (None) rather than a wrap-around computation.
        """
        model = evidence.model_copy(deep=True)

        self._derive_cpu(model, now)
        self._derive_rates(model, now)
        return model

    def _derive_cpu(self, evidence: HostEvidence, now: datetime) -> None:
        total, idle, sampled_at = self._sample_cpu_jiffies(now)
        prev = self._last_cpu_jiffies
        self._last_cpu_jiffies = (total, idle, sampled_at)
        if prev is None or total is None:
            return
        prev_total, prev_idle, prev_at = prev
        if prev_total is None:
            return
        if total < prev_total:
            return  # counter reset; leave utilization unknown
        delta_total = total - prev_total
        delta_idle = (idle - prev_idle) if (idle and prev_idle) else 0
        seconds = (sampled_at - prev_at).total_seconds()
        if delta_total > 0 and seconds > 0:
            usage = (delta_total - delta_idle) / delta_total * 100
            evidence.cpu.utilization_percent = round(max(0.0, min(100.0, usage)), 1)

    def _derive_rates(self, evidence: HostEvidence, now: datetime) -> None:
        prev = self._last_counters
        if prev is not None:
            prev_map, prev_at = prev
            seconds = (now - prev_at).total_seconds()
            if seconds > 0:
                for interface in evidence.interfaces:
                    prior = prev_map.get(interface.name)
                    if prior is None:
                        continue
                    prev_rx, prev_tx = prior
                    if (
                        interface.rx_bytes_total is not None
                        and interface.rx_bytes_total >= prev_rx
                    ):
                        interface.rx_bytes_per_second = round(
                            (interface.rx_bytes_total - prev_rx) / seconds, 1
                        )
                    if (
                        interface.tx_bytes_total is not None
                        and interface.tx_bytes_total >= prev_tx
                    ):
                        interface.tx_bytes_per_second = round(
                            (interface.tx_bytes_total - prev_tx) / seconds, 1
                        )
        counters: Counters = {
            i.name: (i.rx_bytes_total or 0, i.tx_bytes_total or 0)
            for i in evidence.interfaces
        }
        self._last_counters = (counters, now)

    def _sample_cpu_jiffies(self, now: datetime) -> CpuJiffies:
        text = self._proc_stat()
        total: int | None = None
        idle: int | None = None
        for line in text.splitlines():
            if not line.startswith("cpu "):
                continue
            fields = line.split()[1:]
            if len(fields) < 4:
                return None, None, now
            vals = [int(f) for f in fields if f.isdigit()]
            if not vals:
                return None, None, now
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            total = sum(vals)
            break
        return total, idle, now

    def _proc_stat(self) -> str:
        proc = getattr(self._provider, "_proc", None)
        if proc is None:
            return ""
        if isinstance(proc, Path):
            try:
                return proc.read_text(encoding="utf-8")
            except OSError:
                return ""
        read = getattr(proc, "read_text", None)
        if callable(read):
            try:
                return cast(str, read())
            except OSError:
                return ""
        return ""
