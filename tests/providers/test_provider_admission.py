from __future__ import annotations

import asyncio

import pytest

from pawbot.providers.admission import ProviderRequestAdmission


def test_provider_specific_environment_overrides_global(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAWBOT_PROVIDER_MAX_INFLIGHT", "4")
    monkeypatch.setenv("PAWBOT_PROVIDER_RPM", "60")
    monkeypatch.setenv("PAWBOT_DEEPSEEK_MAX_INFLIGHT", "1")
    monkeypatch.setenv("PAWBOT_DEEPSEEK_RPM", "12")

    admission = ProviderRequestAdmission.from_environment("deepseek")

    assert admission.max_inflight == 1
    assert admission.requests_per_minute == 12


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leak_inflight_permit() -> None:
    admission = ProviderRequestAdmission(max_inflight=1)
    await admission.acquire()
    waiter = asyncio.create_task(admission.acquire())
    await asyncio.sleep(0)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    admission.release()
    await asyncio.wait_for(admission.acquire(), timeout=0.2)
    admission.release()


@pytest.mark.asyncio
async def test_rolling_rate_window_expires_without_real_sleep() -> None:
    now = 0.0

    def clock() -> float:
        return now

    admission = ProviderRequestAdmission(requests_per_minute=1, clock=clock)
    await admission.acquire()
    now = 61.0
    await admission.acquire()
