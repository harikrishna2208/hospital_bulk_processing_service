import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay_seconds: float,
    is_retryable: Callable[[BaseException], bool],
    log_context: str = "",
) -> T:
    """Runs `operation` with exponential backoff, retrying only exceptions `is_retryable` accepts."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return await operation()
        except Exception as exc:
            if attempt >= max_attempts or not is_retryable(exc):
                raise
            delay = base_delay_seconds * (2 ** (attempt - 1))
            logger.warning(
                "%s retry attempt=%d/%d after error=%s delay=%.2fs",
                log_context,
                attempt,
                max_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
