"""子进程封装：超时 treeKill（Windows taskkill /T）、输出落盘、编码容错。"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# 中文 Windows 控制台常见 GBK 输出，逐序尝试解码
_CODECS = ("utf-8", "gbk", "cp437")


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def _decode(data: bytes) -> str:
    for codec in _CODECS:
        try:
            return data.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def _tree_kill(proc: subprocess.Popen) -> None:
    """Windows: taskkill /T /F 杀整棵进程树；其它平台 SIGKILL。"""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        proc.kill()


def run_command(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: float = 300,
    env: dict | None = None,
    output_file: Path | None = None,
    shell: bool = False,
) -> ProcResult:
    """执行命令，超时杀进程树，stdout/stderr 可选落盘。

    Windows shell 模式用 cmd /d /c：/d 禁用 AutoRun（本机 AutoRun 被 conda 配置
    污染导致所有命令退出码 1）。
    """
    import sys

    if shell and sys.platform == "win32" and len(cmd) == 1 and isinstance(cmd[0], str):
        cmd = ["cmd", "/d", "/c", cmd[0]]
        shell = False
    start = time.monotonic()
    logger.debug(f"exec: {cmd} (cwd={cwd}, timeout={timeout})")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            shell=shell,
        )
    except FileNotFoundError as e:
        return ProcResult(returncode=127, stdout="", stderr=str(e), timed_out=False, duration=0.0)

    try:
        out_b, err_b = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _tree_kill(proc)
        out_b, err_b = proc.communicate()
        timed_out = True

    stdout, stderr = _decode(out_b), _decode(err_b)
    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(stdout, encoding="utf-8", errors="replace")
        if stderr:
            output_file.with_suffix(".stderr").write_text(stderr, encoding="utf-8", errors="replace")
    return ProcResult(
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        duration=round(time.monotonic() - start, 2),
    )
