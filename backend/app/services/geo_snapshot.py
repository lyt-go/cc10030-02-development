"""分析快照领域服务：对聚合分析结果创建版本化快照，记录分析口径、时间范围和结果条数。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class GeoSnapshotService(BaseModuleService):
    MODULE = "geo_snapshot"
    ENTITY = "版本快照"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["快照编号", "分析口径", "网格层级", "时间范围", "结果条数", "生成方式", "快照水位", "生成时间"]
    REQUIRED_FIELDS = ["快照编号", "分析口径", "网格层级"]
    STATUS_ORDER = ["草稿", "待确认", "已发布", "已归档"]
    ACTION_RULES = {"提交确认": "待确认", "发布快照": "已发布", "标记归档": "已归档"}
    NEGATIVE_ACTIONS = []

    def create_snapshot(self, batch_key: str, analysis_scope: str = "full",
                         grid_level: str = "L6", time_range: str = "24h") -> dict[str, Any]:
        """读取热力聚合和OD矩阵数据集，创建分析口径版本快照，生成快照聚合数据集文件。"""
        # 读取热力聚合数据
        heatmap_data = []
        heatmap_file = self._data_dir("heatmap") / f"{batch_key}_heatmap.jsonl"
        if heatmap_file.exists():
            heatmap_data = self._read_jsonl(heatmap_file)

        # 读取OD矩阵数据
        od_data = []
        od_file = self._data_dir("od_matrix") / f"{batch_key}_od.jsonl"
        if od_file.exists():
            od_data = self._read_jsonl(od_file)

        # 读取区域聚合
        region_data = []
        region_file = self._data_dir("region_aggregates") / f"{batch_key}_region.jsonl"
        if region_file.exists():
            region_data = self._read_jsonl(region_file)

        total_results = len(heatmap_data) + len(od_data) + len(region_data)

        snapshot = {
            "snapshot_id": f"SNAP-{batch_key}-{int(time.time())}",
            "batch_key": batch_key,
            "analysis_scope": analysis_scope,
            "grid_level": grid_level,
            "time_range": time_range,
            "heatmap_cells": len(heatmap_data),
            "od_pairs": len(od_data),
            "region_aggregates": len(region_data),
            "total_results": total_results,
            "peak_heatmap_grid": max((h["point_count"] for h in heatmap_data), default=0),
            "total_trips": sum(od.get("trip_count", 0) for od in od_data),
            "generated_at": time.time(),
            "version": "v1.0",
        }

        output_file = self._data_dir("snapshots") / f"{batch_key}_snapshot.jsonl"
        written = self._write_jsonl(output_file, [snapshot])

        # 创建快照记录
        entry, _ = self.create_entry({
            "快照编号": snapshot["snapshot_id"],
            "分析口径": analysis_scope,
            "网格层级": grid_level,
            "时间范围": time_range,
            "结果条数": str(total_results),
            "生成方式": "auto_aggregate",
            "快照水位": f"热力{len(heatmap_data)}_OD{len(od_data)}_区域{len(region_data)}",
            "生成时间": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time())),
        })
        if entry:
            self.run_action(entry["id"], "发布快照")

        return {
            "batch_key": batch_key,
            "snapshot_id": snapshot["snapshot_id"],
            "total_results": total_results,
            "heatmap_cells": len(heatmap_data),
            "od_pairs": len(od_data),
            "output_file": str(output_file),
        }

    def get_snapshot(self, batch_key: str) -> dict[str, Any]:
        """读取批次的分析快照数据集。"""
        file_path = self._data_dir("snapshots") / f"{batch_key}_snapshot.jsonl"
        snapshots = self._read_jsonl(file_path)
        return snapshots[0] if snapshots else {}