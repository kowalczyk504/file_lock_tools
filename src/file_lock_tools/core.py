"""Advisory file locking for POSIX systems.

The lock is implemented with ``fcntl.flock``, which is tied to the open file
description.  When the owning process dies for any reason -- including
``SIGKILL`` -- the kernel closes the descriptor and drops the lock.  That is the
whole point: an ordinary lock file that is created and later unlinked leaks
whenever the process is killed between those two steps.  ``flock`` has no such
window.

Two consequences worth knowing before you use this module:

* ``flock`` is advisory.  A process that does not call ``flock`` can still write
to the file.  Every participant must use this module (or ``flock`` directly).
* ``flock`` locks are per open file description, not per process.  Two
  ``FileLock`` instances in the same process, both backed by separate ``open``
  calls, do contend with each other.  That is what makes the tests below
  meaningful.
"""

from __future__ import annotations

import errno
import fcntl
import os
import time
from typing import Callable, Optional


class LockError(Exception):
    """Base class for errors raised by this module."""


class LockTimeout(LockError):
    """Raised when ``FileLock.acquire`` cannot take the lock in time."""


class FileLock:
    """An advisory exclusive lock backed by a file.

    The lock is acquired with a non-blocking ``flock`` call in a loop until
    either the lock is held or the deadline passes.  The loop is deliberate:
    blocking ``flock`` cannot be interrupted by a timeout, and polling with a
    caller-supplied clock keeps the retry logic testable without sleeping.

    ``clock`` must be a zero-argument callable returning a float number of
    seconds.  ``time.monotonic`` is the default because it cannot jump when the
    wall clock is adjusted.  Tests pass a fake clock so that no test ever
    depends on real elapsed time.
    """

    def __init__(
        self,
        path: str,
        *,
        poll_interval: float = 0.05,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self.path = os.fspath(path)
        self.poll_interval = float(poll_interval)
        self._clock = clock
        self._fd: Optional[int] = None

    @property
    def locked(self) -> bool:
        """True while this instance holds the lock."""
        return self._fd is not None

    def acquire(self, timeout: Optional[float] = None) -> None:
        """Take the lock, waiting at most ``timeout`` seconds.

        ``timeout=None`` waits forever.  ``timeout=0`` performs a single
        non-blocking attempt.  On success the file descriptor is kept open for
        the lifetime of the lock; on failure ``LockTimeout`` is raised and the
        descriptor is closed so no partial state is left behind.
        """
        if self._fd is not None:
            raise LockError("lock is already held by this instance")
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative or None")

        # Open in append mode so the file is created if missing but never
        # truncated.  Truncating would be a data-loss bug for a lock file that
        # a caller may also use as a coordination point.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)

        deadline = None if timeout is None else self._clock() + timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd = fd
                    return
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES):
                        raise
                if deadline is not None and self._clock() >= deadline:
                    raise LockTimeout(
                        f"could not acquire {self.path!r} within {timeout} seconds"
                    )
                # A caller-supplied clock may not advance between polls (for
                # example a fake clock in a test).  Sleeping here would make
                # the timeout path untestable, so the loop simply retries.
                if self._clock() < (deadline if deadline is not None else float("inf")):
                    time.sleep(self.poll_interval)
        except BaseException:
            os.close(fd)
            raise

    def release(self) -> None:
        """Release the lock if held.  Safe to call more than once."""
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def __del__(self) -> None:
        # Best-effort cleanup.  The kernel would drop the lock when the process
        # exits anyway, but closing the descriptor promptly avoids leaking file
        # descriptors in long-lived processes that create many locks.
        try:
            self.release()
        except Exception:
            pass
