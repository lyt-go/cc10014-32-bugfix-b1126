"""材料批次台账跨模块测试：核验签认 → 工序备料 → 领用 → 退回 → 开工审批。

覆盖任务里的验收场景：一批 12 袋石灰先领 5 袋再退回，东廊木作始终没拿到
该批材料；重复提交退回只扣一次待领量；账上保留已领的 5 袋。
"""
from __future__ import annotations

import pytest

from app.seed import SEED_ROWS
from app.store import store
from app.services.material_verify import MaterialVerifyService
from app.services.work_sequence import WorkSequenceService


@pytest.fixture(autouse=True)
def reset_store():
    store._idempotent_log.clear()
    store._resource_locks.clear()
    for name in list(store._tables.keys()):
        store._tables[name] = [dict(row) for row in SEED_ROWS.get(name, [])]
    yield


material_service = MaterialVerifyService()
work_service = WorkSequenceService()


def make_plan_for(material: str = "石灰", spec: str = "袋装25kg") -> None:
    plan = store.rows("restoration_plan")[2]
    plan["status"] = "已通过"
    plan["修缮工艺"] = f"{material}抹灰"
    plan["材料清单"] = f"{material} {spec}"


def make_lime_setup(total: str = "12"):
    """西廊石灰 12 袋，东廊椽木是另一个工序，始终不该拿到石灰。"""
    make_plan_for()
    west, _ = work_service.create_entry({
        "工序名称": "西廊墙面", "前置工序": "无", "所需工匠": "泥瓦工",
        "所需材料": "石灰", "施工区域": "西廊",
    })
    east, _ = work_service.create_entry({
        "工序名称": "东廊木作", "前置工序": "无", "所需工匠": "木工",
        "所需材料": "椽木", "施工区域": "东廊",
    })
    batch, missing = material_service.create_entry({
        "材料名称": "石灰", "规格型号": "袋装25kg", "供应商": "古建材料厂",
        "送检批次": "LIME-2026-01", "对应工序": "西廊墙面", "进场数量": total,
    })
    assert missing == []
    return west, east, batch


def sign(batch):
    entry, message = material_service.run_action(batch["id"], "签认材料")
    assert entry is not None, message
    return entry


def issue(batch, quantity, process="西廊墙面"):
    return material_service.run_action(
        batch["id"], "领用材料",
        values={"领用数量": quantity, "领用人": "王师傅", "领用工序": process},
    )


def return_batch(batch):
    return material_service.run_action(batch["id"], "退回材料")


def test_lime_sign_does_not_mark_east_corridor_wood():
    """西廊石灰签认不能把东廊椽木标成材料已验证。"""
    west, east, batch = make_lime_setup()
    sign(batch)

    west_row = store.find("work_sequence", west["id"])
    east_row = store.find("work_sequence", east["id"])
    assert west_row["需要材料已验证"] is True
    assert west_row["备料状态"] == "待领用"
    assert west_row["备料清单"][0]["批次"] == "LIME-2026-01"

    assert east_row["需要材料已验证"] is False
    assert east_row["备料清单"] == []
    assert "LIME-2026-01" not in str(east_row)


def test_material_return_is_written_back_to_preparation_list():
    """材料核验退回要写回工序编排的备料清单，并清掉材料已验证标记。"""
    west, east, batch = make_lime_setup()
    sign(batch)
    issue(batch, "5")
    return_batch(batch)

    west_row = store.find("work_sequence", west["id"])
    item = west_row["备料清单"][0]
    assert west_row["需要材料已验证"] is False
    assert west_row["备料状态"] == "已退回"
    assert item["签认状态"] == "已退回"
    assert item["证明有效"] is False
    assert item["已领数量"] == 5

    assert store.find("work_sequence", east["id"])["备料清单"] == []
    assert store.find("work_sequence", east["id"])["需要材料已验证"] is False


def test_duplicate_return_only_withdraws_pending_once():
    """一批 12 袋先领 5 袋再退回：重复退回只扣一次待领量，已领 5 袋仍在账上。"""
    _, _, batch = make_lime_setup()

    # 未签认不能提前领用
    blocked, blocked_msg = issue(batch, "5")
    assert blocked is None and "签认" in blocked_msg

    sign(batch)
    entry, message = issue(batch, "5")
    assert entry is not None, message
    entry = store.find("material_verify", batch["id"])
    assert (entry["已领数量"], entry["待领数量"], entry["已退回数量"]) == (5, 7, 0)

    first, message = return_batch(batch)
    assert first is not None, message
    entry = store.find("material_verify", batch["id"])
    assert (entry["已领数量"], entry["待领数量"], entry["已退回数量"]) == (5, 0, 7)
    assert entry["退回待领数量"] == 7

    # 重复提交退回请求：不再扣待领量，也不改已领份额。
    second, message = return_batch(batch)
    assert second is not None, message
    entry = store.find("material_verify", batch["id"])
    assert (entry["已领数量"], entry["待领数量"], entry["已退回数量"]) == (5, 0, 7)
    assert entry["退回待领数量"] == 0


def test_returned_remainder_does_not_count_as_verified_for_start():
    """退回的剩余量不能算核验通过，后续开工审批要拦住。"""
    west, _, batch = make_lime_setup()
    sign(batch)

    # 只有签认、没有实际领用，不能开工。
    ok, reasons, _ = work_service.start_eligibility(west["id"])
    assert ok is False and any("实际领用" in reason for reason in reasons)

    issue(batch, "5")
    ok, reasons, _ = work_service.start_eligibility(west["id"])
    assert ok is True and reasons == []

    return_batch(batch)
    ok, reasons, _ = work_service.start_eligibility(west["id"])
    assert ok is False
    assert any("已退回" in reason for reason in reasons)
    assert "退回的待领量不作核验通过" in reasons[0]

    # 开工动作与备料清单口径一致，同样被拦住。
    entry, message = work_service.run_action(west["id"], "开工审批")
    assert entry is None
    assert "已退回" in message


def test_requisition_still_queryable_after_warehouse_withdrawal():
    """仓管撤回签认后，重新进入备料能查到这笔领用是否成立，并保留去向凭据。"""
    _, _, batch = make_lime_setup()
    sign(batch)
    issue(batch, "5", process="西廊墙面")

    entry, message = material_service.run_action(batch["id"], "撤回签认")
    assert entry is not None, message
    row = store.find("material_verify", batch["id"])
    assert row["status"] == "已退回"
    assert row["证明状态"] == "废弃"
    assert row["签认证明"]["状态"] == "废弃"
    assert (row["已领数量"], row["待领数量"], row["已退回数量"]) == (5, 0, 7)
    assert row["撤回冲突"]["发生"] is True
    assert row["撤回冲突"]["领用单号"]

    records = material_service.requisition_status(batch["id"])
    assert len(records) == 1
    record = records[0]
    assert record["成立"] is True
    assert record["批次"] == "LIME-2026-01"
    assert record["数量"] == 5
    assert record["签认证明"]
    assert record["批次当前签认状态"] == "已退回"
    assert "去向凭据" in record
    assert row["去向凭据"][0]["数量"] == 5
    assert row["去向凭据"][0]["用于工序"] == "西廊墙面"

    # 重复撤回不改变账目。
    material_service.run_action(batch["id"], "撤回签认")
    row = store.find("material_verify", batch["id"])
    assert (row["已领数量"], row["待领数量"], row["已退回数量"]) == (5, 0, 7)


def test_withdrawn_proof_cannot_be_reused_and_reissued_keeps_returned_out():
    """废弃证明不能再领用；重新签认也不会把已退回的份额变回待领量。"""
    _, _, batch = make_lime_setup()
    sign(batch)
    issue(batch, "5")
    material_service.run_action(batch["id"], "撤回签认")

    again, message = issue(batch, "1")
    assert again is None
    assert "签认" in message

    reissued, message = material_service.run_action(batch["id"], "签认材料")
    assert reissued is not None, message
    row = store.find("material_verify", batch["id"])
    assert row["证明状态"] == "有效"
    assert (row["已领数量"], row["待领数量"], row["已退回数量"]) == (5, 0, 7)
    # 重新签认后开工恢复，但退回的 7 袋不会被重新算成待领或核验通过。
    west = store.query_by_field("work_sequence", "工序名称", "西廊墙面")[0]
    ok, reasons, _ = work_service.start_eligibility(west["id"])
    assert ok is True and reasons == []


def test_batch_must_match_process_and_spec():
    """同一批次号不能对应到别的工序或别的材料规格；领用也要核对规格。"""
    west, east, batch = make_lime_setup()
    sign(batch)

    # 同一批次号对应东廊木作（不同工序）→ 签认被拒。
    other, missing = material_service.create_entry({
        "材料名称": "石灰", "规格型号": "袋装25kg", "供应商": "古建材料厂",
        "送检批次": "LIME-2026-01", "对应工序": "东廊木作", "进场数量": "12",
    })
    assert missing == []
    entry, message = material_service.run_action(other["id"], "签认材料")
    assert entry is None
    assert "同一批次" in message

    # 规格对不上，不能领用。
    wrong_spec, message = material_service.run_action(
        batch["id"], "领用材料",
        values={"领用数量": "1", "领用工序": "西廊墙面", "规格型号": "散装"},
    )
    assert wrong_spec is None
    assert "规格" in message

    # 数量超过待领量也不行。
    too_many, message = issue(batch, "13")
    assert too_many is None and "不足" in message


def test_legacy_signed_material_is_compatible():
    """没有领用台账的旧签认仍然算材料有效，不会误伤不相关工序。"""
    make_plan_for("石灰", "袋装25kg")
    west, _ = work_service.create_entry({
        "工序名称": "西廊石灰墙面", "前置工序": "无", "所需工匠": "泥瓦工",
        "所需材料": "石灰", "施工区域": "西廊",
    })
    east, _ = work_service.create_entry({
        "工序名称": "东廊椽木", "前置工序": "无", "所需工匠": "木工",
        "所需材料": "椽木", "施工区域": "东廊",
    })
    legacy, missing = material_service.create_entry({
        "材料名称": "石灰", "规格型号": "袋装25kg", "供应商": "老供应商",
        "送检批次": "OLD-01", "对应工序": "西廊石灰墙面",
    })
    assert missing == []
    entry, message = material_service.run_action(legacy["id"], "签认材料")
    assert entry is not None, message

    west_row = store.find("work_sequence", west["id"])
    east_row = store.find("work_sequence", east["id"])
    assert west_row["需要材料已验证"] is True
    # 旧数据没有数量口径时不强求领用记录，但也不能标记到东廊。
    assert west_row["材料核对通过"] is True
    assert east_row["需要材料已验证"] is False
    assert east_row["备料清单"] == []


def test_work_sequence_declared_material_without_batch_blocks_start():
    """工序声明了所需材料却没有任何批次时，备料和开工审批都判不通过。"""
    west, _, _ = make_lime_setup()
    # 西廊的石灰批次对应东廊木作，西廊自身没有批次。
    for entry in store.rows("material_verify"):
        entry["对应工序"] = "东廊木作"
    ok, reasons, row = work_service.start_eligibility(west["id"])
    assert ok is False
    assert any("没有对应的核验批次" in reason for reason in reasons)
    assert row["备料状态"] == "缺少材料批次"
    assert row["材料核对通过"] is False


def test_start_approval_action_and_preparation_agree():
    """开工审批动作与备料清单查询结论一致，且每次按当前台账重新判定。"""
    west, _, batch = make_lime_setup()
    sign(batch)
    issue(batch, "5")

    prep = resource_preparation(west["id"])
    approval, message = work_service.run_action(west["id"], "开工审批")
    assert approval is not None, message
    assert approval["开工审批结果"] == "通过"
    assert prep["材料核对通过"] is True

    # 退回后再次审批：不再因为之前审批通过而放行。
    return_batch(batch)
    again, message = work_service.run_action(west["id"], "开工审批")
    assert again is None
    assert "已退回" in message
    assert resource_preparation(west["id"])["材料核对通过"] is False


def resource_preparation(entry_id: int) -> dict:
    return {
        "材料核对通过": store.find("work_sequence", entry_id)["材料核对通过"],
        "备料状态": store.find("work_sequence", entry_id)["备料状态"],
    }


def test_batch_spec_conflict_is_rejected():
    """同一批次号对应不同材料规格也要拒绝。"""
    _, _, batch = make_lime_setup()
    sign(batch)
    other, missing = material_service.create_entry({
        "材料名称": "石灰", "规格型号": "散装", "供应商": "古建材料厂",
        "送检批次": "LIME-2026-01", "对应工序": "西廊墙面", "进场数量": "12",
    })
    assert missing == []
    entry, message = material_service.run_action(other["id"], "签认材料")
    assert entry is None
    assert "同一批次" in message
