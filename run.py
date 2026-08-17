"""入口：run.py --once 单轮同步；默认启动常驻（调度器 + 飞书通道[接入后]）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config.settings import load_settings
from app.main import App
from app.utils.log import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="EvoScout 多 AI 研究助理中枢")
    parser.add_argument("--once", action="store_true", help="单轮同步后退出")
    parser.add_argument("--supervise", action="store_true",
                        help="崩溃自动重启（配合任务计划做常驻）")
    args = parser.parse_args()

    settings = load_settings()
    setup_logging(settings.data_dir)

    if args.supervise:
        _supervise(settings, once=False)
        return
    if args.once:
        app = App(settings).setup()
        app.sync_once()
        app.stop()
        return

    app = App(settings).setup()
    app.start()
    print("EvoScout 运行中，Ctrl+C 退出")
    import threading
    try:
        threading.Event().wait()   # Windows 无 signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()


def _supervise(settings, once: bool) -> None:
    """崩溃自愈循环：App 异常退出后 10s 重启（Ctrl+C 才真正退出）。"""
    import time

    from loguru import logger

    backoff = 5
    while True:
        app = None
        try:
            app = App(settings).setup()
            if once:
                app.sync_once()
                return
            app.start()
            logger.info(f"supervisor: App 已启动（PID {__import__('os').getpid()}）")
            import threading
            threading.Event().wait()
            return   # 正常退出（Ctrl+C）
        except KeyboardInterrupt:
            return
        except Exception:
            logger.exception(f"App 崩溃，{backoff}s 后重启")
            try:
                if app:
                    app.stop()
            except Exception:
                pass
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


if __name__ == "__main__":
    main()
