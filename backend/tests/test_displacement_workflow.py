"""位移异常复核 → 正式标准化 → 出批 → 下游侧别核对的完整主线测试。

覆盖题述关键约束：
1. 复核打开使用原已确认值，未保存期间到达的更正只挂起、不改写基准；
2. 单次正式标准化不可混用两份质量报告；
3. 正式标准化输出携带实际采用的质量报告与转换规则；
4. 已确认规则落在道路内侧，另一套生效规则试算使两点落到外侧；
5. 历史结论可从采用的点与规则从原坐标复算，且不被后到现场判定重写；
6. 出批后可核对下游坐标侧别，并得到下一次待复核范围。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def setup_data_dir(monkeypatch):
    tmpdir = Path(tempfile.mkdtemp(prefix="geo_disp_"))
    import app.services.base as base_mod
    original_root = base_mod.DATA_ROOT
    base_mod.DATA_ROOT = tmpdir
    for sub in ["geo_points", "coord_transformed", "coord_standardized", "coord_trial",
                "coord_issues", "quality_reports", "displacement", "checkpoints"]:
        (tmpdir / sub).mkdir(parents=True, exist_ok=True)
    yield tmpdir
    base_mod.DATA_ROOT = original_root
    shutil.rmtree(tmpdir, ignore_errors=True)


def _write_points(svc, batch_key, points):
    path = svc._data_dir("geo_points") / f"{batch_key}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for p in points:
            f.write(json.dumps({"batch_key": batch_key, **p}, ensure_ascii=False) + "\n")
    return path


def _build_batches(suffix: str):
    from app.services.coord_transform import CoordTransformService
    from app.services.displacement_review import DisplacementReviewService
    from app.services.geo_quality import GeoQualityService
    from app.store import store

    disp = DisplacementReviewService()
    quality = GeoQualityService()
    coord = CoordTransformService()

    batch_key = f"DISP-E2E-{suffix}"
    # 道路：东西走向（方位角90°），锚点在 (39.9042, 116.4074)
    disp.register_road(batch_key, "ROAD-N3", 39.9042, 116.4074, 90.0)

    # 原坐标均在道路南侧（外侧）；值班员确认值在道路北侧（内侧）
    _write_points(coord, batch_key, [
        {"point_id": "P-1001", "lat": 39.9034, "lon": 116.4087},
        {"point_id": "P-1002", "lat": 39.9036, "lon": 116.4091},
    ])
    disp.register_point(batch_key, "P-1001", "ROAD-N3",
                        39.9034, 116.4087, 39.9044, 116.4082,
                        f"QR-E2E-{suffix}", "COOR-R1001")
    disp.register_point(batch_key, "P-1002", "ROAD-N3",
                        39.9036, 116.4091, 39.9046, 116.4086,
                        f"QR-E2E-{suffix}", "COOR-R1001")

    # 质量报告并采用
    rows = store.rows("geo_quality")
    report_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
    rows.append({
        "id": report_id, "status": "已出报告", "pending": False, "abnormal": False,
        "报告编号": f"QR-E2E-{suffix}", "批次编号": batch_key, "漂移点数": "2",
        "重复点数": "0", "缺失率": "0.0%", "异常时段": "2026-11-20",
        "校验规则": "最大速度50m/s", "质检结论": "待复核",
    })
    quality.adopt_report(report_id)

    # 两条生效规则：内侧确认规则 / 外侧试算规则
    coord_rows = store.rows("coord_transform")
    base_id = max((int(r.get("id", 0)) for r in coord_rows), default=0)
    r_inside = base_id + 1
    r_outside = base_id + 2
    coord_rows.append({
        "id": r_inside, "status": "已生效", "pending": False, "abnormal": False,
        "规则编号": "COOR-R1001", "源坐标系": "WGS84", "目标坐标系": "GCJ02",
        "偏移参数": "0.0010,-0.0005", "精度位数": "6", "越界处理": "clamp",
        "规则版本": "v内侧-1", "生效批次": "",
    })
    coord_rows.append({
        "id": r_outside, "status": "已生效", "pending": False, "abnormal": False,
        "规则编号": "COOR-R1002", "源坐标系": "WGS84", "目标坐标系": "GCJ02",
        "偏移参数": "-0.0008,0.0008", "精度位数": "6", "越界处理": "clamp",
        "规则版本": "v外侧试算-1", "生效批次": "",
    })
    return disp, quality, coord, batch_key, r_inside, r_outside


class TestDisplacementWorkflow:
    def test_board_shows_before_after_coordinates_and_side(self):
        suffix = "01"
        disp, _, _, batch_key, _, _ = _build_batches(suffix)
        board = disp.comparison_board(batch_key)
        assert len(board["对照点"]) == 2
        first = board["对照点"][0]
        assert first["点位编号"] == "P-1001"
        assert first["采用质量报告"] == f"QR-E2E-{suffix}"
        assert first["道路侧别"] == "内侧"
        assert first["原坐标"]["side"] == "外侧"
        assert first["标准坐标"]["side"] == "内侧"
        assert first["位移异常"] is True

    def test_open_review_freezes_confirmed_values_and_blocks_mixing_reports(self):
        suffix = "02"
        disp, _, _, batch_key, _, _ = _build_batches(suffix)
        # 没有已采用报告的批次不能复核
        with pytest.raises(ValueError):
            disp.open_review("NOT-A-BATCH")
        task = disp.open_review(batch_key)
        assert task["采用质量报告"] == f"QR-E2E-{suffix}"
        assert {p["点位编号"] for p in task["frozen_points"]} == {"P-1001", "P-1002"}
        # 未保存时不能再开第二个复核任务
        with pytest.raises(ValueError):
            disp.open_review(batch_key)

    def test_correction_during_open_review_is_held_not_applied(self):
        suffix = "03"
        disp, _, _, batch_key, _, _ = _build_batches(suffix)
        task = disp.open_review(batch_key)
        original_frozen = json.loads(json.dumps(task["frozen_points"]))

        # 复核未保存期间到达、已独立确认的更正
        correction = disp.submit_correction(
            batch_key, "P-1001", 39.9030, 116.4095, independently_confirmed=True)
        assert correction["并入状态"] == "待并入"
        assert correction["复核未保存到达"] == "是"

        # 冻结基准未被改写
        assert task["frozen_points"] == original_frozen
        point = disp.find_point(batch_key, "P-1001")
        assert point["标准坐标纬度"] == 39.9044  # 原已确认值保持不变
        assert point["更正状态"] == "待并入更正"

        # 未经独立确认的更正拒绝登记
        with pytest.raises(ValueError):
            disp.submit_correction(batch_key, "P-1001", 39.9, 116.4, independently_confirmed=False)

        # 保存复核：结论基于冻结值，更正继续挂起并进入下一次待复核范围
        saved = disp.save_review(task["id"])
        assert saved["status"] == "已保存"
        scope = disp.next_review_scope(batch_key)
        assert scope["待复核点数"] == 1
        assert scope["范围"][0]["点位编号"] == "P-1001"
        assert scope["范围"][0]["待并入更正坐标"] == [39.9030, 116.4095]

    def test_standardization_carries_report_and_rule_and_rejects_mix(self):
        suffix = "04"
        _, _, coord, batch_key, r_inside, r_outside = _build_batches(suffix)
        # 未生效规则不能正式标准化：构造一条草稿规则
        from app.store import store
        draft_id = max(int(r["id"]) for r in store.rows("coord_transform")) + 1
        store.rows("coord_transform").append({
            "id": draft_id, "status": "草稿", "pending": True, "abnormal": False,
            "规则编号": "COOR-DRAFT", "源坐标系": "WGS84", "目标坐标系": "GCJ02",
            "偏移参数": "0,0", "精度位数": "6", "越界处理": "clamp",
            "规则版本": "draft", "生效批次": "",
        })
        with pytest.raises(ValueError):
            coord.standardize_batch(batch_key, draft_id)

        summary = coord.standardize_batch(batch_key, r_inside)
        assert summary["quality_report"] == f"QR-E2E-{suffix}"
        assert summary["rule_code"] == "COOR-R1001"
        points, _ = coord.get_standardized_points(batch_key)
        for record in points:
            assert record["quality_report"] == f"QR-E2E-{suffix}"
            assert record["rule_code"] == "COOR-R1001"
            assert record["rule_version"] == "v内侧-1"

        # 同一批次再采用第二份报告即构成混用，必须拒绝
        from app.services.geo_quality import GeoQualityService
        quality = GeoQualityService()
        second_id = max(int(r["id"]) for r in store.rows("geo_quality")) + 1
        store.rows("geo_quality").append({
            "id": second_id, "status": "已出报告", "pending": False, "abnormal": False,
            "报告编号": f"QR-E2E-{suffix}-B", "批次编号": batch_key, "漂移点数": "1",
            "重复点数": "0", "缺失率": "0.0%", "异常时段": "2026-11-21",
            "校验规则": "最大速度50m/s", "质检结论": "待复核",
        })
        with pytest.raises(ValueError):
            quality.adopt_report(second_id)

        # 已出批前重复正式标准化默认拒绝重写
        coord.issue_batch(batch_key)
        with pytest.raises(ValueError):
            coord.standardize_batch(batch_key, r_inside)

    def test_confirmed_rule_inside_trial_rule_outside(self):
        suffix = "05"
        _, _, coord, batch_key, r_inside, r_outside = _build_batches(suffix)
        from app.services.displacement_review import DisplacementReviewService
        disp = DisplacementReviewService()
        road = disp._road_for(batch_key)

        coord.standardize_batch(batch_key, r_inside)
        issued, _ = coord.get_standardized_points(batch_key)
        assert all(
            disp.classify_side(r["trans_lat"], r["trans_lon"], road) == "内侧"
            for r in issued
        )

        trial = coord.trial_transform(batch_key, r_outside)
        trial_path = Path(trial["output_file"])
        trial_points = [json.loads(line) for line in trial_path.read_text().splitlines()]
        assert all(
            disp.classify_side(r["trans_lat"], r["trans_lon"], road) == "外侧"
            for r in trial_points
        )
        # 试算不触碰正式产物
        formal, _ = coord.get_standardized_points(batch_key)
        assert all(r["rule_code"] == "COOR-R1001" for r in formal)

    def test_recompute_uses_original_basis_and_remains_reproducible(self):
        suffix = "06"
        disp, _, coord, batch_key, r_inside, _ = _build_batches(suffix)
        coord.standardize_batch(batch_key, r_inside)
        coord.issue_batch(batch_key)

        # 出批后再到的现场更正不能重写历史
        disp.submit_correction(batch_key, "P-1002", 39.9025, 116.4100,
                               source="事后现场判定", independently_confirmed=True)
        result = coord.recompute_standardization(batch_key)
        assert result["reproducible"] is True
        assert result["mismatches"] == []
        assert result["quality_report"] == f"QR-E2E-{suffix}"
        assert result["rule_version"] == "v内侧-1"

    def test_issue_and_downstream_side_verification_with_next_scope(self):
        suffix = "07"
        disp, _, coord, batch_key, r_inside, _ = _build_batches(suffix)
        # 复核未保存时提交更正，随后完成出批
        task = disp.open_review(batch_key)
        disp.submit_correction(batch_key, "P-1001", 39.9030, 116.4095,
                               independently_confirmed=True)
        disp.save_review(task["id"])

        coord.standardize_batch(batch_key, r_inside)
        issue = coord.issue_batch(batch_key)
        assert issue["side_summary"].get("内侧") == 2

        verification = coord.verify_downstream_side(batch_key)
        assert {item["side"] for item in verification["逐点侧别"]} == {"内侧"}
        assert verification["出批报告"] == f"QR-E2E-{suffix}"
        assert verification["出批规则"] == "COOR-R1001"
        next_scope = verification["下一次待复核范围"]
        assert next_scope["待复核点数"] == 1
        assert next_scope["范围"][0]["点位编号"] == "P-1001"
        assert next_scope["范围"][0]["独立确认"] == "是"

    def test_quality_recheck_uses_adopted_report_values(self):
        suffix = "08"
        disp, quality, _, batch_key, _, _ = _build_batches(suffix)
        task = quality.open_recheck(batch_key)
        assert task["采用质量报告"] == f"QR-E2E-{suffix}"
        assert all(p["基准侧别"] == "内侧" for p in task["frozen_points"])
