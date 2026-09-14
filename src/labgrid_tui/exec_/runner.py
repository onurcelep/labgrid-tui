"""Run labgrid-client command lines.

capture mode is what the OSS runner uses; run_suspended remains available
for downstream ActionRunner implementations that want suspend-exec."""

import asyncio
import shlex
import shutil
import subprocess
from collections.abc import Callable


def client_available() -> bool:
    return shutil.which("labgrid-client") is not None


async def run_capture(command_line: str, on_line: Callable[[str], None]) -> int:
    argv = shlex.split(command_line)
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        on_line(f"{argv[0]}: not found on PATH")
        return 127
    assert process.stdout is not None
    async for raw in process.stdout:
        on_line(raw.decode(errors="replace").rstrip("\n"))
    return await process.wait()


def run_suspended(command_line: str) -> int:
    """Blocking exec attached to the real terminal.

    Caller must hold app.suspend() so the terminal belongs to the child.
    """
    try:
        return subprocess.call(shlex.split(command_line))
    except FileNotFoundError:
        return 127
