import errno
import os
import shutil
import tempfile
import unittest

from file_lock_tools import FileLock, LockError, LockTimeout


class FakeClock:
    """Deterministic clock.  Each call advances by ``step`` seconds."""

    def __init__(self, start=0.0, step=0.0):
        self.now = float(start)
        self.step = float(step)

    def __call__(self):
        value = self.now
        self.now += self.step
        return value


class FileLockTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "lockfile")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_acquire_creates_file_and_release_releases(self):
        lock = FileLock(self.path, clock=FakeClock(step=1.0))
        self.assertFalse(lock.locked)
        lock.acquire(timeout=0)
        self.assertTrue(lock.locked)
        self.assertTrue(os.path.exists(self.path))
        lock.release()
        self.assertFalse(lock.locked)

    def test_context_manager_releases(self):
        with FileLock(self.path, clock=FakeClock(step=1.0)) as lock:
            self.assertTrue(lock.locked)
        self.assertFalse(lock.locked)

    def test_second_instance_times_out(self):
        first = FileLock(self.path, clock=FakeClock(step=1.0))
        second = FileLock(self.path, clock=FakeClock(step=1.0))
        first.acquire(timeout=0)
        try:
            with self.assertRaises(LockTimeout):
                second.acquire(timeout=0)
            self.assertFalse(second.locked)
        finally:
            first.release()

    def test_second_instance_succeeds_after_release(self):
        first = FileLock(self.path, clock=FakeClock(step=1.0))
        second = FileLock(self.path, clock=FakeClock(step=1.0))
        first.acquire(timeout=0)
        first.release()
        second.acquire(timeout=0)
        self.assertTrue(second.locked)
        second.release()

    def test_timeout_is_checked_against_clock(self):
        first = FileLock(self.path, clock=FakeClock(step=1.0))
        second = FileLock(self.path, clock=FakeClock(step=1.0))
        first.acquire(timeout=0)
        try:
            # The fake clock advances by 1.0 on every call.  A timeout of 0.5
            # must therefore be exhausted on the first deadline check.
            with self.assertRaises(LockTimeout):
                second.acquire(timeout=0.5)
        finally:
            first.release()

    def test_acquire_twice_raises(self):
        lock = FileLock(self.path, clock=FakeClock(step=1.0))
        lock.acquire(timeout=0)
        try:
            with self.assertRaises(LockError):
                lock.acquire(timeout=0)
        finally:
            lock.release()

    def test_release_without_acquire_is_noop(self):
        lock = FileLock(self.path, clock=FakeClock(step=1.0))
        lock.release()
        self.assertFalse(lock.locked)

    def test_negative_timeout_rejected(self):
        lock = FileLock(self.path, clock=FakeClock(step=1.0))
        with self.assertRaises(ValueError):
            lock.acquire(timeout=-1)

    def test_non_positive_poll_interval_rejected(self):
        with self.assertRaises(ValueError):
            FileLock(self.path, poll_interval=0, clock=FakeClock(step=1.0))

    def test_lock_survives_unlink_of_lock_file(self):
        # Unlinking the path does not release the lock: the lock lives on the
        # open file description, not on the directory entry.  A second lock
        # opened against the same path therefore still contends with the first
        # open file description.
        first = FileLock(self.path, clock=FakeClock(step=1.0))
        second = FileLock(self.path, clock=FakeClock(step=1.0))
        first.acquire(timeout=0)
        try:
            os.unlink(self.path)
            second.acquire(timeout=0)
            self.assertTrue(second.locked)
            second.release()
        finally:
            first.release()

    def test_lock_is_per_open_description(self):
        # Two descriptors opened from the same path contend, which is the
        # property the timeout tests rely on.
        first = FileLock(self.path, clock=FakeClock(step=1.0))
        second = FileLock(self.path, clock=FakeClock(step=1.0))
        first.acquire(timeout=0)
        try:
            with self.assertRaises(LockTimeout):
                second.acquire(timeout=0)
        finally:
            first.release()

    def test_acquire_after_release_reuses_instance(self):
        lock = FileLock(self.path, clock=FakeClock(step=1.0))
        lock.acquire(timeout=0)
        lock.release()
        lock.acquire(timeout=0)
        self.assertTrue(lock.locked)
        lock.release()
        self.assertFalse(lock.locked)


if __name__ == "__main__":
    unittest.main()
