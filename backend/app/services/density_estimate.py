"""密度估算领域服务：对网格内定位点执行核密度估算(KDE)，计算带宽参数、密度值和置信区间。"""
from __future__ import annotations

import math
import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DensityEstimateService(BaseModuleService):
    MODULE = "density_estimate"
    ENTITY = "密度网格"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["估算编号", "网格编号", "带宽参数", "核函数类型", "估算密度", "样本点数", "置信区间", "估算版本"]
    REQUIRED_FIELDS = ["估算编号", "网格编号", "带宽参数"]
    STATUS_ORDER = ["草稿", "待估算", "已估算", "已过期"]
    ACTION_RULES = {"启动估算": "待估算", "确认估算": "已估算", "标记过期": "已过期"}
    NEGATIVE_ACTIONS = []

    def estimate_density(self, batch_key: str, bandwidth: float = 500.0,
                          kernel: str = "gaussian") -> dict[str, Any]:
        """读取网格索引数据集，对每个网格内的定位点执行核密度估算，生成密度估算数据集文件。"""
        # 读取网格索引
        try:
            grid_svc = self._safe_import("app.services.grid_index", "GridIndexService")()
            grids, _ = grid_svc.get_grid_index(batch_key)
        except Exception:
            grids_file = self._data_dir("grid_index") / f"{batch_key}_grid.jsonl"
            if not grids_file.exists():
                raise FileNotFoundError(f"批次 {batch_key} 的网格索引数据不存在")
            grids = self._read_jsonl(grids_file)

        # 读取坐标转换后定位点
        points_file = self._data_dir("coord_transformed") / f"{batch_key}_transformed.jsonl"
        if not points_file.exists():
            points_file = self._data_dir("geo_points") / f"{batch_key}.jsonl"
        points = self._read_jsonl(points_file) if points_file.exists() else []

        if not grids or not points:
            return {"batch_key": batch_key, "grids_estimated": 0}

        density_grids = []
        for g in grids:
            # 计算该网格内点对网格中心的加权密度
            sample_count = g["point_count"]
            if sample_count == 0:
                continue

            # Gaussian kernel: K(u) = exp(-u^2/2) / sqrt(2*pi)
            # density = sum(kernel contributions) / (n * bandwidth)
            density_sum = 0.0
            weights = []
            for p in points:
                lat = p.get("trans_lat", p.get("lat", 0))
                lon = p.get("trans_lon", p.get("lon", 0))
                dist = self._haversine(g["center_lat"], g["center_lon"], lat, lon)
                u = dist / bandwidth if bandwidth > 0 else 0
                if kernel == "gaussian":
                    weight = math.exp(-0.5 * u * u) / math.sqrt(2 * math.pi)
                elif kernel == "uniform":
                    weight = 1.0 if u <= 1.0 else 0.0
                else:
                    weight = max(0, 1 - u * u)  # Epanechnikov
                density_sum += weight
                weights.append(weight)

            estimated_density = density_sum / (max(sample_count, 1) * bandwidth) if bandwidth > 0 else 0
            # 置信区间近似：mean ± 1.96 * std / sqrt(n)
            mean_w = sum(weights) / max(len(weights), 1)
            var_w = sum((w - mean_w) ** 2 for w in weights) / max(len(weights), 1)
            se = math.sqrt(var_w / max(len(weights), 1))
            ci_low = round(estimated_density - 1.96 * se, 8)
            ci_high = round(estimated_density + 1.96 * se, 8)

            density_grids.append({
                "grid_geohash": g["geohash"],
                "batch_key": batch_key,
                "grid_id": g["grid_id"],
                "bandwidth": bandwidth,
                "kernel": kernel,
                "estimated_density": round(estimated_density, 8),
                "sample_count": sample_count,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "estimated_at": time.time(),
            })

        output_file = self._data_dir("density_estimate") / f"{batch_key}_density.jsonl"
        written = self._write_jsonl(output_file, density_grids)

        # 创建估算记录
        for dg in density_grids:
            entry, _ = self.create_entry({
                "估算编号": f"DENS-{batch_key}-{dg['grid_id']:03d}",
                "网格编号": dg["grid_geohash"],
                "带宽参数": f"{dg['bandwidth']}m",
                "核函数类型": dg["kernel"],
                "估算密度": f"{dg['estimated_density']:.8f}",
                "样本点数": str(dg["sample_count"]),
                "置信区间": f"[{dg['ci_low']:.8f},{dg['ci_high']:.8f}]",
                "估算版本": "v1.0",
            })
            if entry:
                self.run_action(entry["id"], "确认估算")

        return {
            "batch_key": batch_key,
            "grids_processed": len(grids),
            "grids_estimated": written,
            "bandwidth": bandwidth,
            "kernel": kernel,
            "output_file": str(output_file),
        }

    def get_density_estimates(self, batch_key: str) -> tuple[list[dict[str, Any]], str]:
        """读取批次的密度估算数据集文件。"""
        file_path = self._data_dir("density_estimate") / f"{batch_key}_density.jsonl"
        data = self._read_jsonl(file_path)
        return data, str(file_path)