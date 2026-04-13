"""Process adapter abstraction for CLI spawning."""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
from abc import ABC, abstractmethod


class ProcessAdapter(ABC):
    """Abstract interface for process control. OS-specific implementations below."""

    @abstractmethod
    async def spawn(self, command: str, args: list[str], cwd: str | None = None, env: dict[str, str] | None = None) -> None: ...

    @abstractmethod
    async def read(self) -> bytes: ...

    @abstractmethod
    async def write(self, data: bytes) -> None: ...

    @abstractmethod
    async def resize(self, rows: int, cols: int) -> None: ...

    @abstractmethod
    def kill(self) -> None: ...

    @abstractmethod
    def is_alive(self) -> bool: ...


class PtyAdapter(ProcessAdapter):
    """Unix pty-based process adapter."""

    def __init__(self) -> None:
        self._master_fd: int | None = None
        self._process: subprocess.Popen | None = None

    async def spawn(self, command: str, args: list[str], cwd: str | None = None, env: dict[str, str] | None = None) -> None:
        master_fd, slave_fd = pty.openpty()

        winsize = struct.pack("HHHH", 24, 80, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        spawn_env = os.environ.copy()
        spawn_env["TERM"] = "xterm-256color"
        if env:
            spawn_env.update(env)

        self._process = subprocess.Popen(
            [command, *args],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=spawn_env,
            preexec_fn=os.setsid,
        )
        os.close(slave_fd)
        self._master_fd = master_fd

        flag = fcntl.fcntl(self._master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self._master_fd, fcntl.F_SETFL, flag | os.O_NONBLOCK)

    async def read(self) -> bytes:
        if self._master_fd is None:
            return b""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._blocking_read)
        except OSError:
            return b""

    def _blocking_read(self) -> bytes:
        if self._master_fd is None:
            return b""
        try:
            return os.read(self._master_fd, 4096)
        except (OSError, BlockingIOError):
            return b""

    async def write(self, data: bytes) -> None:
        if self._master_fd is None:
            return
        os.write(self._master_fd, data)

    async def resize(self, rows: int, cols: int) -> None:
        if self._master_fd is None:
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)

    def kill(self) -> None:
        if self._process and self._process.poll() is None:
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    def is_alive(self) -> bool:
        if self._process is None:
            return False
        return self._process.poll() is None
