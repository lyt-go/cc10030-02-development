"""起止流量领域服务：计算起点网格到终点网格的出行流量矩阵，统计出行次数和平均时长。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class OdMatrixService(BaseModuleService):
    MODULE = "od_matrix"
    ENTITY = "流量单元"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["单元编号", "起点网格", "终点网格", "时段", "出行次数", "平均时长", "出行方式", "矩阵版本"]
    REQUIRED_FIELDS = ["单元编号", "起点网格", "终点网格"]
    STATUS_ORDER = ["草稿", "待计算", "已计算", "已过期"]
    ACTION_RULES = {"启动计算": "待计算", "确认计算": "已计算", "标记过期": "已过期"}
    NEGATIVE_ACTIONS = []

    def compute_od_from_trajectories(self, batch_key: str, time_bucket_sec: int = 3600) -> dict[str, Any]:
        """读取轨迹分段数据集，计算起点网格到终点网格的出行流量矩阵，生成OD流量数据集。"""
        # 读取轨迹分段
        try:
            seg_svc = self._safe_import("app.services.track_segment", "TrackSegmentService")()
            segments, _ = seg_svc.get_segments(batch_key)
        except Exception:
            seg_file = self._data_dir("track_segments") / f"{batch_key}_segments.jsonl"
            if not seg_file.exists():
                raise FileNotFoundError(f"批次 {batch_key} 的轨迹分段数据不存在，请先执行轨迹切分")
            segments = self._read_jsonl(seg_file)

        if not segments:
            return {"batch_key": batch_key, "od_pairs": 0, "segments": 0}

        # 按起点网格和终点网格聚合
        od_map: dict[str, dict[str, Any]] = {}
        for seg in segments:
            origin_gh = self._geohash_encode(seg["start_lat"], seg["start_lon"], 6)
            dest_gh = self._geohash_encode(seg["end_lat"], seg["end_lon"], 6)
            ts = seg.get("start_time", 0)
            time_bucket = int(ts // time_bucket_sec) * time_bucket_sec
            key = f"{origin_gh}_{dest_gh}_{time_bucket}"

            if key not in od_map:
                od_map[key] = {
                    "origin_geohash": origin_gh,
                    "dest_geohash": dest_gh,
                    "time_bucket": time_bucket,
                    "trip_count": 0,
                    "total_duration": 0.0,
                    "segments": [],
                }
            od_map[key]["trip_count"] += 1
            od_map[key]["total_duration"] += abs(seg.get("end_time", 0) - seg.get("start_time", 0))
            od_map[key]["segments"].append(seg["segment_id"])

        od_pairs = []
        unit_id = 0
        for key, info in od_map.items():
            unit_id += 1
            avg_duration = info["total_duration"] / max(info["trip_count"], 1)
            travel_mode = "walk" if avg_duration < 600 else ("bike" if avg_duration < 1800 else "vehicle")
            od_pairs.append({
                "unit_id": unit_id,
                "batch_key": batch_key,
                "origin_geohash": info["origin_geohash"],
                "dest_geohash": info["dest_geohash"],
                "time_bucket": info["time_bucket"],
                "trip_count": info["trip_count"],
                "avg_duration": round(avg_duration, 1),
                "travel_mode": travel_mode,
            })

        output_file = self._data_dir("od_matrix") / f"{batch_key}_od.jsonl"
        written = self._write_jsonl(output_file, od_pairs)

        # 创建OD矩阵记录
        for od in od_pairs:
            entry, _ = self.create_entry({
                "单元编号": f"OD-{batch_key}-{od['unit_id']:03d}",
                "起点网格": od["origin_geohash"],
                "终点网格": od["dest_geohash"],
                "时段": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(od["time_bucket"])),
                "出行次数": str(od["trip_count"]),
                "平均时长": f"{od['avg_duration']}s",
                "出行方式": od["travel_mode"],
                "矩阵版本": "v1.0",
            })
            if entry:
                self.run_action(entry["id"], "确认计算")

        return {
            "batch_key": batch_key,
            "segments_processed": len(segments),
            "od_pairs": written,
            "output_file": str(output_file),
        }

    def get_od_matrix(self, batch_key: str) -> tuple[list[dict[str, Any]], str]:
        """读取批次的OD流量矩阵数据集文件。"""
        file_path = self._data_dir("od_matrix") / f"{batch_key}_od.jsonl"
        data = self._read_jsonl(file_path)
        return data, str(file_path)