import time
from collections.abc import Iterator
from contextlib import contextmanager


class StageTimer:
    """Records how long each named stage of a split takes, in seconds.

    Used to get honest before/after numbers for the job queue: the same
    stages are timed whether the split runs inside a request or in the
    worker, so the totals are directly comparable."""

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self.stages: dict[str, float] = {}
        # Sizes that explain the timings, e.g. how many tracks needed a
        # fresh LLM tag vs. came from the tag cache.
        self.counts: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = round(time.perf_counter() - start, 3)

    def summary(self) -> dict[str, float]:
        return {**self.stages, "total": round(time.perf_counter() - self._started, 3)}
