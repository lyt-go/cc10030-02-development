"""空间关联领域服务：将两套空间数据集按距离容差执行关联匹配，生成空间关联结果数据集。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SpatialJoinService(BaseModuleService):
    MODULE = "spatial_join"
    ENTITY = "关联任务"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["任务编号", "左数据集", "右数据集", "关联方式", "距离容差", "匹配条数", "未匹配条数", "关联版本"]
    REQUIRED_FIELDS = ["任务编号", "左数据集", "右数据集"]
    STATUS_ORDER = ["草稿", "待关联", "关联中", "已完成"]
    ACTION_RULES = {"启动关联": "关联中", "标记完成": "已完成", "标记失败": "已完成"}
    NEGATIVE_ACTIONS = []

    def join_trajectories_to_grids(self, batch_key: str, distance_tolerance: float = 500.0) -> dict[str, Any]:
        """将轨迹分段数据集与网格索引数据集按距离容差执行空间关联，生成轨迹-网格关联数据集。"""
        # 读取轨迹分段
        try:
            seg_svc = self._safe_import("app.services.track_segment", "TrackSegmentService")()
            segments, _ = seg_svc.get_segments(batch_key)
        except Exception:
            seg_file = self._data_dir("track_segments") / f"{batch_key}_segments.jsonl"
            if not seg_file.exists():
                raise FileNotFoundError(f"批次 {batch_key} 的轨迹分段数据不存在")
            segments = self._read_jsonl(seg_file)

        # 读取网格索引
        try:
            grid_svc = self._safe_import("app.services.grid_index", "GridIndexService")()
            grids, _ = grid_svc.get_grid_index(batch_key)
        except Exception:
            grids_file = self._data_dir("grid_index") / f"{batch_key}_grid.jsonl"
            if not grids_file.exists():
                raise FileNotFoundError(f"批次 {batch_key} 的网格索引数据不存在")
            grids = self._read_jsonl(grids_file)

        if not segments or not grids:
            return {"batch_key": batch_key, "matches": 0, "unmatched": len(segments)}

        matches = []
        unmatched = 0
        for seg in segments:
            seg_lat = (seg["start_lat"] + seg["end_lat"]) / 2
            seg_lon = (seg["start_lon"] + seg["end_lon"]) / 2
            best_grid = None
            best_dist = float("inf")
            for g in grids:
                dist = self._haversine(seg_lat, seg_lon, g["center_lat"], g["center_lon"])
                if dist < best_dist:
                    best_dist = dist
                    best_grid = g
            if best_dist <= distance_tolerance:
                matches.append({
                    "segment_id": seg["segment_id"],
                    "batch_key": batch_key,
                    "grid_geohash": best_grid["geohash"],
                    "grid_id": best_grid["grid_id"],
                    "seg_center_lat": seg_lat,
                    "seg_center_lon": seg_lon,
                    "grid_center_lat": best_grid["center_lat"],
                    "grid_center_lon": best_grid["center_lon"],
                    "distance": round(best_dist, 2),
                    "tolerance": distance_tolerance,
                    "matched_at": time.time(),
                })
            else:
                unmatched += 1

        output_file = self._data_dir("spatial_join") / f"{batch_key}_join.jsonl"
        written = self._write_jsonl(output_file, matches)

        # 创建关联记录
        entry, _ = self.create_entry({
            "任务编号": f"JOIN-{batch_key}",
            "左数据集": f"track_segments/{batch_key}",
            "右数据集": f"grid_index/{batch_key}",
            "关联方式": "distance_spatial_join",
            "距离容差": f"{distance_tolerance}m",
            "匹配条数": str(written),
            "未匹配条数": str(unmatched),
            "关联版本": "v1.0",
        })
        if entry:
            self.run_action(entry["id"], "标记完成")

        return {
            "batch_key": batch_key,
            "total_segments": len(segments),
            "total_grids": len(grids),
            "matches": written,
            "unmatched": unmatched,
            "output_file": str(output_file),
        }

    def get_join_results(self, batch_key: str) -> tuple[list[dict[str, Any]], str]:
        """读取批次的空间关联结果数据集文件。"""
        file_path = self._data_dir("spatial_join") / f"{batch_key}_join.jsonl"
        data = self._read_jsonl(file_path)
        return data, str(file_path)