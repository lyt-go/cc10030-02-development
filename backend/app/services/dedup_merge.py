"""点位归并领域服务：按去重键、时间容差和距离容差对重复定位点执行归并去重，生成归并后的数据集。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DedupMergeService(BaseModuleService):
    MODULE = "dedup_merge"
    ENTITY = "归并批次"
    OPERATION_TERM = "去重"
    LIST_FIELDS = ["归并编号", "批次编号", "去重键列", "时间容差", "距离容差", "原始条数", "归并条数", "归并水位"]
    REQUIRED_FIELDS = ["归并编号", "批次编号", "去重键列"]
    STATUS_ORDER = ["草稿", "待归并", "已归并", "已回滚"]
    ACTION_RULES = {"启动归并": "待归并", "确认归并": "已归并", "执行回滚": "已回滚"}
    NEGATIVE_ACTIONS = []

    def dedup_batch(self, batch_key: str, time_tolerance: float = 60.0,
                     dist_tolerance: float = 10.0) -> dict[str, Any]:
        """读取采集批次定位点数据集文件，按时间和距离容差执行去重归并，生成归并后的定位点数据集。"""
        points_file = self._data_dir("geo_points") / f"{batch_key}.jsonl"
        if not points_file.exists():
            raise FileNotFoundError(f"批次 {batch_key} 的定位点数据文件不存在")

        points = self._read_jsonl(points_file)
        if not points:
            return {"batch_key": batch_key, "original": 0, "merged": 0}

        original_count = len(points)
        # 按终端和时间排序
        points.sort(key=lambda p: (p.get("terminal", ""), p.get("timestamp", 0)))

        merged = []
        dedup_key_fn = lambda p: p.get("terminal", "")
        for i, p in enumerate(points):
            is_dup = False
            for m in reversed(merged[-10:]):  # 只看最近10条提高性能
                if dedup_key_fn(p) != dedup_key_fn(m):
                    continue
                time_diff = abs(p.get("timestamp", 0) - m.get("timestamp", 0))
                dist = self._haversine(
                    p.get("lat", 0), p.get("lon", 0),
                    m.get("lat", 0), m.get("lon", 0)
                )
                if time_diff <= time_tolerance and dist <= dist_tolerance:
                    is_dup = True
                    break
            if not is_dup:
                merged.append({
                    "point_id": len(merged) + 1,
                    "orig_point_ids": [p["point_id"]],
                    "lat": p["lat"],
                    "lon": p["lon"],
                    "timestamp": p["timestamp"],
                    "terminal": p.get("terminal"),
                    "accuracy": p.get("accuracy"),
                    "batch_key": batch_key,
                    "dedup_time_tolerance": time_tolerance,
                    "dedup_dist_tolerance": dist_tolerance,
                })

        output_file = self._data_dir("dedup_merged") / f"{batch_key}_merged.jsonl"
        written = self._write_jsonl(output_file, merged)

        # 创建归并记录
        entry, _ = self.create_entry({
            "归并编号": f"DEDUP-{batch_key}", "批次编号": batch_key,
            "去重键列": "terminal,timestamp",
            "时间容差": f"{time_tolerance}s",
            "距离容差": f"{dist_tolerance}m",
            "原始条数": str(original_count),
            "归并条数": str(written),
            "归并水位": f"已归并{written}/{original_count}条",
        })
        if entry:
            self.run_action(entry["id"], "确认归并")

        return {
            "batch_key": batch_key,
            "original_count": original_count,
            "merged_count": written,
            "removed_duplicates": original_count - written,
            "output_file": str(output_file),
        }

    def get_merged_points(self, batch_key: str) -> tuple[list[dict[str, Any]], str]:
        """读取指定批次的去重归并后的定位点数据集文件。"""
        file_path = self._data_dir("dedup_merged") / f"{batch_key}_merged.jsonl"
        if not file_path.exists():
            raise FileNotFoundError(f"批次 {batch_key} 的归并数据集不存在")
        data = self._read_jsonl(file_path)
        return data, str(file_path)