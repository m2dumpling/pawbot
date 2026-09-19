"""Small, dependency-free admission controls for provider requests.

The default is deliberately unlimited so existing single-user installations
keep their behaviour.  Deployments can opt in through environment variables:

* ``PAWBOT_PROVIDER_MAX_INFLIGHT`` / ``PAWBOT_<PROVIDER>_MAX_INFLIGHT``
* ``PAWBOT_PROVIDER_RPM`` / ``PAWBOT_<PROVIDER>_RPM``

The provider-specific value wins.  This is a process-local guardrail, not a
distributed quota.  It protects one gateway process from accidental bursts;
an external rate limiter is still required for a multi-instance deployment.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import deque
from collections.abc import Callable

_WINDOW_SECONDS = 60.0
_POSITIVE_INT = re.compile(r"^[0-9]+$")


def _env_int(name: str, default: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw or not _POSITIVE_INT.fullmatch(raw):
        return default
    value = int(raw)
    return value if value > 0 else 0


def _provider_env_prefix(provider_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", provider_name).strip("_")
    return normalized.upper() or "PROVIDER"


class ProviderRequestAdmission:
    """Process-local concurrency and rolling-window request admission.

    A request waits before it reaches the provider SDK.  Cancellation while
    waiting is propagated and never leaks an in-flight permit.
    """

    def __init__(
        self,
        *,
        max_inflight: int = 0,
        requests_per_minute: int = 0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_inflight = max(0, int(max_inflight))
        self.requests_per_minute = max(0, int(requests_per_minute))
        self._clock = clock
        self._inflight = (
            asyncio.Semaphore(self.max_inflight) if self.max_inflight > 0 else None
        )
        self._rate_lock = asyncio.Lock()
        self._request_times: deque[float] = deque()

    @classmethod
    def from_environment(cls, provider_name: str) -> "ProviderRequestAdmission":
        prefix = _provider_env_prefix(provider_name)
        return cls(
            max_inflight=_env_int(
                f"PAWBOT_{prefix}_MAX_INFLIGHT",
                _env_int("PAWBOT_PROVIDER_MAX_INFLIGHT"),
            ),
            requests_per_minute=_env_int(
                f"PAWBOT_{prefix}_RPM",
                _env_int("PAWBOT_PROVIDER_RPM"),
            ),
        )

    @property
    def enabled(self) -> bool:
        return self._inflight is not None or self.requests_per_minute > 0

    async def _wait_for_rate_slot(self) -> None:
        if self.requests_per_minute <= 0:
            return
        while True:
            async with self._rate_lock:
                now = self._clock()
                cutoff = now - _WINDOW_SECONDS
                while self._request_times and self._request_times[0] <= cutoff:
                    self._request_times.popleft()
                if len(self._request_times) < self.requests_per_minute:
                    self._request_times.append(now)
                    return
                wait_seconds = max(0.001, self._request_times[0] + _WINDOW_SECONDS - now)
            await asyncio.sleep(wait_seconds)

    async def acquire(self) -> None:
        acquired = False
        try:
            if self._inflight is not None:
                await self._inflight.acquire()
                acquired = True
            await self._wait_for_rate_slot()
        except BaseException:
            if acquired and self._inflight is not None:
                self._inflight.release()
            raise

    def release(self) -> None:
        if self._inflight is not None:
            self._inflight.release()

    async def __aenter__(self) -> "ProviderRequestAdmission":
        await self.acquire()
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.release()


__all__ = ["ProviderRequestAdmission"]
