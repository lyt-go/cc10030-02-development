"""兴趣点匹配领域服务：将驻留事件与POI兴趣点按距离和名称相似度进行候选排名匹配，生成POI匹配数据集。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class PoiMatchService(BaseModuleService):
    MODULE = "poi_match"
    ENTITY = "匹配候选"
    OPERATION_TERM = "匹配"
    LIST_FIELDS = ["候选编号", "驻留事件", "兴趣点编号", "名称相似度", "距离", "候选排名", "采纳结论", "匹配方式"]
    REQUIRED_FIELDS = ["候选编号", "驻留事件", "兴趣点编号"]
    STATUS_ORDER = ["草稿", "待匹配", "已匹配", "已驳回"]
    ACTION_RULES = {"启动匹配": "待匹配", "确认匹配": "已匹配", "标记驳回": "已驳回"}
    NEGATIVE_ACTIONS = []

    def match_dwell_to_poi(self, batch_key: str, poi_list: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """读取驻留事件数据集，将每个驻留事件与POI兴趣点列表按距离排序匹配，生成POI匹配候选数据集。"""
        # 读取驻留事件
        try:
            dwell_svc = self._safe_import("app.services.dwell_detect", "DwellDetectService")()
            events, _ = dwell_svc.get_dwell_events(batch_key)
        except Exception:
            events_file = self._data_dir("dwell_events") / f"{batch_key}_dwell.jsonl"
            if not events_file.exists():
                raise FileNotFoundError(f"批次 {batch_key} 的驻留事件数据不存在，请先执行驻留识别")
            events = self._read_jsonl(events_file)

        if not events:
            return {"batch_key": batch_key, "events_processed": 0, "candidates_generated": 0}

        # 默认POI列表（模拟）
        if poi_list is None:
            poi_list = [
                {"poi_id": "POI_001", "name": "北京站", "lat": 39.9028, "lon": 116.4272},
                {"poi_id": "POI_002", "name": "天安门", "lat": 39.9087, "lon": 116.3975},
                {"poi_id": "POI_003", "name": "国贸", "lat": 39.9087, "lon": 116.4605},
                {"poi_id": "POI_004", "name": "西单", "lat": 39.9120, "lon": 116.3748},
                {"poi_id": "POI_005", "name": "三里屯", "lat": 39.9323, "lon": 116.4551},
            ]

        candidates = []
        match_id = 0
        for evt in events:
            poi_distances = []
            for poi in poi_list:
                dist = self._haversine(evt["center_lat"], evt["center_lon"], poi["lat"], poi["lon"])
                poi_distances.append((poi, dist))
            poi_distances.sort(key=lambda x: x[1])

            for rank, (poi, dist) in enumerate(poi_distances[:3], 1):
                match_id += 1
                candidates.append({
                    "candidate_id": match_id,
                    "batch_key": batch_key,
                    "event_id": evt["event_id"],
                    "poi_id": poi["poi_id"],
                    "poi_name": poi["name"],
                    "dwell_center_lat": evt["center_lat"],
                    "dwell_center_lon": evt["center_lon"],
                    "poi_lat": poi["lat"],
                    "poi_lon": poi["lon"],
                    "distance": round(dist, 2),
                    "rank": rank,
                    "event_type": evt["event_type"],
                    "matched_at": time.time(),
                })

        output_file = self._data_dir("poi_match") / f"{batch_key}_poi.jsonl"
        written = self._write_jsonl(output_file, candidates)

        # 创建匹配记录
        for cand in candidates:
            if cand["rank"] == 1:
                entry, _ = self.create_entry({
                    "候选编号": f"POI-{batch_key}-{cand['candidate_id']:03d}",
                    "驻留事件": str(cand["event_id"]),
                    "兴趣点编号": cand["poi_id"],
                    "名称相似度": f"{cand['poi_name']}",
                    "距离": f"{cand['distance']}m",
                    "候选排名": str(cand["rank"]),
                    "采纳结论": "最佳匹配",
                    "匹配方式": "distance_nearest",
                })
                if entry:
                    self.run_action(entry["id"], "确认匹配")

        return {
            "batch_key": batch_key,
            "events_processed": len(events),
            "candidates_generated": written,
            "output_file": str(output_file),
        }

    def get_poi_matches(self, batch_key: str) -> tuple[list[dict[str, Any]], str]:
        """读取批次的POI匹配候选数据集文件。"""
        file_path = self._data_dir("poi_match") / f"{batch_key}_poi.jsonl"
        data = self._read_jsonl(file_path)
        return data, str(file_path)