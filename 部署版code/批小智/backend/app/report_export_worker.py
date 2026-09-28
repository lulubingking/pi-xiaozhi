"""报告 PDF / Word 导出 Worker。

报告接口只负责冻结快照并入队；本 Worker 负责从 ``report_exports`` 队列领取
任务、生成文件、原子替换到配置的存储根目录，并把失败原因持久化。首版按
SQLite 单 Worker 运行，避免在不支持行锁的情况下启动多个领取者。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

from sqlalchemy import select

# 支持从项目根目录执行 ``python backend/app/report_export_worker.py``。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.core.utils import utc_now
from app.db.session import SessionLocal
from app.models import ReportExport
from app.report_exporters import build_report_file


settings = get_settings()


def _storage_root() -> Path:
    root = Path(settings.storage_root)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path_for_key(storage_key: str) -> Path:
    root = _storage_root()
    candidate = (root / storage_key).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("报告文件存储路径不在配置的存储根目录内")
    return candidate


def _claim_export(worker_id: str) -> ReportExport | None:
    db = SessionLocal()
    try:
        export = db.scalar(
            select(ReportExport)
            .where(ReportExport.status == "queued")
            .order_by(ReportExport.created_at, ReportExport.id)
            .with_for_update()
        )
        if export is None:
            db.rollback()
            return None
        export.status = "running"
        export.failure_code = None
        db.commit()
        return export
    finally:
        db.close()


def _process_export(export_id: str, worker_id: str) -> None:
    db = SessionLocal()
    temp_path: Path | None = None
    try:
        export = db.get(ReportExport, export_id)
        if export is None:
            return
        try:
            snapshot = json.loads(export.scope_json)
            if not isinstance(snapshot, dict):
                raise ValueError("导出快照不是 JSON 对象")
            storage_key = f"reports/{export.id}.{export.format}"
            output_path = _path_for_key(storage_key)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
            build_report_file(snapshot, temp_path, export.format)
            if not temp_path.is_file() or temp_path.stat().st_size <= 0:
                raise ValueError("导出器未生成有效文件")
            os.replace(temp_path, output_path)
            export.status = "succeeded"
            export.storage_key = storage_key
            export.failure_code = None
            export.finished_at = utc_now()
            db.commit()
        except Exception as exc:  # noqa: BLE001 - Worker 必须持久化未知生成失败
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            export.status = "failed"
            export.storage_key = None
            export.failure_code = f"EXPORT_GENERATION_FAILED:{type(exc).__name__}"
            export.finished_at = utc_now()
            db.commit()
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="领取并处理一个 queued 导出任务后退出")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="轮询间隔（秒），默认 2")
    args = parser.parse_args()
    worker_id = os.getenv("REPORT_EXPORT_WORKER_ID") or f"report-export-worker:{socket.gethostname()}:{os.getpid()}"

    while True:
        export = _claim_export(worker_id)
        if export is None:
            if args.once:
                return
            time.sleep(max(0.2, args.poll_interval))
            continue
        _process_export(export.id, worker_id)
        if args.once:
            return


if __name__ == "__main__":
    main()
