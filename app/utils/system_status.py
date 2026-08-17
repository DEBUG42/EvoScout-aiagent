"""系统状态采集：psutil + nvidia-smi（供 /status 命令与 master 的 system_status 工具共用）。"""
from __future__ import annotations

import platform
import time

import psutil

from app.core.subprocess import run_command


def get_system_status() -> str:
    boot = time.time() - psutil.boot_time()
    lines = [
        f"主机: {platform.node()} ({platform.platform()[:40]})",
        f"CPU: {psutil.cpu_percent(interval=1):.0f}%（{psutil.cpu_count()} 核）",
        f"内存: {psutil.virtual_memory().percent}%（{psutil.virtual_memory().used >> 30}G / {psutil.virtual_memory().total >> 30}G）",
    ]
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            lines.append(f"磁盘 {part.device}: {usage.percent}%（剩余 {usage.free >> 30}G）")
        except OSError:
            continue
    lines.append(f"开机时长: {int(boot // 3600)}h{int(boot % 3600 // 60)}m")
    gpu = run_command(
        ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
         "--format=csv,noheader"],
        timeout=10,
    )
    if gpu.ok and gpu.stdout.strip():
        lines.append("GPU: " + " | ".join(g.strip() for g in gpu.stdout.strip().splitlines()))
    return "\n".join(lines)
