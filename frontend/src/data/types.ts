/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ActionInput = {
  name: string
  label: string
  required?: boolean
}

export type PreparationItem = {
  材料名称: string
  规格型号: string
  批次: string
  对应工序: string
  核验单: number
  应备数量: number
  已领数量: number
  待领数量: number
  已退回数量: number
  签认状态: string
  签认证明: string
  证明有效: boolean
  核对通过: boolean
  领用记录: Record<string, unknown>[]
  去向凭据: Record<string, unknown>[]
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
  ledgerFields?: string[]
  derivedFields?: string[]
  actionInputs?: Record<string, ActionInput[]>
  preparationPanel?: boolean
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
