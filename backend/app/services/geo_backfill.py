"""历史回补领域服务：对迟到定位数据按日期区间执行历史数据回补，将回补数据归入已有批次数据集。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class GeoBackfillService(BaseModuleService):
    MODULE = "geo_backfill"
    ENTITY = "回补任务"
    OPERATION_TERM = "回填"
    LIST_FIELDS = ["回补编号", "目标数据集", "起始日期", "截止日期", "回补范围", "并行度", "覆盖方式", "回补水位"]
    REQUIRED_FIELDS = ["回补编号", "目标数据集", "起始日期"]
    STATUS_ORDER = ["草稿", "待回补", "回补中", "已完成", "已取消"]
    ACTION_RULES = {"启动回补": "回补中", "标记完成": "已完成", "取消回补": "已取消"}
    NEGATIVE_ACTIONS = []

    def backfill_late_data(self, batch_key: str, late_point_count: int = 15,
                            start_date: str = "2026-09-01",
                            end_date: str = "2026-09-07") -> dict[str, Any]:
        """为目标采集批次生成迟到数据并追加回补到已有定位点数据集文件中。"""
        points_file = self._data_dir("geo_points") / f"{batch_key}.jsonl"
        if not points_file.exists():
            raise FileNotFoundError(f"批次 {batch_key} 的定位点数据文件不存在")

        existing = self._read_jsonl(points_file)
        existing_count = len(existing)

        # 生成迟到数据
        late_points = self._generate_sample_points(count=late_point_count)
        for p in late_points:
            p["batch_key"] = batch_key
            p["is_backfill"] = True
            p["backfill_date_range"] = f"{start_date}~{end_date}"

        # 追加写入
        written = self._write_jsonl(points_file, late_points)

        # 记录回补水位
        self._write_checkpoint(f"backfill_{batch_key}", {
            "batch_key": batch_key,
            "existing_count": existing_count,
            "backfill_count": written,
            "total_after_backfill": existing_count + written,
            "start_date": start_date,
            "end_date": end_date,
            "backfilled_at": time.time(),
        })

        # 创建回补记录
        entry, _ = self.create_entry({
            "回补编号": f"BF-{batch_key}",
            "目标数据集": batch_key,
            "起始日期": start_date,
            "截止日期": end_date,
            "回补范围": f"{start_date}~{end_date}",
            "并行度": "1",
            "覆盖方式": "append",
            "回补水位": f"已回补{written}/{existing_count+written}条",
        })
        if entry:
            self.run_action(entry["id"], "标记完成")

        return {
            "batch_key": batch_key,
            "existing_points": existing_count,
            "backfill_points": written,
            "total_after": existing_count + written,
            "date_range": f"{start_date}~{end_date}",
        }

    def verify_backfill_complete(self, batch_key: str) -> dict[str, Any]:
        """校验指定批次的历史回补完成状态，读取数据文件和检查点返回补全情况。"""
        points_file = self._data_dir("geo_points") / f"{batch_key}.jsonl"
        current_count = len(self._read_jsonl(points_file)) if points_file.exists() else 0
        cp = self._read_checkpoint(f"backfill_{batch_key}")
        return {
            "batch_key": batch_key,
            "current_total": current_count,
            "original_count": cp.get("existing_count", 0) if cp else 0,
            "backfill_count": cp.get("backfill_count", 0) if cp else 0,
            "is_complete": current_count == (cp.get("total_after_backfill", 0)) if cp else False,
        }