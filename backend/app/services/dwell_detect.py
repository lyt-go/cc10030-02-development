"""驻留识别领域服务：在空间半径内检测轨迹驻留事件，计算停留时长并生成驻留事件数据集。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DwellDetectService(BaseModuleService):
    MODULE = "dwell_detect"
    ENTITY = "驻留事件"
    OPERATION_TERM = "识别"
    LIST_FIELDS = ["事件编号", "分段编号", "中心坐标", "半径", "停留时长", "点数", "判定阈值", "事件类型"]
    REQUIRED_FIELDS = ["事件编号", "分段编号", "中心坐标"]
    STATUS_ORDER = ["草稿", "待识别", "已识别", "已驳回"]
    ACTION_RULES = {"启动识别": "待识别", "确认识别": "已识别", "标记驳回": "已驳回"}
    NEGATIVE_ACTIONS = []

    def detect_from_segments(self, batch_key: str, radius: float = 100.0,
                              min_dwell_sec: float = 120.0) -> dict[str, Any]:
        """读取轨迹分段数据集文件，在指定半径内检测驻留事件，生成驻留事件数据集。"""
        try:
            seg_svc = self._safe_import("app.services.track_segment", "TrackSegmentService")()
            segments, _ = seg_svc.get_segments(batch_key)
        except FileNotFoundError:
            raise FileNotFoundError(f"批次 {batch_key} 的轨迹分段数据不存在，请先执行轨迹切分")

        events = []
        event_id = 0

        for seg in segments:
            # 读取该分段对应的坐标转换定位点
            points_file = self._data_dir("coord_transformed") / f"{batch_key}_transformed.jsonl"
            if not points_file.exists():
                continue
            points = self._read_jsonl(points_file)
            if not points:
                continue

            # 检测驻留：连续定位点在同一半径范围内且时间跨度大于阈值
            dwell_clusters = self._cluster_dwell_points(points, radius, min_dwell_sec)
            for cluster in dwell_clusters:
                if cluster["duration"] >= min_dwell_sec and cluster["count"] >= 2:
                    event_id += 1
                    center_lat = sum(p["trans_lat"] for p in cluster["points"]) / len(cluster["points"])
                    center_lon = sum(p["trans_lon"] for p in cluster["points"]) / len(cluster["points"])
                    event_type = "long_stay" if cluster["duration"] >= 600 else "short_stay"
                    events.append({
                        "event_id": event_id,
                        "batch_key": batch_key,
                        "segment_id": seg["segment_id"],
                        "center_lat": round(center_lat, 6),
                        "center_lon": round(center_lon, 6),
                        "radius": radius,
                        "dwell_duration": round(cluster["duration"], 1),
                        "point_count": cluster["count"],
                        "threshold": min_dwell_sec,
                        "event_type": event_type,
                        "start_time": cluster["start_time"],
                        "end_time": cluster["end_time"],
                    })

        # 写入驻留事件数据集
        output_file = self._data_dir("dwell_events") / f"{batch_key}_dwell.jsonl"
        written = self._write_jsonl(output_file, events)

        # 自动在内存中创建事件记录
        for evt in events:
            entry, _ = self.create_entry({
                "事件编号": f"DWEL-{batch_key}-{evt['event_id']:03d}",
                "分段编号": str(evt["segment_id"]),
                "中心坐标": f"{evt['center_lat']},{evt['center_lon']}",
                "半径": f"{radius}m",
                "停留时长": f"{evt['dwell_duration']}s",
                "点数": str(evt["point_count"]),
                "判定阈值": f"{min_dwell_sec}s",
                "事件类型": evt["event_type"],
            })
            if entry:
                self.run_action(entry["id"], "确认识别")

        return {
            "batch_key": batch_key,
            "segments_processed": len(segments),
            "events_detected": written,
            "output_file": str(output_file),
        }

    def _cluster_dwell_points(self, points: list[dict[str, Any]], radius: float,
                               min_dwell_sec: float) -> list[dict[str, Any]]:
        """在定位点序列中聚合驻留簇，识别连续在半径范围内的点集并计算停留时长。"""
        clusters = []
        i = 0
        while i < len(points):
            cluster_points = [points[i]]
            j = i + 1
            while j < len(points):
                in_radius = True
                for cp in cluster_points:
                    dist = self._haversine(cp["trans_lat"], cp["trans_lon"],
                                          points[j]["trans_lat"], points[j]["trans_lon"])
                    if dist > radius:
                        in_radius = False
                        break
                if in_radius:
                    cluster_points.append(points[j])
                    j += 1
                else:
                    break
            if len(cluster_points) >= 2:
                duration = abs(cluster_points[-1]["timestamp"] - cluster_points[0]["timestamp"])
                clusters.append({
                    "points": cluster_points,
                    "count": len(cluster_points),
                    "start_time": cluster_points[0]["timestamp"],
                    "end_time": cluster_points[-1]["timestamp"],
                    "duration": duration,
                })
            i = j
        return clusters

    def get_dwell_events(self, batch_key: str) -> tuple[list[dict[str, Any]], str]:
        """读取指定批次的驻留事件数据集文件。"""
        file_path = self._data_dir("dwell_events") / f"{batch_key}_dwell.jsonl"
        if not file_path.exists():
            raise FileNotFoundError(f"批次 {batch_key} 的驻留事件数据集不存在")
        events = self._read_jsonl(file_path)
        return events, str(file_path)