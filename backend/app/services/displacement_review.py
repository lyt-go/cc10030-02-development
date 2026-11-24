"""位移异常复核领域服务。

承载「位移异常 → 原坐标/标准坐标对照 → 质量报告与转换规则绑定 → 出批」的复核主线：

- 同一看板并列展示位移异常前后的原坐标与标准坐标，并给出点、采用的质量报告、道路侧别；
- 复核任务打开时固化的是值班员「原已确认值」，复核未保存期间到达的更正只登记、不并入，
  更正须经独立确认，历史结论始终能从采用的点与规则复算；
- 道路侧别依据道路锚点与走向按左/右手法则判定，供正式标准化与试算结果对照。
"""
from __future__ import annotations

import math
import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

POINT_TABLE = "displacement_point"
ROAD_TABLE = "displacement_road"
CORRECTION_TABLE = "displacement_correction"
TASK_TABLE = "displacement_review"


class DisplacementReviewService(BaseModuleService):
    MODULE = TASK_TABLE
    ENTITY = "复核任务"
    OPERATION_TERM = "复核"
    LIST_FIELDS = ["任务编号", "批次编号", "点位编号", "道路侧别", "采用质量报告", "转换规则", "复核状态"]
    REQUIRED_FIELDS = ["任务编号", "批次编号", "点位编号"]
    STATUS_ORDER = ["草稿", "复核中", "已保存", "已关闭"]
    ACTION_RULES = {"启动复核": "复核中", "保存复核": "已保存", "关闭任务": "已关闭"}
    NEGATIVE_ACTIONS = []

    # -- 道路与点位基线 -------------------------------------------------

    def register_road(self, batch_key: str, road_code: str, anchor_lat: float,
                      anchor_lon: float, bearing_deg: float) -> dict[str, Any]:
        """登记批次采用的道路走向（锚点经纬度 + 行进方位角），作为侧别判定基准。"""
        for row in store.rows(ROAD_TABLE):
            if row.get("批次编号") == batch_key and row.get("道路编号") == road_code:
                row.update({
                    "锚点纬度": anchor_lat, "锚点经度": anchor_lon, "方位角": bearing_deg,
                })
                return row
        road = {
            "id": max((int(r.get("id", 0)) for r in store.rows(ROAD_TABLE)), default=0) + 1,
            "批次编号": batch_key,
            "道路编号": road_code,
            "锚点纬度": anchor_lat,
            "锚点经度": anchor_lon,
            "方位角": bearing_deg,
        }
        store.rows(ROAD_TABLE).append(road)
        return road

    def _road_for(self, batch_key: str) -> dict[str, Any] | None:
        for row in store.rows(ROAD_TABLE):
            if row.get("批次编号") == batch_key:
                return row
        return None

    def register_point(self, batch_key: str, point_code: str, road_code: str,
                       orig_lat: float, orig_lon: float,
                       confirmed_lat: float, confirmed_lon: float,
                       quality_report_code: str, rule_code: str) -> dict[str, Any]:
        """登记位移异常点：原坐标（原签发依据）与值班员已确认的标准坐标分列保存。

        后续现场判定不得覆盖 confirmed_*，只能以更正单据的形式独立登记。
        """
        road = self._road_for(batch_key)
        rows = store.rows(POINT_TABLE)
        for row in rows:
            if row.get("批次编号") == batch_key and row.get("点位编号") == point_code:
                raise ValueError(f"点位 {point_code} 已在批次 {batch_key} 登记，禁止重复写入")
        orig_side = self.classify_side(orig_lat, orig_lon, road) if road else "未判定"
        confirmed_side = self.classify_side(confirmed_lat, confirmed_lon, road) if road else "未判定"
        point = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "批次编号": batch_key,
            "点位编号": point_code,
            "道路编号": road_code,
            "道路侧别": confirmed_side,
            "原坐标纬度": orig_lat,
            "原坐标经度": orig_lon,
            "原坐标侧别": orig_side,
            "标准坐标纬度": confirmed_lat,
            "标准坐标经度": confirmed_lon,
            "标准坐标侧别": confirmed_side,
            "位移异常": orig_side != confirmed_side,
            "采用质量报告": quality_report_code,
            "采用转换规则": rule_code,
            "复核状态": "待复核",
            "更正状态": "无更正",
        }
        rows.append(point)
        return point

    def find_point(self, batch_key: str, point_code: str) -> dict[str, Any] | None:
        for row in store.rows(POINT_TABLE):
            if row.get("批次编号") == batch_key and row.get("点位编号") == point_code:
                return row
        return None

    def list_points(self, batch_key: str) -> list[dict[str, Any]]:
        return [r for r in store.rows(POINT_TABLE) if r.get("批次编号") == batch_key]

    @staticmethod
    def classify_side(lat: float, lon: float, road: dict[str, Any] | None,
                      tolerance_m: float = 1.0) -> str:
        """按道路锚点与方位角判定点位于道路内侧（行进方向左侧）还是外侧（右侧）。

        以道路行进方向 (d_e, d_n) 的左手法向量 (-d_n, d_e) 与锚点→点位向量做点积，
        点积为正即位于行进方向左侧（内侧），为负位于右侧（外侧）。
        """
        if not road:
            return "未判定"
        anchor_lat = float(road["锚点纬度"])
        anchor_lon = float(road["锚点经度"])
        bearing = math.radians(float(road["方位角"]))
        d_e, d_n = math.sin(bearing), math.cos(bearing)
        v_e = (float(lon) - anchor_lon) * math.cos(math.radians(anchor_lat)) * 111320.0
        v_n = (float(lat) - anchor_lat) * 110540.0
        cross = v_n * d_e - v_e * d_n
        if abs(cross) <= tolerance_m:
            return "轴线上"
        return "内侧" if cross > 0 else "外侧"

    # -- 复核任务 -------------------------------------------------------

    def _adopted_report_code(self, batch_key: str) -> str | None:
        for row in store.rows("geo_quality"):
            if row.get("批次编号") == batch_key and row.get("采用状态") == "已采用":
                return str(row.get("报告编号"))
        return None

    def open_tasks(self, batch_key: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(TASK_TABLE)
            if row.get("批次编号") == batch_key and row.get("status") == "复核中"
        ]

    def open_review(self, batch_key: str, point_codes: list[str] | None = None) -> dict[str, Any]:
        """打开复核任务。

        质量分析打开复核使用的是原已确认值：任务快照只取点位主数据中的标准坐标，
        不读取任何后到的现场判定/更正坐标。复核未保存期间，这些值保持冻结。
        """
        report_code = self._adopted_report_code(batch_key)
        if not report_code:
            raise ValueError(f"批次 {batch_key} 缺少已采用的质量报告，不能打开复核")
        if self.open_tasks(batch_key):
            raise ValueError(f"批次 {batch_key} 已有未保存的复核任务，请先保存或关闭")

        points = self.list_points(batch_key)
        if point_codes:
            wanted = set(point_codes)
            points = [p for p in points if p.get("点位编号") in wanted]
        if not points:
            raise ValueError(f"批次 {batch_key} 下没有可复核的位移异常点")

        task_rows = store.rows(TASK_TABLE)
        task_id = max((int(r.get("id", 0)) for r in task_rows), default=0) + 1
        task_no = f"REV-{batch_key}-{task_id}"
        now = time.time()
        frozen = []
        for point in points:
            if point.get("复核状态") == "复核中":
                raise ValueError(f"点位 {point.get('点位编号')} 已在另一复核任务中")
            # 冻结原已确认值作为本次复核基准，与之后到达的更正严格隔离
            frozen.append({
                "点位编号": point["点位编号"],
                "基准纬度": point["标准坐标纬度"],
                "基准经度": point["标准坐标经度"],
                "基准侧别": point["标准坐标侧别"],
                "采用质量报告": point.get("采用质量报告") or report_code,
                "采用转换规则": point.get("采用转换规则"),
            })
            point["复核状态"] = "复核中"
        task = {
            "id": task_id,
            "status": "复核中",
            "pending": True,
            "abnormal": False,
            "任务编号": task_no,
            "批次编号": batch_key,
            "点位编号": "、".join(item["点位编号"] for item in frozen),
            "道路侧别": "、".join(sorted({item["基准侧别"] for item in frozen})),
            "采用质量报告": report_code,
            "转换规则": "、".join(sorted({str(item["采用转换规则"]) for item in frozen})),
            "复核状态": "复核中",
            "opened_at": now,
            "frozen_points": frozen,
        }
        task_rows.append(task)
        self._write_checkpoint(f"displacement_review_{task_no}", task)
        return task

    def save_review(self, task_id: int) -> dict[str, Any]:
        """保存复核任务。

        保存只确认冻结基准上的复核结论；未保存期间到达的更正保持「待并入」，
        自动构成下一次待复核范围，不回写本次结论。
        """
        task = store.find(TASK_TABLE, task_id)
        if task is None:
            raise ValueError(f"复核任务 {task_id} 不存在")
        if task.get("status") != "复核中":
            raise ValueError(f"复核任务 {task_id} 不在复核中，无需保存")
        task["status"] = "已保存"
        task["pending"] = False
        task["复核状态"] = "已保存"
        task["saved_at"] = time.time()
        codes = {item["点位编号"] for item in task.get("frozen_points", [])}
        for point in self.list_points(task["批次编号"]):
            if point.get("点位编号") in codes:
                point["复核状态"] = "已复核"
        self._write_checkpoint(f"displacement_review_{task['任务编号']}", task)
        return task

    # -- 更正登记（独立确认，不覆盖历史结论） ---------------------------

    def submit_correction(self, batch_key: str, point_code: str,
                          corrected_lat: float, corrected_lon: float,
                          source: str = "现场判定",
                          independently_confirmed: bool = True) -> dict[str, Any]:
        """登记后到的坐标更正。

        更正必须独立确认；若批次存在未保存的复核任务，更正只入账（待并入），
        在重新核查完成前不会改写本次复核使用的原已确认值。
        """
        point = self.find_point(batch_key, point_code)
        if point is None:
            raise ValueError(f"批次 {batch_key} 下点位 {point_code} 不存在")
        if not independently_confirmed:
            raise ValueError("更正坐标未经独立确认，不能登记")
        blocked = bool(self.open_tasks(batch_key))
        row = {
            "id": max((int(r.get("id", 0)) for r in store.rows(CORRECTION_TABLE)), default=0) + 1,
            "批次编号": batch_key,
            "点位编号": point_code,
            "更正纬度": corrected_lat,
            "更正经度": corrected_lon,
            "更正来源": source,
            "独立确认": "是",
            "登记时间": time.time(),
            # 复核未保存时挂起；保存后并入下一次复核范围
            "并入状态": "待并入" if blocked else "待并入",
            "复核未保存到达": "是" if blocked else "否",
        }
        store.rows(CORRECTION_TABLE).append(row)
        point["更正状态"] = "待并入更正"
        self._write_jsonl(
            self._data_dir("displacement") / f"{batch_key}_corrections.jsonl", [row])
        return row

    def pending_corrections(self, batch_key: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(CORRECTION_TABLE)
            if row.get("批次编号") == batch_key and row.get("并入状态") == "待并入"
        ]

    def next_review_scope(self, batch_key: str) -> dict[str, Any]:
        """下一次待复核范围：所有持有待并入更正的点及其更正来源。"""
        corrections = self.pending_corrections(batch_key)
        scope = []
        for correction in corrections:
            point = self.find_point(batch_key, str(correction["点位编号"]))
            scope.append({
                "点位编号": correction["点位编号"],
                "道路侧别": point.get("道路侧别") if point else "未判定",
                "当前标准坐标": (
                    [point.get("标准坐标纬度"), point.get("标准坐标经度")] if point else None
                ),
                "待并入更正坐标": [correction["更正纬度"], correction["更正经度"]],
                "更正来源": correction.get("更正来源"),
                "独立确认": correction.get("独立确认"),
            })
        return {
            "批次编号": batch_key,
            "待复核点数": len(scope),
            "范围": scope,
        }

    # -- 对照看板 -------------------------------------------------------

    def comparison_board(self, batch_key: str) -> dict[str, Any]:
        """位移异常前后坐标对照视图：点、采用质量报告、道路侧别、原坐标与标准坐标并列。"""
        road = self._road_for(batch_key)
        rows = []
        for point in self.list_points(batch_key):
            correction = next((
                c for c in self.pending_corrections(batch_key)
                if str(c.get("点位编号")) == str(point.get("点位编号"))
            ), None)
            rows.append({
                "点位编号": point["点位编号"],
                "道路编号": point.get("道路编号"),
                "道路侧别": point.get("道路侧别"),
                "采用质量报告": point.get("采用质量报告"),
                "采用转换规则": point.get("采用转换规则"),
                "位移异常": point.get("位移异常"),
                "原坐标": {
                    "lat": point.get("原坐标纬度"),
                    "lon": point.get("原坐标经度"),
                    "side": point.get("原坐标侧别"),
                },
                "标准坐标": {
                    "lat": point.get("标准坐标纬度"),
                    "lon": point.get("标准坐标经度"),
                    "side": point.get("标准坐标侧别"),
                },
                "待并入更正": (
                    {
                        "lat": correction["更正纬度"],
                        "lon": correction["更正经度"],
                        "side": self.classify_side(
                            correction["更正纬度"], correction["更正经度"], road),
                        "source": correction.get("更正来源"),
                    }
                    if correction else None
                ),
                "复核状态": point.get("复核状态"),
                "更正状态": point.get("更正状态"),
            })
        tasks = [
            {
                "任务编号": t.get("任务编号"),
                "状态": t.get("status"),
                "点位编号": t.get("点位编号"),
                "采用质量报告": t.get("采用质量报告"),
            }
            for t in store.rows(TASK_TABLE) if t.get("批次编号") == batch_key
        ]
        return {
            "批次编号": batch_key,
            "道路": road,
            "对照点": rows,
            "复核任务": tasks,
            "待并入更正数": len(self.pending_corrections(batch_key)),
        }
