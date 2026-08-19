from __future__ import annotations

import errno
import msvcrt
import os
from pathlib import Path
from typing import BinaryIO


class AlreadyRunning(RuntimeError):
    pass


class StartupLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._file: BinaryIO | None = None

    def __enter__(self) -> "StartupLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def acquire(self) -> None:
        if self._file is not None:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            handle.close()
            if error.errno in {errno.EACCES, errno.EDEADLK}:
                raise AlreadyRunning(f"lock is already held: {self.path}") from error
            raise

        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        self._file = handle

    def release(self) -> None:
        if self._file is None:
            return

        handle = self._file
        self._file = None
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()
