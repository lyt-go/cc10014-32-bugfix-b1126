"""工序编排领域服务：编排修缮工序顺序，发布工序时检查前置工序完成状态、安全交底和工匠到岗。

材料的备料清单与开工审批共用材料核验批次台账的口径：只有签认证明仍然有效、
并且已经发生实际领用的材料，才算该工序的备料核验通过；被退回的剩余待领量
不会重新变成核验通过。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.material_ledger import (
    material_start_reasons,
    refresh_work_sequence,
)
from app.store import store


class WorkSequenceService(BaseModuleService):
    MODULE = "work_sequence"
    ENTITY = "工序卡"
    OPERATION_TERM = "发布工序"
    LIST_FIELDS = ["工序名称", "前置工序", "所需工匠", "所需材料", "施工区域", "预计工时", "编排人", "工序状态", "备料状态", "材料核对通过", "开工材料条件", "备注说明"]
    REQUIRED_FIELDS = ["工序名称", "前置工序", "所需工匠"]
    STATUS_ORDER = ["待排期", "已发布", "施工中", "已完成"]
    ACTION_RULES = {
        "发布工序": "已发布",
        "开工审批": None,
        "开始施工": "施工中",
        "完成工序": "已完成",
    }
    NEGATIVE_ACTIONS = []
    # 开工审批是即时判定，每次都按当前台账重新核验，不落幂等缓存。
    NON_IDEMPOTENT_ACTIONS = ["开工审批"]

    # ── 备料清单 ──
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        entry, missing = super().create_entry(values)
        if entry is not None:
            for key, value in values.items():
                if key.startswith("_"):
                    continue
                entry.setdefault(key, value)
            refresh_work_sequence(entry)
        return entry, missing

    def preparation(self, entry_id: int) -> list[dict[str, Any]]:
        """返回工序的备料清单（实时从材料批次台账生成）。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return []
        refresh_work_sequence(entry)
        return [dict(item) for item in entry.get("备料清单", [])]

    def list_entries(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        rows, total = super().list_entries(**kwargs)
        for row in rows:
            refresh_work_sequence(row)
        return rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = super().get_entry(entry_id)
        if entry is not None:
            refresh_work_sequence(entry)
        return entry

    def start_eligibility(self, entry_id: int) -> tuple[bool, list[str], dict[str, Any] | None]:
        """开工审批判定：备料清单与开工审批使用同一个核验口径。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return False, [f"{self.ENTITY} {entry_id} 不存在或已归档"], None
        reasons = material_start_reasons(entry)
        return not reasons, reasons, entry

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        # 返回工序依赖图
        all_rows = store.rows(self.MODULE)
        base["dependency_graph"] = [
            {"工序": r.get("工序名称"), "前置": r.get("前置工序"), "状态": r.get("status")}
            for r in all_rows
        ]
        base["completed"] = [r.get("工序名称") for r in all_rows if r.get("status") == "已完成"]
        base["preparation"] = [
            {"工序": r.get("工序名称"), "备料状态": r.get("备料状态"),
             "材料核对通过": r.get("材料核对通过"), "备料清单": r.get("备料清单", [])}
            for r in all_rows
        ]
        return base

    # ── 跨模块校验 ──
    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "发布工序":
            # 1. 检查前置工序是否已完成
            prereq = entry.get("前置工序", "")
            if prereq and prereq != "无":
                matched = False
                for row in store.rows("work_sequence"):
                    if row.get("工序名称", "") == prereq and row.get("id") != entry_id:
                        matched = True
                        if row.get("status") != "已完成":
                            return False, f"前置工序「{prereq}」尚未完成，当前状态「{row.get('status', '')}」"
                        break
                if not matched and prereq != "无":
                    return False, f"前置工序「{prereq}」在系统中不存在"
            # 2. 检查关联安全交底是否已签认
            process_name = entry.get("工序名称", "")
            safety_records = store.query_by_field("safety_briefing", "交底工序", process_name)
            if not safety_records:
                return False, f"工序「{process_name}」缺少安全交底记录"
            unsigned = [r for r in safety_records if r.get("status") != "已签认"]
            if unsigned:
                return False, f"安全交底未完全签认，尚有{len(unsigned)}条待签认"
        if action in ("开工审批", "开始施工"):
            # 材料核验与工序备料使用同一口径：签认证明有效 + 实际领用。
            reasons = material_start_reasons(entry)
            if reasons:
                return False, "；".join(reasons)
        if action == "开始施工":
            process_name = entry.get("工序名称", "")
            # 检查工匠是否已到岗
            craftsmen = store.query_by_field("craftsman_assign", "分配工序", process_name)
            if craftsmen and any(c.get("status") != "已到岗" for c in craftsmen):
                return False, "该工序的工匠尚未全部到岗"
            # 检查关联排期是否已发布
            schedules = store.query_by_field("construction_schedule", "计划工序", process_name)
            if schedules:
                unreleased = [s for s in schedules if s.get("status") == "编制中"]
                if unreleased:
                    return False, "关联施工排期还未发布"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> list[dict]:
        effects = []
        if action == "开工审批":
            # 审批通过后记录结果，供后续开始施工复用同一口径。
            refresh_work_sequence(entry)
            entry["开工审批结果"] = "通过"
            entry["开工材料条件"] = "通过"
            effects.extend(dict(item) for item in entry.get("备料清单", []))
        elif action == "开始施工":
            refresh_work_sequence(entry)
            entry["开工审批结果"] = "通过"
        if action == "完成工序":
            process_name = entry.get("工序名称", "")
            # 工序完成后联动更新关联施工排期
            for sched in store.rows("construction_schedule"):
                if sched.get("计划工序", "") == process_name:
                    if sched.get("status") == "执行中":
                        sched["里程碑节点"] = f"{process_name}已完成"
                        effects.append(dict(sched))
            # 联动更新修缮日志中关联日志
            for log in store.rows("restoration_log"):
                if log.get("对应工序", "") == process_name:
                    if log.get("status") in ("已审核", "已填写"):
                        log["status"] = "已归档"
                        log["pending"] = False
                        effects.append(dict(log))
        return effects
