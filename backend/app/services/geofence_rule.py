"""电子围栏判定领域服务：维护围栏规则、顶点序列与进出判定条件，对定位点执行围栏命中检测。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class GeofenceRuleService(BaseModuleService):
    MODULE = "geofence_rule"
    ENTITY = "电子围栏"
    OPERATION_TERM = "判定"
    LIST_FIELDS = ["围栏编号", "围栏形状", "顶点序列", "判定方式", "进出条件", "生效时段", "命中次数", "规则版本"]
    REQUIRED_FIELDS = ["围栏编号", "围栏形状", "顶点序列"]
    STATUS_ORDER = ["草稿", "待生效", "已生效", "已停用"]
    ACTION_RULES = {"提交生效": "待生效", "确认生效": "已生效", "标记停用": "已停用"}
    NEGATIVE_ACTIONS = []

    def check_points_against_fence(self, batch_key: str, fence_id: int = 1) -> dict[str, Any]:
        """读取采集批次定位点数据文件，按围栏规则对定位点执行进出判定，生成围栏命中数据集。"""
        fence = store.find(self.MODULE, fence_id)
        if fence is None:
            return {"error": f"围栏 {fence_id} 不存在"}

        # 读取归并后的定位点
        try:
            dedup_svc = self._safe_import("app.services.dedup_merge", "DedupMergeService")()
            points, _ = dedup_svc.get_merged_points(batch_key)
        except Exception:
            points_file = self._data_dir("geo_points") / f"{batch_key}.jsonl"
            if not points_file.exists():
                raise FileNotFoundError(f"批次 {batch_key} 的定位点文件不存在")
            points = self._read_jsonl(points_file)

        if not points:
            return {"batch_key": batch_key, "hits": 0, "total_points": 0}

        # 解析围栏顶点
        vertices_str = fence.get("顶点序列", "39.9,116.4;39.92,116.4;39.92,116.42;39.9,116.42")
        vertices = []
        for v in vertices_str.split(";"):
            parts = v.split(",")
            if len(parts) >= 2:
                vertices.append((float(parts[0]), float(parts[1])))

        hits = []
        for p in points:
            lat = p.get("lat", 0)
            lon = p.get("lon", 0)
            inside = self._point_in_polygon(lat, lon, vertices)
            if inside:
                hits.append({
                    "point_id": p.get("point_id"),
                    "batch_key": batch_key,
                    "fence_id": fence_id,
                    "fence_name": fence.get("围栏编号"),
                    "lat": lat,
                    "lon": lon,
                    "timestamp": p.get("timestamp"),
                    "terminal": p.get("terminal"),
                    "hit_at": time.time(),
                })

        # 写入围栏命中数据集
        output_file = self._data_dir("geofence_hits") / f"{batch_key}_fence{fence_id}_hits.jsonl"
        written = self._write_jsonl(output_file, hits)

        # 更新命中次数（兼容种子数据中非数字值）
        try:
            current_hits = int(fence.get("命中次数", 0))
        except (ValueError, TypeError):
            current_hits = 0
        fence["命中次数"] = str(current_hits + written)

        return {
            "batch_key": batch_key,
            "fence_id": fence_id,
            "total_points": len(points),
            "hits": written,
            "output_file": str(output_file),
        }

    @staticmethod
    def _point_in_polygon(lat: float, lon: float, vertices: list[tuple[float, float]]) -> bool:
        """射线法判断点是否在多边形围栏内。"""
        n = len(vertices)
        inside = False
        j = n - 1
        for i in range(n):
            vi_lat, vi_lon = vertices[i]
            vj_lat, vj_lon = vertices[j]
            if ((vi_lon > lon) != (vj_lon > lon)) and \
               (lat < (vj_lat - vi_lat) * (lon - vi_lon) / (vj_lon - vi_lon) + vi_lat):
                inside = not inside
            j = i
        return inside

    def get_fence_hits(self, batch_key: str, fence_id: int = 1) -> tuple[list[dict[str, Any]], str]:
        """读取批次在指定围栏的命中记录数据集文件。"""
        file_path = self._data_dir("geofence_hits") / f"{batch_key}_fence{fence_id}_hits.jsonl"
        hits = self._read_jsonl(file_path)
        return hits, str(file_path)