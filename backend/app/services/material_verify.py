"""材料核验领域服务：核验材料时校验已通过方案中的工艺要求，签认后联动工序可用性。

材料核验单同时是一批材料的台账：签认/退回只影响与该批次对应的工序，
退回结果实时写回工序编排的备料清单；已领用的份额保留去向凭据。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.material_ledger import (
    batch_label,
    batch_process,
    batch_conflict,
    ensure_ledger,
    issue_batch,
    material_spec,
    parse_quantity,
    refresh_all_work_sequences,
    requisition_records,
    sign_batch,
    validate_issue,
    withdraw_batch,
)
from app.store import store


class MaterialVerifyService(BaseModuleService):
    MODULE = "material_verify"
    ENTITY = "材料核验单"
    OPERATION_TERM = "开始核验"
    LIST_FIELDS = ["材料名称", "规格型号", "供应商", "送检批次", "对应工序", "进场数量", "批次数量", "送检数量", "已领数量", "待领数量", "已退回数量", "检测报告", "核验人", "核验结果", "备注说明"]
    REQUIRED_FIELDS = ["材料名称", "规格型号", "供应商"]
    STATUS_ORDER = ["待核验", "核验中", "已签认", "已退回"]
    ACTION_RULES = {
        "开始核验": "核验中",
        "签认材料": "已签认",
        "退回材料": "已退回",
        "领用材料": None,
        "撤回签认": "已退回",
        "废弃证明": "已退回",
    }
    # 业务口头叫法与动作名对齐：同一件事只走一套台账逻辑。
    ACTION_ALIASES = {
        "签认": "签认材料",
        "领用": "领用材料",
        "发料": "领用材料",
        "退回": "退回材料",
        "退料": "退回材料",
        "撤回": "撤回签认",
        "撤回材料": "撤回签认",
        "撤回证明": "撤回签认",
        "废弃": "废弃证明",
    }
    NEGATIVE_ACTIONS = ["退回材料", "撤回签认", "废弃证明"]
    # 领用与撤回都写台账，由台账自身保证重复调用不重复扣减。
    NON_IDEMPOTENT_ACTIONS = ["领用材料", "退回材料", "撤回签认", "废弃证明"]

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        canonical = self.ACTION_ALIASES.get(action.strip(), action.strip())
        return super().run_action(entry_id, canonical, values=values)

    # ── 批次台账 ──
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        entry, missing = super().create_entry(values)
        if entry is not None:
            # 批次台账字段不在展示列里时也保留下来，避免批次身份丢失。
            for key, value in values.items():
                if key.startswith("_"):
                    continue
                entry.setdefault(key, value)
            ensure_ledger(entry)
        return entry, missing

    @staticmethod
    def _action_values(entry: dict[str, Any]) -> dict[str, Any]:
        return entry.get("_action_values") or {}

    def list_entries(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        # 列表页也要看到批次台账口径，老数据在读取时补账，不改写历史签认。
        for row in store.rows(self.MODULE):
            ensure_ledger(row)
        return super().list_entries(**kwargs)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = super().get_entry(entry_id)
        if entry is not None:
            ensure_ledger(entry)
        return entry

    def requisition_status(self, entry_id: int) -> list[dict[str, Any]]:
        """重新进入备料时，查询该批次历史领用是否成立（撤回签认后仍可查）。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return []
        return requisition_records(entry)

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["batches"] = [ensure_ledger(dict(row)) for row in store.rows(self.MODULE)]
        return base

    def _matched_work_sequences(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        from app.services.material_ledger import matches_work_sequence

        return [
            ws_row for ws_row in store.rows("work_sequence")
            if matches_work_sequence(entry, ws_row)
        ]

    # ── 跨模块校验 ──
    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "签认材料":
            # 校验该材料是否在任一已通过方案的工艺要求中出现
            approved_plans = [
                row for row in store.rows("restoration_plan") if row.get("status") == "已通过"
            ]
            if approved_plans:
                material_name = entry.get("材料名称", "")
                spec = entry.get("规格型号", "")
                matched = False
                mismatch_reasons = []
                for plan in approved_plans:
                    craft = plan.get("修缮工艺", "")
                    if material_name in craft or (spec and spec in plan.get("材料清单", "")):
                        matched = True
                        break
                    mismatch_reasons.append(
                        f"方案{plan.get('方案编号','')}工艺要求: {craft}"
                    )
                if not matched:
                    details = "; ".join(mismatch_reasons[:3])
                    return False, f"材料「{material_name}」未在任何已通过修缮方案的工艺中出现。{details}"
            # 同一批次号必须始终对应同一个工序和同一个材料规格。
            conflict = batch_conflict(entry, exclude_id=entry_id)
            if conflict is not None:
                return False, (
                    f"批次「{batch_label(entry)}」已对应工序「{batch_process(conflict)}」/"
                    f"规格「{material_spec(conflict)}」，同一批次不能再对应其它工序或规格"
                )
        if action in ("领用材料", "领用"):
            ensure_ledger(entry)
            params = self._action_values(entry)
            quantity = parse_quantity(
                params.get("领用数量") or entry.get("_领用数量") or entry.get("领用数量")
            )
            try:
                validate_issue(
                    entry,
                    quantity,
                    process=str(params.get("领用工序") or ""),
                    spec=str(params.get("规格型号") or ""),
                )
            except ValueError as exc:
                return False, str(exc)
        if action in ("撤回签认", "废弃证明"):
            ensure_ledger(entry)
            if entry.get("status") not in ("已签认", "已退回"):
                return False, f"材料核验单当前状态为「{entry.get('status', '')}」，还没有可撤回的签认"
            # 与工匠领用发生冲突不阻止撤回，但要把冲突和领用是否成立记录下来，
            # 供重新进入备料时查询。
        return True, ""

    # ── 跨模块联动 ──
    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> list[dict]:
        effects: list[dict] = []
        if action == "签认材料":
            sign_batch(entry)
            refresh_all_work_sequences()
            effects.extend(
                dict(ws_row) for ws_row in self._matched_work_sequences(entry)
            )
        elif action in ("退回材料", "撤回签认", "废弃证明"):
            # 只撤回未领用的资格；已领用份额保留去向凭据。重复撤回只扣一次待领量。
            withdrawal = withdraw_batch(entry)
            entry["退回待领数量"] = withdrawal
            entry["领用核验"] = requisition_records(entry)
            # 签认被撤回后，允许重新签认（幂等窗口不应挡住合法重签）。
            store.clear_idempotent(self.MODULE, entry_id, "签认材料")
            refresh_all_work_sequences()
            effects.extend(
                dict(ws_row) for ws_row in self._matched_work_sequences(entry)
            )
        elif action in ("领用材料", "领用"):
            params = self._action_values(entry)
            quantity = parse_quantity(
                params.get("领用数量") or entry.get("_领用数量") or entry.get("领用数量")
            )
            record = issue_batch(
                entry,
                float(quantity or 0),
                craftsman=str(params.get("领用人") or entry.get("_领用人") or "工匠"),
                process=str(params.get("领用工序") or entry.get("_领用工序") or ""),
                voucher_no=str(params.get("领用单号") or entry.get("_领用单号") or ""),
                spec=str(params.get("规格型号") or ""),
            )
            entry["最近领用"] = record
            refresh_all_work_sequences()
            effects.extend(
                dict(ws_row) for ws_row in self._matched_work_sequences(entry)
            )
        return effects
