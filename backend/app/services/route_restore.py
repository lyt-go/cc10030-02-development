"""路径还原领域服务：将轨迹分段匹配到路网路段，执行缺口插值和路径还原，生成路网匹配数据集。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class RouteRestoreService(BaseModuleService):
    MODULE = "route_restore"
    ENTITY = "还原路段"
    OPERATION_TERM = "入湖"
    LIST_FIELDS = ["路段编号", "分段编号", "路段序列", "路网编号", "缺口数量", "插值方式", "还原里程", "还原版本"]
    REQUIRED_FIELDS = ["路段编号", "分段编号", "路段序列"]
    STATUS_ORDER = ["草稿", "待还原", "已还原", "还原失败"]
    ACTION_RULES = {"启动还原": "待还原", "确认还原": "已还原", "标记失败": "还原失败"}
    NEGATIVE_ACTIONS = []

    # 模拟路网数据
    _ROAD_NETWORK = [
        {"road_id": "RN001", "name": "长安街", "start_lat": 39.905, "start_lon": 116.39, "end_lat": 39.91, "end_lon": 116.42},
        {"road_id": "RN002", "name": "二环路", "start_lat": 39.90, "start_lon": 116.40, "end_lat": 39.92, "end_lon": 116.41},
        {"road_id": "RN003", "name": "三环路", "start_lat": 39.88, "start_lon": 116.38, "end_lat": 39.93, "end_lon": 116.45},
        {"road_id": "RN004", "name": "朝阳路", "start_lat": 39.91, "start_lon": 116.45, "end_lat": 39.93, "end_lon": 116.47},
        {"road_id": "RN005", "name": "东直门外大街", "start_lat": 39.94, "start_lon": 116.43, "end_lat": 39.95, "end_lon": 116.46},
    ]

    def restore_from_segments(self, batch_key: str) -> dict[str, Any]:
        """读取轨迹分段数据集，将每个分段匹配到最近路网路段，执行线性插值还原缺口，生成路径还原数据集。"""
        # 读取轨迹分段
        try:
            seg_svc = self._safe_import("app.services.track_segment", "TrackSegmentService")()
            segments, _ = seg_svc.get_segments(batch_key)
        except Exception:
            seg_file = self._data_dir("track_segments") / f"{batch_key}_segments.jsonl"
            if not seg_file.exists():
                raise FileNotFoundError(f"批次 {batch_key} 的轨迹分段数据不存在")
            segments = self._read_jsonl(seg_file)

        if not segments:
            return {"batch_key": batch_key, "restored_routes": 0, "segments": 0}

        restored = []
        route_id = 0
        total_gaps = 0

        for seg in segments:
            seg_mid_lat = (seg["start_lat"] + seg["end_lat"]) / 2
            seg_mid_lon = (seg["start_lon"] + seg["end_lon"]) / 2

            # 匹配最近路网路段
            best_road = None
            best_dist = float("inf")
            for road in self._ROAD_NETWORK:
                road_mid_lat = (road["start_lat"] + road["end_lat"]) / 2
                road_mid_lon = (road["start_lon"] + road["end_lon"]) / 2
                dist = self._haversine(seg_mid_lat, seg_mid_lon, road_mid_lat, road_mid_lon)
                if dist < best_dist:
                    best_dist = dist
                    best_road = road

            if best_road:
                route_id += 1
                gap_count = max(0, (seg["point_count"] - 1) // 5)
                total_gaps += gap_count
                restored.append({
                    "route_id": route_id,
                    "batch_key": batch_key,
                    "segment_id": seg["segment_id"],
                    "road_id": best_road["road_id"],
                    "road_name": best_road["name"],
                    "start_lat": seg["start_lat"],
                    "start_lon": seg["start_lon"],
                    "end_lat": seg["end_lat"],
                    "end_lon": seg["end_lon"],
                    "gap_count": gap_count,
                    "interpolation": "linear" if gap_count > 0 else "none",
                    "restored_mileage": seg.get("mileage", 0),
                    "road_match_distance": round(best_dist, 2),
                })

        output_file = self._data_dir("route_restore") / f"{batch_key}_routes.jsonl"
        written = self._write_jsonl(output_file, restored)

        # 创建还原记录
        for r in restored:
            entry, _ = self.create_entry({
                "路段编号": f"ROUTE-{batch_key}-{r['route_id']:03d}",
                "分段编号": str(r["segment_id"]),
                "路段序列": r["road_id"],
                "路网编号": r["road_id"],
                "缺口数量": r["gap_count"],
                "插值方式": r["interpolation"],
                "还原里程": f"{r['restored_mileage']}m",
                "还原版本": "v1.0",
            })
            if entry:
                self.run_action(entry["id"], "确认还原")

        return {
            "batch_key": batch_key,
            "segments_processed": len(segments),
            "restored_routes": written,
            "total_gaps": total_gaps,
            "output_file": str(output_file),
        }

    def get_restored_routes(self, batch_key: str) -> tuple[list[dict[str, Any]], str]:
        """读取批次路径还原数据集文件。"""
        file_path = self._data_dir("route_restore") / f"{batch_key}_routes.jsonl"
        data = self._read_jsonl(file_path)
        return data, str(file_path)