"""材料批次台账：批次与工序/规格的对应、领用与退回、备料清单与开工资格。

设计约定（材料核验与工序编排共用同一套口径）：

* 一个材料核验单就是一批材料，批次身份由「对应工序 + 材料名称 + 规格型号 + 送检批次」
  共同确定。没有填写「对应工序」的旧数据按材料名称在工序名称/施工区域中出现来兼容。
* 每批材料维护一本台账：进场数量、已领数量、待领数量、已退回数量、去向凭据。
  领用只减少待领数量；退回（仓管撤回签认）只从「未领用资格」里撤回剩余待领量，
  已经领用的份额保留在账上，并保留去向凭据。
* 工序的「备料清单」由与该工序匹配的材料批次实时生成。备料清单里的「核对通过」
  与开工审批使用同一个判定函数，避免一处显示已验证、另一处又不认。
"""
from __future__ import annotations

import re
import time
from typing import Any

from app.store import store

SIGNED = "已签认"
RETURNED = "已退回"
VOIDED = "废弃"
LEDGER_FIELDS = ("批次数量", "已领数量", "待领数量", "已退回数量", "证明状态")


# ── 基础解析 ──
def parse_quantity(value: Any) -> float | None:
    """从「12」「12 袋」「12.0袋」这类文本里取出数量。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def format_quantity(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


# ── 批次字段 ──
def batch_no(entry: dict[str, Any]) -> str:
    for field in ("送检批次", "批次号", "批次"):
        value = entry.get(field)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def material_name(entry: dict[str, Any]) -> str:
    for field in ("材料名称", "材料"):
        value = entry.get(field)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def material_spec(entry: dict[str, Any]) -> str:
    for field in ("规格型号", "规格"):
        value = entry.get(field)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def batch_process(entry: dict[str, Any]) -> str:
    for field in ("对应工序", "使用工序", "关联工序", "工序名称", "工序"):
        value = entry.get(field)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def batch_total(entry: dict[str, Any]) -> float | None:
    for field in ("进场数量", "送检数量", "批次数量", "数量"):
        value = parse_quantity(entry.get(field))
        if value is not None:
            return value
    return None


def batch_label(entry: dict[str, Any]) -> str:
    parts = [material_name(entry), material_spec(entry), batch_no(entry)]
    return "/".join(part for part in parts if part) or f"材料核验单#{entry.get('id')}"


# ── 台账初始化与状态 ──
def _first_value(entry: dict[str, Any], fields: tuple[str, ...]) -> Any:
    for field in fields:
        value = entry.get(field)
        if value not in (None, ""):
            return value
    return None


ISSUED_FIELDS = ("已领数量", "已领用数量", "已领量", "已领取数量", "领用数量")
RETURNED_FIELDS = ("已退回数量", "退回数量", "退回量", "已退回量")


def ensure_ledger(entry: dict[str, Any]) -> dict[str, Any]:
    """补齐台账字段；数量相关字段按「已领 + 待领 + 已退回 = 进场」守恒。"""
    total = batch_total(entry)
    if total is not None:
        if not entry.get("批次数量"):
            entry["批次数量"] = total
    issued = parse_quantity(_first_value(entry, ISSUED_FIELDS))
    returned = parse_quantity(_first_value(entry, RETURNED_FIELDS))
    entry["已领数量"] = issued or 0.0
    entry["已退回数量"] = returned or 0.0
    if total is None:
        entry.setdefault("待领数量", None)
    else:
        pending = total - float(entry["已领数量"]) - float(entry["已退回数量"])
        entry["待领数量"] = max(pending, 0.0)
    entry.setdefault("证明状态", "有效")
    entry.setdefault("领用记录", [])
    entry.setdefault("去向凭据", [])
    # 只读别名，方便页面和外部查询用不同叫法读到同一本账。
    entry["已领用量"] = entry["已领数量"]
    entry["待领用量"] = entry["待领数量"]
    entry["退回数量"] = entry["已退回数量"]
    entry["待领量"] = entry["待领数量"]
    return entry


def proof_valid(entry: dict[str, Any]) -> bool:
    """签认证明是否仍然有效（已签认且没有被仓管撤回）。"""
    return entry.get("status") == SIGNED and entry.get("证明状态") != VOIDED


def batch_state(entry: dict[str, Any]) -> str:
    if entry.get("证明状态") == VOIDED or entry.get("status") == RETURNED:
        return RETURNED
    if entry.get("status") == SIGNED:
        return SIGNED
    return str(entry.get("status") or "未签认")


# ── 批次与工序的对应 ──
def matches_work_sequence(entry: dict[str, Any], ws_row: dict[str, Any]) -> bool:
    """批次只对应自己的工序与材料规格，不能再把别的工序一并标记为已验证。"""
    ws_name = str(ws_row.get("工序名称") or "").strip()
    area = str(ws_row.get("施工区域") or "").strip()
    process = batch_process(entry)
    name = material_name(entry)
    spec = material_spec(entry)
    if process:
        if process == ws_name:
            return True
        # 允许批次填的是工序的简称，但只在唯一命中时成立，避免把同区域的其它工序一起标记。
        if ws_name and process in ws_name:
            same_prefix = [
                row for row in store.rows("work_sequence")
                if process in str(row.get("工序名称") or "")
            ]
            return len(same_prefix) == 1
        return False
    if not name:
        return False
    required = required_materials(ws_row)
    if required:
        # 工序声明了所需材料时，只认材料清单，避免「东廊」这类区域字眼误配。
        return any(required_material_matches(item, name, spec) for item in required)
    # 兼容没有需求清单的老数据：用工序名称/施工区域里的材料名兜底。
    return name in ws_name or (bool(area) and name in area)


def _work_sequence_for_process(process: str) -> dict[str, Any] | None:
    for ws_row in store.rows("work_sequence"):
        if str(ws_row.get("工序名称") or "") == process:
            return ws_row
    return None


def batch_identity(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    """批次身份：对应工序 + 材料名称 + 规格型号 + 送检批次。"""
    return (
        batch_process(entry),
        material_name(entry),
        material_spec(entry),
        batch_no(entry),
    )


def batch_conflict(entry: dict[str, Any], exclude_id: int | None = None) -> dict[str, Any] | None:
    """检查同一批次号是否已经对应到别的工序或材料规格。

    旧数据可能没有填写「对应工序」，这种情况不当作冲突，保持兼容。
    """
    number = batch_no(entry)
    name = material_name(entry)
    if not number or not name:
        return None
    process = batch_process(entry)
    spec = material_spec(entry)
    for other in store.rows("material_verify"):
        if other is entry:
            continue
        if exclude_id is not None and int(other.get("id") or 0) == exclude_id:
            continue
        if batch_no(other) != number or material_name(other) != name:
            continue
        other_process = batch_process(other)
        other_spec = material_spec(other)
        if process and other_process and process != other_process:
            return other
        if spec and other_spec and spec != other_spec:
            return other
    return None


# ── 签认 / 领用 / 撤回 ──
def sign_batch(entry: dict[str, Any]) -> None:
    """签认材料：证明恢复有效，但不会把已经退回的份额重新变成待领量。"""
    ensure_ledger(entry)
    entry["证明状态"] = "有效"
    total = batch_total(entry)
    if total is not None:
        entry["待领数量"] = max(
            total - float(entry.get("已领数量") or 0) - float(entry.get("已退回数量") or 0),
            0.0,
        )
    entry["签认证明"] = {
        "证明编号": f"QR-{entry.get('id')}",
        "材料名称": material_name(entry),
        "规格型号": material_spec(entry),
        "批次": batch_no(entry),
        "对应工序": batch_process(entry),
        "签认时间": _now(),
        "状态": "有效",
    }


def validate_issue(
    entry: dict[str, Any],
    quantity: float | None,
    *,
    process: str = "",
    spec: str = "",
) -> None:
    """校验一次领用是否成立，供动作前置校验和实际入账共用同一套规则。"""
    ensure_ledger(entry)
    if quantity is None or quantity <= 0:
        raise ValueError("领用材料必须填写大于 0 的领用数量")
    if not proof_valid(entry):
        raise ValueError(f"批次「{batch_label(entry)}」的签认证明已{batch_state(entry)}，不能领用")
    if spec and material_spec(entry) and spec != material_spec(entry):
        raise ValueError(
            f"批次「{batch_label(entry)}」的规格为「{material_spec(entry)}」，与领用规格「{spec}」不一致"
        )
    explicit_process = batch_process(entry)
    if process:
        if explicit_process and not matches_work_sequence(entry, {"工序名称": process}):
            raise ValueError(
                f"批次「{batch_label(entry)}」对应工序为「{explicit_process}」，不能领用到「{process}」"
            )
        ws_row = _work_sequence_for_process(process)
        if explicit_process and ws_row is not None and not matches_work_sequence(entry, ws_row):
            raise ValueError(
                f"批次「{batch_label(entry)}」对应工序为「{explicit_process}」，不能领用到「{process}」"
            )
    pending = parse_quantity(entry.get("待领数量"))
    if pending is not None and quantity > pending:
        raise ValueError(
            f"批次「{batch_label(entry)}」待领数量为 {format_quantity(pending)}，"
            f"不足本次领用 {format_quantity(quantity)}"
        )


def issue_batch(
    entry: dict[str, Any],
    quantity: float,
    *,
    craftsman: str = "工匠",
    process: str = "",
    voucher_no: str = "",
    spec: str = "",
) -> dict[str, Any]:
    """工匠领用：批次、工序、材料规格必须对得上，数量不能超过待领量。"""
    validate_issue(entry, quantity, process=process, spec=spec)

    explicit_process = batch_process(entry)
    if not process:
        process = explicit_process
        if not process:
            matched = [
                ws_row for ws_row in store.rows("work_sequence")
                if matches_work_sequence(entry, ws_row)
            ]
            process = str(matched[0].get("工序名称") or "") if matched else ""

    voucher_no = voucher_no or f"LY-{entry.get('id')}-{len(entry.get('领用记录', [])) + 1}"
    existing = [
        record for record in entry.get("领用记录", [])
        if voucher_no and record.get("领用单号") == voucher_no
    ]
    if existing:
        # 同一个领用单号重复提交只入账一次。
        return existing[0]

    proof = entry.get("签认证明")
    destination = f"用于工序「{process or '未指定'}」"
    record = {
        "领用单号": voucher_no,
        "材料名称": material_name(entry),
        "规格型号": material_spec(entry),
        "批次": batch_no(entry),
        "对应工序": process,
        "数量": quantity,
        "领用人": craftsman,
        "领用时间": _now(),
        "有效性": "成立",
        "去向凭据": destination,
        # 领用发生时该批次持有的签认证明：事后仓管撤回签认也只影响未领用部分。
        "签认证明": (proof.get("证明编号") if isinstance(proof, dict) else "")
        or f"材料核验单#{entry.get('id')}",
    }
    entry["领用记录"].append(record)
    entry["去向凭据"].append({
        "领用单号": voucher_no,
        "数量": quantity,
        "用于工序": process,
        "领用人": craftsman,
        "时间": record["领用时间"],
    })
    entry["已领数量"] = float(entry.get("已领数量") or 0) + quantity
    if entry.get("待领数量") is not None:
        entry["待领数量"] = max(float(entry["待领数量"]) - quantity, 0.0)
    return record


def withdraw_batch(entry: dict[str, Any]) -> float:
    """仓管撤回/退回该批材料的签认。

    只从「未领用的资格」里撤回剩余待领量；已领用的份额原样留在账上，领用记录
    仍判定为成立并保留去向凭据。重复撤回不会重复扣减待领量。
    """
    ensure_ledger(entry)
    if entry.get("证明状态") == VOIDED:
        entry["待领数量"] = 0.0 if entry.get("待领数量") is not None else None
        for record in entry.get("领用记录", []):
            record.setdefault("有效性", "成立")
            record.setdefault("撤回后核验", "领用发生在撤回前，去向凭据保留")
        return 0.0

    pending = parse_quantity(entry.get("待领数量")) or 0.0
    entry["已退回数量"] = float(entry.get("已退回数量") or 0) + pending
    if entry.get("待领数量") is not None:
        entry["待领数量"] = 0.0
    entry["证明状态"] = VOIDED
    entry["撤回待领数量"] = pending
    if isinstance(entry.get("签认证明"), dict):
        entry["签认证明"] = {
            **entry["签认证明"],
            "状态": VOIDED,
            "废弃时间": _now(),
            "废弃原因": "仓管撤回本批材料签认",
        }

    # 已经发生的领用不受撤回影响：领用成立，去向凭据保留。
    conflicts = []
    for record in entry.get("领用记录", []):
        if record.get("有效性", "成立") != "成立":
            continue
        record["有效性"] = "成立"
        record["撤回后核验"] = "领用发生在撤回前，去向凭据保留"
        if parse_quantity(record.get("数量")):
            record["冲突"] = "仓管撤回签认时该批材料已有领用，领用仍然成立"
            conflicts.append(record.get("领用单号"))
    if conflicts:
        entry["撤回冲突"] = {
            "发生": True,
            "领用单号": conflicts,
            "处理": "已领份额保留，去向凭据保留；未领用的剩余量已撤回",
        }
    return pending


# ── 备料清单与开工审批（同一口径） ──
def required_materials(ws_row: dict[str, Any]) -> list[str]:
    """工序声明的所需材料；兼容「所需材料」「材料清单」几种写法。"""
    raw = None
    for field in ("所需材料", "材料清单", "需求材料"):
        if ws_row.get(field) not in (None, ""):
            raw = ws_row.get(field)
            break
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        items: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                name = str(item.get("材料名称") or item.get("材料") or "").strip()
                spec = str(item.get("规格型号") or item.get("规格") or "").strip()
                if name or spec:
                    items.append(f"{name} {spec}".strip())
                continue
            items.append(str(item))
    else:
        text = str(raw)
        items = [part for part in re.split(r"[、,，;；/|]+", text) if part.strip()]
    return [item.strip() for item in items if item.strip()]


def _split_required(item: str) -> tuple[str, str]:
    """把「石灰 袋装25kg」拆成材料名和规格；没有规格时第二段为空。"""
    parts = [part for part in re.split(r"\s+", item.strip()) if part]
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def required_material_matches(item: str, name: str, spec: str) -> bool:
    """工序所需材料与批次的对应：材料名必须一致，声明了规格时规格也要一致。"""
    required_name, required_spec = _split_required(item)
    if not required_name:
        return False
    if not name:
        return bool(required_spec) and required_spec in spec
    if required_name != name and required_name not in name and name not in required_name:
        if not (required_spec and required_spec in spec):
            return False
    if not required_spec:
        return True
    if not spec:
        return False
    return required_spec == spec or required_spec in spec or spec in required_spec


def issued_quantity(entry: dict[str, Any]) -> float:
    """实际领用数量：有领用单时只认成立且仍有凭据的领用记录。"""
    records = entry.get("领用记录") or []
    if records:
        return sum(
            float(parse_quantity(record.get("数量")) or 0)
            for record in records
            if record.get("有效性", "成立") == "成立"
        )
    # 兼容没有领用单、只有台账数量的历史数据。
    return float(parse_quantity(entry.get("已领数量")) or 0)


def qualification(entry: dict[str, Any]) -> bool:
    """核验口径（备料清单与开工审批共用）。

    * 签认已退回/废弃：退回的剩余量一律不算核验通过。
    * 有领用记录：必须有成立的实际领用，才判通过。
    * 没有领用记录（老数据）：只按当前签认证明是否有效判断，兼容既有材料签认。
    """
    ensure_ledger(entry)
    if not proof_valid(entry):
        return False
    if batch_total(entry) is None:
        # 没有数量口径的旧签认：证明有效即通过。
        return True
    return issued_quantity(entry) > 0


def _preparation_entry(entry: dict[str, Any]) -> dict[str, Any]:
    ensure_ledger(entry)
    total = batch_total(entry)
    issued = issued_quantity(entry)
    pending = parse_quantity(entry.get("待领数量"))
    returned = float(entry.get("已退回数量") or 0)
    valid = proof_valid(entry)
    state = batch_state(entry)
    proof = entry.get("签认证明")

    checked = qualification(entry)
    return {
        "材料名称": material_name(entry),
        "材料": material_name(entry),
        "规格型号": material_spec(entry),
        "规格": material_spec(entry),
        "批次": batch_no(entry),
        "对应工序": batch_process(entry),
        "工序": batch_process(entry),
        "核验单": entry.get("id"),
        "应备数量": total or 0.0,
        "批次数量": total or 0.0,
        "已领数量": issued,
        "已领": issued,
        "有效领用数量": issued,
        "待领数量": pending if pending is not None else 0.0,
        "待领": pending if pending is not None else 0.0,
        "已退回数量": returned,
        "退回": returned,
        "签认状态": state,
        "状态": state,
        "签认证明": (proof.get("证明编号") if isinstance(proof, dict) else "") or (
            f"材料核验单#{entry.get('id')}" if valid else ""
        ),
        "证明有效": valid,
        "核对通过": checked,
        "领用记录": [dict(record) for record in entry.get("领用记录", [])],
        "去向凭据": [dict(item) for item in entry.get("去向凭据", [])],
    }


def preparation_list(ws_row: dict[str, Any]) -> list[dict[str, Any]]:
    entries = [
        _preparation_entry(entry)
        for entry in store.rows("material_verify")
        if matches_work_sequence(entry, ws_row)
    ]
    entries.sort(key=lambda item: int(item.get("核验单") or 0))
    return entries


def missing_materials(ws_row: dict[str, Any], entries: list[dict[str, Any]]) -> list[str]:
    """工序声明了所需材料、却没有任何批次对应的，也要拦住开工。"""
    missing = []
    for required in required_materials(ws_row):
        matched = any(
            required_material_matches(required, item["材料名称"], item["规格型号"])
            for item in entries
        )
        if not matched:
            missing.append(required)
    return missing


def refresh_work_sequence(ws_row: dict[str, Any]) -> dict[str, Any]:
    """把材料批次台账实时写回工序编排的备料清单。"""
    entries = preparation_list(ws_row)
    missing = missing_materials(ws_row, entries)
    ws_row["备料清单"] = entries
    ws_row["缺失材料"] = missing
    ws_row["需要材料已验证"] = bool(entries) and all(item["证明有效"] for item in entries)
    ws_row["材料核对通过"] = (
        not missing and all(item["核对通过"] for item in entries)
    )
    ws_row["开工材料条件"] = "通过" if ws_row["材料核对通过"] else "不通过"

    if not entries:
        ws_row["备料状态"] = "缺少材料批次" if missing else "无需备料"
    elif missing:
        ws_row["备料状态"] = "缺少材料批次"
    elif any(item["签认状态"] == RETURNED for item in entries):
        ws_row["备料状态"] = "已退回"
    elif all(item["核对通过"] for item in entries):
        ws_row["备料状态"] = "已备料"
    elif any(item["签认状态"] == SIGNED for item in entries):
        ws_row["备料状态"] = "待领用"
    else:
        ws_row["备料状态"] = "待核验"
    return ws_row


def refresh_all_work_sequences() -> None:
    for ws_row in store.rows("work_sequence"):
        refresh_work_sequence(ws_row)


def material_start_reasons(ws_row: dict[str, Any]) -> list[str]:
    """开工审批的材料条件：签认有效 + 实际领用；退回的剩余量不算核验通过。"""
    refresh_work_sequence(ws_row)
    reasons: list[str] = []
    for required in ws_row.get("缺失材料", []):
        reasons.append(f"工序所需材料「{required}」没有对应的核验批次，不能开工")
    for item in ws_row.get("备料清单", []):
        state = item["签认状态"]
        if state == RETURNED:
            reasons.append(
                f"材料批次「{item['批次'] or item['材料名称']}」签认已退回，"
                f"退回的待领量不作核验通过，已领 {format_quantity(item['已领数量'])} 仅保留去向凭据"
            )
        elif not item["证明有效"]:
            reasons.append(f"材料批次「{item['批次'] or item['材料名称']}」签认状态为「{state}」，不能开工")
        elif not item["核对通过"]:
            reasons.append(
                f"材料批次「{item['批次'] or item['材料名称']}」尚无实际领用记录，不能仅凭签认开工"
            )
    return reasons


def requisition_records(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """重新进入备料时查询领用是否成立（撤回签认后仍可查到历史领用）。"""
    ensure_ledger(entry)
    records = []
    for record in entry.get("领用记录", []):
        item = {
            **record,
            "成立": record.get("有效性", "成立") == "成立",
            "批次当前签认状态": batch_state(entry),
            "可用资格": batch_state(entry) == SIGNED
            and parse_quantity(record.get("数量")) is not None,
        }
        if entry.get("证明状态") == VOIDED:
            item["撤回冲突"] = "签认已撤回，但这笔领用发生在撤回之前，成立且保留去向凭据"
        records.append(item)
    return records
