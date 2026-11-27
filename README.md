# 古建筑修缮管理平台

文保工程师、工匠和监理共同管理古建筑修缮工序，审核修缮方案，签收变更后按确认版施工。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 建筑勘查 | `building_survey` | 勘查记录 | 建筑名称、勘查部位、病害类型 |
| 修缮方案 | `restoration_plan` | 修缮方案 | 方案编号、对应勘查、修缮工艺 |
| 材料核验 | `material_verify` | 材料核验单 | 材料名称、规格型号、供应商 |
| 工匠分配 | `craftsman_assign` | 工匠分配单 | 工匠姓名、技艺类别、资质等级 |
| 工序编排 | `work_sequence` | 工序卡 | 工序名称、前置工序、所需工匠 |
| 质量检查 | `quality_inspect` | 检查记录 | 检查工序、检查项目、技术标准 |
| 历史图纸 | `historical_drawing` | 历史图纸 | 图纸编号、绘制年代、绘制内容 |
| 构件登记 | `component_registry` | 构件档案 | 构件名称、所属建筑、材质类型 |
| 环境监测 | `env_monitor` | 监测数据 | 监测位置、温度读数、湿度读数 |
| 修缮日志 | `restoration_log` | 施工日志 | 施工日期、对应工序、施工内容 |
| 验收记录 | `acceptance_record` | 验收报告 | 验收工序、验收标准、验收结论 |
| 修缮支出 | `funding_use` | 支出记录 | 支出项目、支出类别、预算金额 |
| 档案归档 | `archive_mgmt` | 修缮档案 | 档案名称、档案类别、形成日期 |
| 专家评审 | `expert_review` | 评审意见 | 评审方案、专家姓名、专业领域 |
| 安全交底 | `safety_briefing` | 交底记录 | 交底工序、危险源、防护措施 |
| 施工排期 | `construction_schedule` | 排期计划 | 计划工序、开始日期、结束日期 |
| 变更签证 | `change_order` | 变更签证单 | 变更事项、变更原因、影响工序 |
| 竣工总结 | `completion_summary` | 竣工报告 | 修缮项目、开工日期、竣工日期 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 材料批次与开工审批口径

材料核验单同时是一批材料的台账（`backend/app/services/material_ledger.py`）：

- 批次身份 = `对应工序 + 材料名称 + 规格型号 + 送检批次`，同一批次不能对应到
  别的工序或别的规格；没有填 `对应工序` 的旧数据按工序的 `所需材料` 兼容。
- 台账数量满足 `进场 = 已领 + 待领 + 已退回`。`领用材料` 只扣待领量，写入领用
  单和去向凭据；`退回材料` / `撤回签认`（仓管）只从**未领用**的资格里撤回剩余
  待领量，已经领用的份额保留在账上，领用记录仍判为成立并保留去向凭据。
  重复提交退回请求只扣一次待领量。
- 工序编排的 `备料清单`、`备料状态`、`开工材料条件` 由批次台账实时生成，
  工序卡不会自带一份独立的“材料已验证”标记，材料核验页退回后立即写回。
- **备料清单与开工审批使用同一个判定函数** `material_ledger.qualification`：
  签认证明仍然有效 + 有实际领用（有数量口径时）；退回的剩余量一律不算核验通过。
  没有数量口径的旧材料签认按“证明有效”兼容。

相关接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/work_sequence/{id}/preparation` | 工序备料清单 |
| GET | `/api/work_sequence/{id}/start-eligibility` | 开工审批资格与不通过原因 |
| GET | `/api/material_verify/{id}/requisitions` | 批次领用记录及是否成立（撤回后仍可查） |

动作参数通过动作接口的 `values` 传入，例如领用：

```json
POST /api/material_verify/1/actions
{"action": "领用材料", "values": {"领用数量": "5", "领用人": "王师傅", "领用工序": "西廊墙面"}}
```

领域：应用开发
子方向：web前端/全栈开发
