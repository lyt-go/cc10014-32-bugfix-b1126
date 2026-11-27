import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult, PreparationItem } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
  values: Record<string, string> = {},
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action, values }),
  })
  return await response.json()
}

export type PreparationResult = {
  module: string
  entry_id: number
  工序名称: string
  备料状态: string
  材料核对通过: boolean
  items: PreparationItem[]
}

export type EligibilityResult = {
  module: string
  entry_id: number
  工序名称: string
  开工条件: string
  材料核对通过: boolean
  reasons: string[]
  items: PreparationItem[]
}

export async function fetchPreparation(
  key: string,
  id: number,
): Promise<PreparationResult> {
  const response = await request(`/api/${key}/${id}/preparation`)
  if (!response.ok) throw new Error('备料清单读取失败')
  return await response.json()
}

export async function fetchStartEligibility(
  key: string,
  id: number,
): Promise<EligibilityResult> {
  const response = await request(`/api/${key}/${id}/start-eligibility`)
  if (!response.ok) throw new Error('开工审批资格读取失败')
  return await response.json()
}

export type RequisitionResult = {
  module: string
  entry_id: number
  材料名称: string
  规格型号: string
  送检批次: string
  对应工序: string
  签认状态: string
  证明状态: string
  已领数量: number
  待领数量: number
  已退回数量: number
  items: Record<string, unknown>[]
}

export async function fetchRequisitions(
  key: string,
  id: number,
): Promise<RequisitionResult> {
  const response = await request(`/api/${key}/${id}/requisitions`)
  if (!response.ok) throw new Error('领用记录读取失败')
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export type DependentStatus = {
  module: string
  entry_id: number
  entry: Record<string, unknown>
  upstream: { module: string; id: number; name: string; status: string }[]
  downstream: { module: string; id: number; name: string; status: string }[]
}

export async function fetchDependentStatus(
  moduleKey: string,
  entryId: number,
): Promise<DependentStatus> {
  const response = await request(`/api/cross-module/dependent-status/${moduleKey}/${entryId}`)
  if (!response.ok) throw new Error('跨模块状态查询失败')
  return await response.json()
}

export async function fetchDependencyGraph(): Promise<{ nodes: unknown[]; edges: unknown[] }> {
  const response = await request('/api/cross-module/dependency-graph')
  if (!response.ok) throw new Error('依赖图加载失败')
  return await response.json()
}
