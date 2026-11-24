"""坐标标准化领域服务：维护坐标系转换规则、偏移参数与越界处理，并对采集批次执行坐标归一化。

正式标准化（standardize_batch）与通用试算（transform_batch）分离：
- 正式标准化必须绑定一份已采用质量报告和一条已生效规则，单次出批不可混用两份报告，
  输出记录携带实际采用的质量报告编号与转换规则编号/版本，供下游与历史复算；
- 试算（trial_transform）只生成对照数据集，不触碰正式产物；
- 历史复算（recompute_standardization）始终从原签发依据的原始坐标出发，
  不使用后来的现场判定/更正重写历史结论。
"""
from __future__ import annotations

import json
import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class CoordTransformService(BaseModuleService):
    MODULE = "coord_transform"
    ENTITY = "转换规则"
    OPERATION_TERM = "转换"
    LIST_FIELDS = ["规则编号", "源坐标系", "目标坐标系", "偏移参数", "精度位数", "越界处理", "规则版本", "生效批次"]
    REQUIRED_FIELDS = ["规则编号", "源坐标系", "目标坐标系"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
    ACTION_RULES = {"提交校验": "待校验", "确认生效": "已生效", "标记废弃": "已废弃"}
    NEGATIVE_ACTIONS = []

    # -- 规则解析 -------------------------------------------------------

    @staticmethod
    def _parse_rule(rule: dict[str, Any] | None) -> dict[str, Any]:
        offset_lat = 0.0
        offset_lon = 0.0
        precision = 6
        if rule:
            try:
                parts = str(rule.get("偏移参数", "0.0,0.0")).split(",")
                offset_lat = float(parts[0]) if len(parts) > 0 else 0.0
                offset_lon = float(parts[1]) if len(parts) > 1 else 0.0
                precision = int(rule.get("精度位数", "6"))
            except (ValueError, IndexError):
                pass
        return {"offset_lat": offset_lat, "offset_lon": offset_lon, "precision": precision}

    def _build_transformed(self, points: list[dict[str, Any]], batch_key: str,
                           rule: dict[str, Any] | None,
                           quality_report_code: str | None) -> list[dict[str, Any]]:
        cfg = self._parse_rule(rule)
        offset_lat, offset_lon, precision = cfg["offset_lat"], cfg["offset_lon"], cfg["precision"]
        transformed = []
        for p in points:
            new_lat = round(p["lat"] + offset_lat, precision)
            new_lon = round(p["lon"] + offset_lon, precision)
            # 越界处理
            if new_lat > 90 or new_lat < -90:
                new_lat = max(-90, min(90, new_lat))
            if new_lon > 180 or new_lon < -180:
                new_lon = max(-180, min(180, new_lon))
            record = {
                "point_id": p["point_id"],
                "batch_key": batch_key,
                "orig_lat": p["lat"],
                "orig_lon": p["lon"],
                "trans_lat": new_lat,
                "trans_lon": new_lon,
                "timestamp": p.get("timestamp"),
                "terminal": p.get("terminal"),
                "accuracy": p.get("accuracy"),
                "bearing": p.get("bearing"),
                "speed": p.get("speed"),
                "rule_id": rule.get("id") if rule else None,
                "rule_version": rule.get("规则版本", "v1.0") if rule else "v1.0",
            }
            # 正式标准化必须把实际采用的质量报告与转换规则带进输出
            if quality_report_code is not None:
                record["quality_report"] = quality_report_code
            transformed.append(record)
        return transformed

    def transform_batch(self, batch_key: str, rule_id: int = 1) -> dict[str, Any]:
        """读取采集批次定位点文件，解析原始坐标并执行坐标系转换，生成转换后的定位点数据集文件。"""
        points_file = self._data_dir("geo_points") / f"{batch_key}.jsonl"
        if not points_file.exists():
            raise FileNotFoundError(f"批次 {batch_key} 的定位点数据文件不存在，请先接入采集批次")
        points = self._read_jsonl(points_file)

        rule = store.find(self.MODULE, rule_id)
        transformed = self._build_transformed(points, batch_key, rule, None)

        output_file = self._data_dir("coord_transformed") / f"{batch_key}_transformed.jsonl"
        written = self._write_jsonl(output_file, transformed)

        if rule:
            rule["生效批次"] = batch_key

        return {
            "batch_key": batch_key,
            "input_points": len(points),
            "transformed_points": written,
            "rule_id": rule_id,
            "output_file": str(output_file),
        }

    def get_transformed_points(self, batch_key: str) -> tuple[list[dict[str, Any]], str]:
        """读取指定批次的坐标转换结果数据集，返回转换后的定位点记录和文件路径。"""
        file_path = self._data_dir("coord_transformed") / f"{batch_key}_transformed.jsonl"
        if not file_path.exists():
            raise FileNotFoundError(f"批次 {batch_key} 的转换数据集文件不存在")
        points = self._read_jsonl(file_path)
        return points, str(file_path)

    def apply_rule_to_all(self, rule_id: int) -> dict[str, Any]:
        """对已接入的全部采集批次应用指定转换规则，批量生成坐标标准化数据集。"""
        rule = store.find(self.MODULE, rule_id)
        if rule is None:
            return {"error": f"转换规则 {rule_id} 不存在"}
        geo_svc = self._safe_import("app.services.geo_source", "GeoSourceService")()
        active_batches = geo_svc.list_active_batches()
        results = {}
        for batch in active_batches:
            batch_key = batch["批次编号"]
            try:
                results[batch_key] = self.transform_batch(batch_key, rule_id)
            except Exception as e:
                results[batch_key] = {"error": str(e)}
        return {"rule_id": rule_id, "batches_processed": len(results), "results": results}

    # -- 正式标准化（绑定质量报告 + 转换规则） --------------------------

    def _adopted_quality_report(self, batch_key: str) -> dict[str, Any]:
        adopted = [
            row for row in store.rows("geo_quality")
            if str(row.get("批次编号")) == batch_key and row.get("采用状态") == "已采用"
        ]
        if not adopted:
            raise ValueError(f"批次 {batch_key} 没有已采用的质量报告，不能正式标准化")
        if len(adopted) > 1:
            # 单次正式标准化不可混用两份报告
            raise ValueError(
                f"批次 {batch_key} 存在多份已采用质量报告，"
                "单次正式标准化不可混用两份报告"
            )
        return adopted[0]

    def standardize_batch(self, batch_key: str, rule_id: int,
                          overwrite: bool = False) -> dict[str, Any]:
        """正式标准化：绑定唯一已采用质量报告与一条已生效规则后生成正式坐标数据集。"""
        report = self._adopted_quality_report(batch_key)
        rule = store.find(self.MODULE, rule_id)
        if rule is None:
            raise ValueError(f"转换规则 {rule_id} 不存在")
        if rule.get("status") != "已生效":
            raise ValueError(f"转换规则 {rule_id} 未生效（当前状态：{rule.get('status')}），不能用于正式标准化")

        # 已出批的批次默认禁止重写，保证历史结论可从采用的点与规则复算
        issued_cp = self._read_checkpoint(f"coord_issue_{batch_key}")
        if issued_cp and not overwrite:
            raise ValueError(
                f"批次 {batch_key} 已完成正式出批；如需重新核查请显式覆盖，"
                "历史正式产物将按原报告与原规则留痕"
            )

        points_file = self._data_dir("geo_points") / f"{batch_key}.jsonl"
        if not points_file.exists():
            raise FileNotFoundError(f"批次 {batch_key} 的定位点数据文件不存在，请先接入采集批次")
        points = self._read_jsonl(points_file)

        quality_report_code = str(report.get("报告编号"))
        transformed = self._build_transformed(points, batch_key, rule, quality_report_code)
        for record in transformed:
            record["quality_report_id"] = report.get("id")
            record["rule_code"] = rule.get("规则编号")
            record["standardized_at"] = time.time()

        output_file = self._data_dir("coord_standardized") / f"{batch_key}_standardized.jsonl"
        self._overwrite_jsonl(output_file, transformed)
        rule["生效批次"] = batch_key

        summary = {
            "batch_key": batch_key,
            "input_points": len(points),
            "standardized_points": len(transformed),
            "quality_report": quality_report_code,
            "quality_report_id": report.get("id"),
            "rule_id": rule_id,
            "rule_code": rule.get("规则编号"),
            "rule_version": rule.get("规则版本"),
            "output_file": str(output_file),
            "overwrite": overwrite,
        }
        self._write_checkpoint(f"coord_standardize_{batch_key}", summary)
        return summary

    def trial_transform(self, batch_key: str, rule_id: int) -> dict[str, Any]:
        """用另一套生效规则试算，只写试算数据集，不改变正式标准化产物与已采用报告。"""
        rule = store.find(self.MODULE, rule_id)
        if rule is None:
            raise ValueError(f"转换规则 {rule_id} 不存在")
        points_file = self._data_dir("geo_points") / f"{batch_key}.jsonl"
        if not points_file.exists():
            raise FileNotFoundError(f"批次 {batch_key} 的定位点数据文件不存在")
        points = self._read_jsonl(points_file)
        transformed = self._build_transformed(points, batch_key, rule, None)
        output_file = (
            self._data_dir("coord_trial") / f"{batch_key}_rule{rule_id}_trial.jsonl"
        )
        self._overwrite_jsonl(output_file, transformed)
        return {
            "batch_key": batch_key,
            "rule_id": rule_id,
            "rule_code": rule.get("规则编号"),
            "rule_version": rule.get("规则版本"),
            "trial_points": len(transformed),
            "output_file": str(output_file),
        }

    def recompute_standardization(self, batch_key: str) -> dict[str, Any]:
        """按历史正式出批采用的质量报告与转换规则，从原始坐标复算标准坐标。

        只读取原签发依据（geo_points 原始坐标 + 出批检查点中的报告/规则），
        不读取后来的现场判定或待并入更正，因此历史结论可被完整复现。
        """
        issue = self._read_checkpoint(f"coord_issue_{batch_key}")
        if not issue:
            std_cp = self._read_checkpoint(f"coord_standardize_{batch_key}")
            if not std_cp:
                raise ValueError(f"批次 {batch_key} 没有正式标准化记录，无法复算")
            issue = std_cp
        rule_id = int(issue["rule_id"])
        rule = store.find(self.MODULE, rule_id)
        points_file = self._data_dir("geo_points") / f"{batch_key}.jsonl"
        points = self._read_jsonl(points_file)
        recomputed = self._build_transformed(
            points, batch_key, rule, str(issue["quality_report"]))

        official, _ = self.get_standardized_points(batch_key)
        mismatches = []
        for old, new in zip(official, recomputed):
            if (old.get("trans_lat") != new.get("trans_lat")
                    or old.get("trans_lon") != new.get("trans_lon")):
                mismatches.append({
                    "point_id": new.get("point_id"),
                    "issued": [old.get("trans_lat"), old.get("trans_lon")],
                    "recomputed": [new.get("trans_lat"), new.get("trans_lon")],
                })
        return {
            "batch_key": batch_key,
            "quality_report": issue.get("quality_report"),
            "rule_id": rule_id,
            "rule_version": issue.get("rule_version"),
            "recomputed_points": len(recomputed),
            "mismatches": mismatches,
            "reproducible": not mismatches,
        }

    def get_standardized_points(self, batch_key: str) -> tuple[list[dict[str, Any]], str]:
        file_path = self._data_dir("coord_standardized") / f"{batch_key}_standardized.jsonl"
        if not file_path.exists():
            raise FileNotFoundError(f"批次 {batch_key} 的正式标准化数据集不存在")
        return self._read_jsonl(file_path), str(file_path)

    # -- 出批与下游核对 -------------------------------------------------

    def issue_batch(self, batch_key: str) -> dict[str, Any]:
        """完成正式出批：固化本批采用的报告与规则，并逐点核对送到下游的坐标道路侧别。"""
        std_cp = self._read_checkpoint(f"coord_standardize_{batch_key}")
        if not std_cp:
            raise ValueError(f"批次 {batch_key} 尚未正式标准化，不能出批")
        points, file_path = self.get_standardized_points(batch_key)

        displacement_svc = self._safe_import(
            "app.services.displacement_review", "DisplacementReviewService")()
        road = displacement_svc._road_for(batch_key)
        side_summary: dict[str, int] = {}
        point_sides = []
        for record in points:
            side = displacement_svc.classify_side(
                float(record["trans_lat"]), float(record["trans_lon"]), road)
            side_summary[side] = side_summary.get(side, 0) + 1
            point_sides.append({"point_id": record.get("point_id"), "side": side})

        issue = {
            "batch_key": batch_key,
            "issued_at": time.time(),
            "quality_report": std_cp["quality_report"],
            "quality_report_id": std_cp.get("quality_report_id"),
            "rule_id": std_cp["rule_id"],
            "rule_code": std_cp.get("rule_code"),
            "rule_version": std_cp.get("rule_version"),
            "issued_points": len(points),
            "side_summary": side_summary,
            "output_file": file_path,
        }
        self._write_checkpoint(f"coord_issue_{batch_key}", issue)
        with open(
            self._data_dir("coord_issues") / f"{batch_key}_issue.jsonl",
            "w", encoding="utf-8",
        ) as f:
            f.write(json.dumps(issue, ensure_ascii=False, default=str) + "\n")
        return issue

    def verify_downstream_side(self, batch_key: str) -> dict[str, Any]:
        """核对这批送到下游的坐标到底位于道路哪一侧，并给出下一次待复核范围。"""
        issue = self._read_checkpoint(f"coord_issue_{batch_key}")
        if not issue:
            raise ValueError(f"批次 {batch_key} 尚未出批")
        points, _ = self.get_standardized_points(batch_key)
        displacement_svc = self._safe_import(
            "app.services.displacement_review", "DisplacementReviewService")()
        road = displacement_svc._road_for(batch_key)
        detail = []
        for record in points:
            detail.append({
                "point_id": record.get("point_id"),
                "trans_lat": record.get("trans_lat"),
                "trans_lon": record.get("trans_lon"),
                "side": displacement_svc.classify_side(
                    float(record["trans_lat"]), float(record["trans_lon"]), road),
                "quality_report": record.get("quality_report"),
                "rule_code": record.get("rule_code"),
            })
        next_scope = displacement_svc.next_review_scope(batch_key)
        return {
            "批次编号": batch_key,
            "出批报告": issue.get("quality_report"),
            "出批规则": issue.get("rule_code"),
            "侧别汇总": issue.get("side_summary"),
            "逐点侧别": detail,
            "下一次待复核范围": next_scope,
        }

    # -- 文件工具 -------------------------------------------------------

    def _overwrite_jsonl(self, filepath: Any, records: list[dict[str, Any]]) -> int:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return len(records)
