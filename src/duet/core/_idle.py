"""Track inactivity independently of the listener and desktop UI."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import monotonic


class IdleTracker:
    def __init__(
        self, timeout_seconds: float, *, clock: Callable[[], float] = monotonic
    ):
        self.set_timeout(timeout_seconds)
        self._clock = clock
        self._in_flight = 0
        self._last_activity = clock()

    def set_timeout(self, timeout_seconds: float) -> None:
        if not 0 < timeout_seconds < float("inf"):
            raise ValueError("Idle timeout must be finite and positive")
        self._timeout = timeout_seconds

    def touch(self) -> None:
        self._last_activity = self._clock()

    def expired(self) -> bool:
        return (
            self._in_flight == 0
            and self._clock() - self._last_activity >= self._timeout
        )

    @contextmanager
    def hold(self) -> Iterator[None]:
        """Keep the listener available until an in-flight request finishes."""
        self._in_flight += 1
        try:
            yield
        finally:
            self._in_flight -= 1

    @contextmanager
    def activity(self) -> Iterator[None]:
        """Keep the listener available and start a fresh idle period on completion."""
        with self.hold():
            try:
                yield
            finally:
                self.touch()
