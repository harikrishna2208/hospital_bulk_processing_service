import pytest

from app.utils.retry import retry_async


async def test_succeeds_on_first_attempt():
    calls = []

    async def op():
        calls.append(1)
        return "ok"

    result = await retry_async(op, max_attempts=3, base_delay_seconds=0.001, is_retryable=lambda e: True)
    assert result == "ok"
    assert len(calls) == 1


async def test_retries_retryable_errors_until_success():
    calls = []

    async def op():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("transient")
        return "ok"

    result = await retry_async(op, max_attempts=3, base_delay_seconds=0.001, is_retryable=lambda e: True)
    assert result == "ok"
    assert len(calls) == 3


async def test_raises_after_exhausting_max_attempts():
    calls = []

    async def op():
        calls.append(1)
        raise ValueError("always fails")

    with pytest.raises(ValueError):
        await retry_async(op, max_attempts=3, base_delay_seconds=0.001, is_retryable=lambda e: True)
    assert len(calls) == 3


async def test_does_not_retry_non_retryable_errors():
    calls = []

    async def op():
        calls.append(1)
        raise ValueError("non-retryable")

    with pytest.raises(ValueError):
        await retry_async(op, max_attempts=3, base_delay_seconds=0.001, is_retryable=lambda e: False)
    assert len(calls) == 1
