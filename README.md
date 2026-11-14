# file_lock_tools

An advisory exclusive lock backed by a file, released by the kernel even when
the process dies badly.

```python
from file_lock_tools import FileLock

with FileLock("/tmp/myapp.lock") as lock:
    # Only one process at a time reaches this block.
    print(lock.locked)  # True
```

## Why this exists

A common way to coordinate processes is to create a lock file with
`os.open(path, O_CREAT | O_EXCL)` and unlink it when done.  If the process is
killed between those two steps -- `SIGKILL`, a power cut, a container stop with
no grace period -- the file is left behind and every later run blocks forever.

This library uses `fcntl.flock` instead.  The lock is attached to the open file
description, so the kernel drops it automatically when the descriptor is closed,
including when the process dies.  The cost is that `flock` is advisory: a
process that never calls `flock` can still write to the file.  Every participant
must use `FileLock` (or call `flock` directly).  That trade-off is deliberate and
is the reason the library exists.

## API

- `FileLock(path, *, poll_interval=0.05, clock=time.monotonic)` -- construct a
  lock object.  Constructing it does not take the lock.  `path` may be a `str`
  or any object accepted by `os.fspath`.  `poll_interval` is the pause between
  retries and must be positive.  `clock` is a zero-argument callable returning
  seconds; it exists so tests can run without sleeping.
- `FileLock.acquire(timeout=None)` -- take the lock.  `timeout=None` waits
  forever, `timeout=0` makes a single non-blocking attempt, and a positive
  timeout bounds the wait.  Raises `LockTimeout` on failure, `LockError` if the
  instance already holds the lock, and `ValueError` for a negative timeout.
- `FileLock.release()` -- release the lock.  Safe to call when the lock is not
  held.
- `FileLock.locked` -- a `bool` property, true while this instance holds the
  lock.
- The context manager form (`with FileLock(...) as lock:`) acquires on entry and
  releases on exit, including when the body raises.
- `LockError` -- base class for errors from this module.
- `LockTimeout` -- subclass of `LockError`, raised when `acquire` gives up.

## The awkward edge

`flock` locks are per open file description, not per process.  Two `FileLock`
instances in the same process that each call `os.open` on the same path do
contend, and the second one will time out while the first holds the lock.  This
is usually what you want, but it surprises people who expect a process-wide
reentrant lock.  There is no reentrancy here: calling `acquire` twice on the
same instance raises `LockError` rather than succeeding.

Unlinking the lock file while a lock is held does not release the lock.  The
lock follows the open file description, so a second `FileLock` opened against
the same path still blocks until the first is released.
