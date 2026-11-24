"""位移异常复核与正式标准化专用领域路由。

通用资源路由只承载列表/登记/动作；本路由承载跨服务的复核、更正、正式标准化、
试算、出批与下游侧别核对，状态流转仍全部落在 services 层。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.coord_transform import CoordTransformService
from app.services.displacement_review import DisplacementReviewService
from app.services.geo_quality import GeoQualityService

router = APIRouter(prefix="/api", tags=["位移复核与标准化"])

displacement_svc = DisplacementReviewService()
quality_svc = GeoQualityService()
coord_svc = CoordTransformService()


class OpenReviewPayload(BaseModel):
    batch_key: str
    point_codes: list[str] | None = None


class SaveReviewPayload(BaseModel):
    task_id: int


class CorrectionPayload(BaseModel):
    batch_key: str
    point_code: str
    corrected_lat: float
    corrected_lon: float
    source: str = "现场判定"
    independently_confirmed: bool = True


class AdoptReportPayload(BaseModel):
    report_id: int


class StandardizePayload(BaseModel):
    batch_key: str
    rule_id: int
    overwrite: bool = False


class TrialPayload(BaseModel):
    batch_key: str
    rule_id: int


class BatchKeyPayload(BaseModel):
    batch_key: str


def _guard(call: Any):
    try:
        return call()
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# -- 位移复核看板与任务 --

@router.get("/displacement/board/{batch_key}")
def comparison_board(batch_key: str) -> dict[str, Any]:
    """位移异常前后坐标对照看板（点、质量报告、道路侧别、原/标准坐标并列）。"""
    return displacement_svc.comparison_board(batch_key)


@router.post("/displacement/reviews/open")
def open_review(payload: OpenReviewPayload) -> dict[str, Any]:
    """质量分析打开复核：固化原已确认值，与后到更正隔离。"""
    return _guard(lambda: displacement_svc.open_review(
        payload.batch_key, payload.point_codes))


@router.post("/displacement/reviews/save")
def save_review(payload: SaveReviewPayload) -> dict[str, Any]:
    return _guard(lambda: displacement_svc.save_review(payload.task_id))


@router.post("/displacement/corrections")
def submit_correction(payload: CorrectionPayload) -> dict[str, Any]:
    """登记独立确认的更正；复核未保存时仅挂起待并入，不改写当前复核基准。"""
    return _guard(lambda: displacement_svc.submit_correction(
        payload.batch_key,
        payload.point_code,
        payload.corrected_lat,
        payload.corrected_lon,
        payload.source,
        payload.independently_confirmed,
    ))


@router.get("/displacement/next-scope/{batch_key}")
def next_review_scope(batch_key: str) -> dict[str, Any]:
    return displacement_svc.next_review_scope(batch_key)


# -- 质量报告采用与复核入口 --

@router.post("/quality/adopt")
def adopt_quality_report(payload: AdoptReportPayload) -> dict[str, Any]:
    return _guard(lambda: quality_svc.adopt_report(payload.report_id))


@router.post("/quality/recheck/open")
def open_quality_recheck(payload: OpenReviewPayload) -> dict[str, Any]:
    return _guard(lambda: quality_svc.open_recheck(
        payload.batch_key, payload.point_codes))


# -- 坐标标准化：正式出批、试算、复算、下游核对 --

@router.post("/coord/standardize")
def standardize_batch(payload: StandardizePayload) -> dict[str, Any]:
    """正式标准化，输出携带实际采用的质量报告与转换规则。"""
    return _guard(lambda: coord_svc.standardize_batch(
        payload.batch_key, payload.rule_id, payload.overwrite))


@router.post("/coord/trial")
def trial_transform(payload: TrialPayload) -> dict[str, Any]:
    """另一套生效规则试算，不触碰正式产物。"""
    return _guard(lambda: coord_svc.trial_transform(
        payload.batch_key, payload.rule_id))


@router.post("/coord/recompute")
def recompute_standardization(payload: BatchKeyPayload) -> dict[str, Any]:
    """按历史采用的报告与规则从原签发坐标复算，验证历史结论可复现。"""
    return _guard(lambda: coord_svc.recompute_standardization(payload.batch_key))


@router.post("/coord/issue")
def issue_batch(payload: BatchKeyPayload) -> dict[str, Any]:
    """完成出批并逐点核对下游坐标道路侧别。"""
    return _guard(lambda: coord_svc.issue_batch(payload.batch_key))


@router.post("/coord/verify-downstream")
def verify_downstream(payload: BatchKeyPayload) -> dict[str, Any]:
    return _guard(lambda: coord_svc.verify_downstream_side(payload.batch_key))


@router.get("/coord/standardized/{batch_key}")
def get_standardized(batch_key: str) -> dict[str, Any]:
    def _call():
        points, path = coord_svc.get_standardized_points(batch_key)
        return {"batch_key": batch_key, "points": points, "file": path}
    return _guard(_call)
