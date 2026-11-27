<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th v-for="column in meta.ledgerFields ?? []" :key="`ledger-${column}`">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="row in rows" :key="String(row.id)">
          <tr
            :data-testid="`row-${row.id}`"
            :data-status="String(row.status)"
          >
            <td v-for="column in meta.fields" :key="column">{{ cellText(row, column) }}</td>
            <td v-for="column in meta.ledgerFields ?? []" :key="`ledger-${column}`">
              {{ cellText(row, column) }}
            </td>
            <td>{{ row.status }}</td>
            <td class="row-actions">
              <button
                v-for="action in meta.actions"
                :key="action"
                class="link"
                type="button"
                :data-testid="`action-${row.id}-${action}`"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
              <button
                v-if="meta.preparationPanel"
                class="link"
                type="button"
                :data-testid="`prep-${row.id}`"
                @click="openPreparation(row)"
              >
                备料清单
              </button>
            </td>
          </tr>
          <tr v-if="detailFields.length" :key="`detail-${row.id}`" class="detail-row">
            <td :colspan="meta.fields.length + (meta.ledgerFields?.length ?? 0) + 2">
              <span v-for="field in detailFields" :key="field" class="detail-item">
                {{ field }}：{{ cellText(row, field) }}
              </span>
            </td>
          </tr>
        </template>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + (meta.ledgerFields?.length ?? 0) + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="actionDialog" class="modal-backdrop" data-testid="action-modal">
      <form class="modal" @submit.prevent="submitActionInput">
        <h3>{{ actionDialog.action }}：{{ actionDialog.row['材料名称'] ?? actionDialog.row['工序名称'] }}</h3>
        <p class="page-desc">{{ actionHint }}</p>
        <label v-for="input in actionDialog.inputs" :key="input.name" class="create-field">
          <span>{{ input.label }}</span>
          <input
            v-model="actionValues[input.name]"
            :data-testid="`action-input-${input.name}`"
            :required="input.required"
          />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="actionDialog = null">取消</button>
          <button class="btn primary" data-testid="submit-action" type="submit">确认</button>
        </div>
      </form>
    </div>

    <div v-if="prepPanel" class="modal-backdrop" data-testid="preparation-modal">
      <section class="modal wide">
        <h3>备料清单：{{ prepPanel.工序名称 }}</h3>
        <p class="page-desc">
          备料状态：{{ prepPanel.备料状态 }} · 材料核对：{{ prepPanel.材料核对通过 ? '通过' : '不通过' }}
          · 开工材料条件：{{ prepPanel.开工材料条件 }}
        </p>
        <p v-if="prepPanel.reasons.length" class="error-text">{{ prepPanel.reasons.join('；') }}</p>
        <table class="data-table">
          <thead>
            <tr>
              <th>材料</th><th>规格</th><th>批次</th><th>应备</th><th>已领</th>
              <th>待领</th><th>已退回</th><th>签认</th><th>核对</th><th>去向凭据</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in prepPanel.items" :key="item.批次 + item.材料名称">
              <td>{{ item.材料名称 }}</td>
              <td>{{ item.规格型号 || '—' }}</td>
              <td>{{ item.批次 || '—' }}</td>
              <td>{{ item.应备数量 }}</td>
              <td>{{ item.已领数量 }}</td>
              <td>{{ item.待领数量 }}</td>
              <td>{{ item.已退回数量 }}</td>
              <td>{{ item.签认状态 }}</td>
              <td>{{ item.核对通过 ? '通过' : '不通过' }}</td>
              <td>
                <span v-for="proof in item.去向凭据" :key="String(proof.领用单号)">
                  {{ proof.领用单号 }} ×{{ proof.数量 }} → {{ proof.用于工序 }}
                </span>
              </td>
            </tr>
            <tr v-if="!prepPanel.items.length">
              <td colspan="10" class="empty-state">该工序没有对应的材料批次</td>
            </tr>
          </tbody>
        </table>
        <div class="page-actions">
          <button class="btn" type="button" @click="prepPanel = null">关闭</button>
        </div>
      </section>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  createEntry,
  downloadEntries,
  fetchPreparation,
  fetchStartEligibility,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type { ActionInput, EntryRow, PreparationItem } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const actionDialog = ref<{ action: string; row: EntryRow; inputs: ActionInput[] } | null>(null)
const actionValues = ref<Record<string, string>>({})
const prepPanel = ref<{
  工序名称: string
  备料状态: string
  材料核对通过: boolean
  开工材料条件: string
  reasons: string[]
  items: PreparationItem[]
} | null>(null)

// 不在列表主字段里的台账/派生字段，按行展示在展开行里。
const detailFields = computed(() =>
  (meta.derivedFields ?? []).filter((field) => !meta.ledgerFields?.includes(field)),
)
const actionHint = computed(() =>
  actionDialog.value?.action === '领用材料'
    ? '领用数量只从该批次的待领量里扣；领用工序必须与批次对应的工序一致。'
    : actionDialog.value?.action === '撤回签认'
      ? '仓管撤回只收回未领用的剩余量，已经领用的份额保留去向凭据，可在领用查询里核对是否成立。'
      : '',
)

function cellText(row: EntryRow, column: string): string {
  const value = row[column]
  if (value === undefined || value === null || value === '') return '—'
  if (Array.isArray(value)) return `${value.length} 条`
  if (typeof value === 'object') return value['证明编号'] ?? '—'
  return String(value)
}

const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((r) => r.status === meta.statuses[1]).length },
  { label: meta.metrics[2], value: rows.value.filter((r) => r.status === meta.statuses[meta.statuses.length - 1]).length },
])

const statusSummary = computed(() =>
  meta.statuses.map((s) => ({ status: s, count: rows.value.filter((r) => String(r.status) === s).length })),
)

function resetFilters() { filters.value = {}; void reload() }

async function exportRows() {
  errorMessage.value = ''
  try { await downloadEntries(meta.key, filters.value) }
  catch (e) { errorMessage.value = e instanceof Error ? e.message : '导出失败' }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((f) => [f, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true; errorMessage.value = ''
  try {
    const r = await createEntry(meta.key, createValues.value)
    if (!r.ok) throw new Error(r.message)
    showCreate.value = false; await reload()
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '登记失败' }
  finally { creating.value = false }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  const inputs = meta.actionInputs?.[action]
  if (inputs && inputs.length) {
    actionValues.value = Object.fromEntries(inputs.map((input) => [input.name, '']))
    actionDialog.value = { action, row, inputs }
    return
  }
  await dispatchAction(action, row, {})
}

async function submitActionInput() {
  if (!actionDialog.value) return
  const { action, row, inputs } = actionDialog.value
  const missing = inputs.filter((input) => input.required && !actionValues.value[input.name])
  if (missing.length) {
    errorMessage.value = `请填写：${missing.map((input) => input.label).join('、')}`
    return
  }
  actionDialog.value = null
  await dispatchAction(action, row, actionValues.value)
}

async function dispatchAction(action: string, row: EntryRow, values: Record<string, string>) {
  errorMessage.value = ''
  try {
    const r = await applyAction(meta.key, Number(row.id), action, values)
    if (!r.ok) throw new Error(r.message)
    await reload()
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '操作失败' }
}

async function openPreparation(row: EntryRow) {
  errorMessage.value = ''
  try {
    const [prep, eligibility] = await Promise.all([
      fetchPreparation(meta.key, Number(row.id)),
      fetchStartEligibility(meta.key, Number(row.id)),
    ])
    prepPanel.value = {
      工序名称: prep.工序名称,
      备料状态: prep.备料状态,
      材料核对通过: eligibility.材料核对通过,
      开工材料条件: eligibility.开工条件,
      reasons: eligibility.reasons,
      items: prep.items,
    }
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '备料清单读取失败' }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items; total.value = payload.total
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '业务列表读取失败' }
}

onMounted(reload)
</script>

<style scoped>
.modal-backdrop { position:fixed; inset:0; background:rgba(0,0,0,.35); display:flex; align-items:center; justify-content:center; z-index:100; }
.modal { background:#fff; border-radius:8px; padding:24px; width:480px; max-height:80vh; overflow-y:auto; }
.create-field { display:flex; flex-direction:column; gap:4px; margin-bottom:12px; }
.create-field span { font-size:13px; color:var(--text-secondary,#4a5568); }
.create-field input { padding:6px 8px; border:1px solid var(--border-color,#e2e8f0); border-radius:4px; font-size:13px; }
.modal.wide { width: min(960px, 100%); }
.modal .data-table { margin: 12px 0; }
.modal .data-table td, .modal .data-table th { font-size: 12px; }
.detail-row td { background: #fafbfc; color: var(--muted); }
.detail-item { margin-right: 16px; font-size: 12px; }
</style>