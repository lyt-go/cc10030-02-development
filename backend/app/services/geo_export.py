"""结果导出领域服务：将空间分析结果按区域和字段范围进行分片导出，生成导出任务数据集。"""
from __future__ import annotations

import json
import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class GeoExportService(BaseModuleService):
    MODULE = "geo_export"
    ENTITY = "导出任务"
    OPERATION_TERM = "导出"
    LIST_FIELDS = ["导出编号", "快照编号", "导出格式", "区域范围", "字段范围", "记录条数", "分片大小", "导出水位"]
    REQUIRED_FIELDS = ["导出编号", "快照编号", "导出格式"]
    STATUS_ORDER = ["草稿", "待导出", "导出中", "已交付"]
    ACTION_RULES = {"启动导出": "导出中", "确认交付": "已交付", "标记失败": "已交付"}
    NEGATIVE_ACTIONS = []

    def export_batch_results(self, batch_key: str, export_format: str = "jsonl",
                              shard_size: int = 100) -> dict[str, Any]:
        """读取分析快照数据集，按指定格式将热力、OD矩阵和区域数据分片导出，生成导出结果文件。"""
        results = []
        total_records = 0

        # 导出热力数据
        heatmap_file = self._data_dir("heatmap") / f"{batch_key}_heatmap.jsonl"
        if heatmap_file.exists():
            heatmap_data = self._read_jsonl(heatmap_file)
            results.extend(heatmap_data)
            total_records += len(heatmap_data)

        # 导出OD矩阵数据
        od_file = self._data_dir("od_matrix") / f"{batch_key}_od.jsonl"
        if od_file.exists():
            od_data = self._read_jsonl(od_file)
            results.extend(od_data)
            total_records += len(od_data)

        # 导出区域聚合数据
        region_file = self._data_dir("region_aggregates") / f"{batch_key}_region.jsonl"
        if region_file.exists():
            region_data = self._read_jsonl(region_file)
            results.extend(region_data)
            total_records += len(region_data)

        if not results:
            return {"batch_key": batch_key, "exported": 0, "total": 0}

        # 分片导出
        shards = []
        for i in range(0, len(results), shard_size):
            shard = results[i:i + shard_size]
            shard_file = self._data_dir("exports") / f"{batch_key}_shard_{i // shard_size + 1:03d}.{export_format}"
            self._write_jsonl(shard_file, shard)
            shards.append(str(shard_file))

        # 写入导出汇总数据集
        export_summary = {
            "batch_key": batch_key,
            "export_format": export_format,
            "total_records": total_records,
            "shards": len(shards),
            "shard_size": shard_size,
            "exported_at": time.time(),
        }
        summary_file = self._data_dir("exports") / f"{batch_key}_export_summary.jsonl"
        self._write_jsonl(summary_file, [export_summary])

        # 创建导出记录
        entry, _ = self.create_entry({
            "导出编号": f"EXPORT-{batch_key}",
            "快照编号": f"SNAP-{batch_key}",
            "导出格式": export_format,
            "区域范围": "all",
            "字段范围": "all",
            "记录条数": str(total_records),
            "分片大小": str(shard_size),
            "导出水位": f"已导出{total_records}条/{len(shards)}分片",
        })
        if entry:
            self.run_action(entry["id"], "确认交付")

        return {
            "batch_key": batch_key,
            "exported_records": total_records,
            "shards": len(shards),
            "format": export_format,
            "shard_files": shards,
        }

    def get_export_summary(self, batch_key: str) -> dict[str, Any]:
        """读取批次的导出任务汇总数据集。"""
        file_path = self._data_dir("exports") / f"{batch_key}_export_summary.jsonl"
        summaries = self._read_jsonl(file_path)
        return summaries[0] if summaries else {}