"""Retry policy for upstream reads.

Reads are retried; writes are not.  Drone's promote endpoint and Gitea's release
endpoint are not idempotent, so a retry there can create a second production
deployment or a second tag.  Recovering from an uncertain write is reconciliation
work (see DeploymentService._reconcile_promotion), not something a transport
layer can decide.

Backoff is "full jitter": each attempt sleeps a random amount in
[0, min(cap, base * 2**attempt)).  Uniform backoff would make every poller in a
bundle retry in lockstep and hit a struggling Drone at the same instant.
"""

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_seconds: float = 0.25
    max_seconds: float = 2.0

    def delay_for(self, attempt: int) -> float:
        window = min(self.max_seconds, self.base_seconds * (2**attempt))
        return random.uniform(0, window)


def retry_read(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy,
    retry_on: tuple[type[BaseException], ...],
    description: str,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call operation, retrying only the exception types named in retry_on.

    The final attempt's exception propagates unchanged so callers keep their
    stable error codes.
    """
    last_error: BaseException | None = None
    for attempt in range(policy.attempts):
        try:
            return operation()
        except retry_on as exc:
            last_error = exc
            if attempt == policy.attempts - 1:
                break
            delay = policy.delay_for(attempt)
            logger.warning(
                "Upstream read failed, retrying operation=%s attempt=%s/%s delay=%.2fs error=%s",
                description,
                attempt + 1,
                policy.attempts,
                delay,
                exc.__class__.__name__,
            )
            sleep(delay)
    assert last_error is not None  # noqa: S101 - unreachable unless retry_on is empty
    raise last_error
