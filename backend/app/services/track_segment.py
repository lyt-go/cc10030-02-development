"""轨迹切分领域服务：按时间间隔与距离阈值将定位点序列切分为轨迹分段数据集。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class TrackSegmentService(BaseModuleService):
    MODULE = "track_segment"
    ENTITY = "轨迹分段"
    OPERATION_TERM = "切分"
    LIST_FIELDS = ["分段编号", "终端编号", "起始时间", "结束时间", "点数", "间隔阈值", "里程", "切分策略"]
    REQUIRED_FIELDS = ["分段编号", "终端编号", "起始时间"]
    STATUS_ORDER = ["草稿", "待切分", "已切分", "切分异常"]
    ACTION_RULES = {"启动切分": "待切分", "确认切分": "已切分", "标记异常": "切分异常"}
    NEGATIVE_ACTIONS = []

    def segment_from_transformed(self, batch_key: str, interval_sec: float = 300.0,
                                  distance_threshold: float = 500.0) -> dict[str, Any]:
        """读取坐标转换后的定位点数据集，按时间间隔和距离阈值切分为轨迹分段，生成轨迹分段数据集文件。"""
        # 读取坐标转换后的定位点
        try:
            coord_svc = self._safe_import("app.services.coord_transform", "CoordTransformService")()
            points, points_file = coord_svc.get_transformed_points(batch_key)
        except FileNotFoundError:
            raise FileNotFoundError(f"批次 {batch_key} 的转换数据不存在，请先执行坐标标准化")
        except Exception:
            # fallback: try direct read
            points_file_alt = self._data_dir("coord_transformed") / f"{batch_key}_transformed.jsonl"
            if not points_file_alt.exists():
                raise FileNotFoundError(f"批次 {batch_key} 的转换数据不存在，请先执行坐标标准化")
            points = self._read_jsonl(points_file_alt)

        if not points:
            return {"batch_key": batch_key, "segments": 0, "total_points": 0}

        # 按终端和时间排序
        points.sort(key=lambda p: (p.get("terminal", ""), p.get("timestamp", 0)))

        segments = []
        current_segment = []
        segment_id = 0
        total_mileage = 0.0

        for i, p in enumerate(points):
            if not current_segment:
                current_segment.append(p)
                continue
            last = current_segment[-1]
            time_diff = abs(p.get("timestamp", 0) - last.get("timestamp", 0))
            dist = self._haversine(
                last["trans_lat"], last["trans_lon"],
                p["trans_lat"], p["trans_lon"]
            )
            if time_diff > interval_sec or dist > distance_threshold:
                # 完成当前分段
                segment_id += 1
                seg_start = current_segment[0]
                seg_end = current_segment[-1]
                seg_mileage = 0.0
                for j in range(1, len(current_segment)):
                    seg_mileage += self._haversine(
                        current_segment[j-1]["trans_lat"], current_segment[j-1]["trans_lon"],
                        current_segment[j]["trans_lat"], current_segment[j]["trans_lon"]
                    )
                total_mileage += seg_mileage
                segments.append({
                    "segment_id": segment_id,
                    "batch_key": batch_key,
                    "terminal": seg_start.get("terminal"),
                    "start_time": seg_start.get("timestamp"),
                    "end_time": seg_end.get("timestamp"),
                    "point_count": len(current_segment),
                    "start_lat": seg_start["trans_lat"],
                    "start_lon": seg_start["trans_lon"],
                    "end_lat": seg_end["trans_lat"],
                    "end_lon": seg_end["trans_lon"],
                    "mileage": round(seg_mileage, 2),
                    "interval_threshold": interval_sec,
                    "strategy": "time_distance",
                })
                current_segment = [p]
            else:
                current_segment.append(p)

        # 最后一个分段
        if current_segment and len(current_segment) > 1:
            segment_id += 1
            seg_start = current_segment[0]
            seg_end = current_segment[-1]
            seg_mileage = 0.0
            for j in range(1, len(current_segment)):
                seg_mileage += self._haversine(
                    current_segment[j-1]["trans_lat"], current_segment[j-1]["trans_lon"],
                    current_segment[j]["trans_lat"], current_segment[j]["trans_lon"]
                )
            total_mileage += seg_mileage
            segments.append({
                "segment_id": segment_id,
                "batch_key": batch_key,
                "terminal": seg_start.get("terminal"),
                "start_time": seg_start.get("timestamp"),
                "end_time": seg_end.get("timestamp"),
                "point_count": len(current_segment),
                "start_lat": seg_start["trans_lat"],
                "start_lon": seg_start["trans_lon"],
                "end_lat": seg_end["trans_lat"],
                "end_lon": seg_end["trans_lon"],
                "mileage": round(seg_mileage, 2),
                "interval_threshold": interval_sec,
                "strategy": "time_distance",
            })

        # 写入分段数据集文件
        output_file = self._data_dir("track_segments") / f"{batch_key}_segments.jsonl"
        written = self._write_jsonl(output_file, segments)

        # 自动更新分段记录
        for seg in segments:
            entry, _ = self.create_entry({
                "分段编号": f"SEG-{batch_key}-{seg['segment_id']:03d}",
                "终端编号": seg.get("terminal", "UNKNOWN"),
                "起始时间": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(seg["start_time"])),
                "结束时间": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(seg["end_time"])),
                "点数": str(seg["point_count"]),
                "间隔阈值": f"{interval_sec}s",
                "里程": f"{seg['mileage']}m",
                "切分策略": "time_distance",
            })
            if entry:
                self.run_action(entry["id"], "确认切分")

        return {
            "batch_key": batch_key,
            "total_points": len(points),
            "segments": written,
            "total_mileage": round(total_mileage, 2),
            "output_file": str(output_file),
        }

    def get_segments(self, batch_key: str) -> tuple[list[dict[str, Any]], str]:
        """读取指定批次的轨迹分段数据集文件，返回分段记录和文件路径。"""
        file_path = self._data_dir("track_segments") / f"{batch_key}_segments.jsonl"
        if not file_path.exists():
            raise FileNotFoundError(f"批次 {batch_key} 的轨迹分段数据集不存在")
        segments = self._read_jsonl(file_path)
        return segments, str(file_path)