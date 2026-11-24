<template>
  <section class="page displacement-page" data-testid="displacement-board">
    <header class="page-head">
      <div>
        <h2>位移异常坐标对照看板</h2>
        <p class="page-desc">
          并列展示位移异常前后的原坐标与标准坐标，逐点列出采用的质量报告、转换规则与道路侧别；
          复核打开使用原已确认值，后到更正独立挂起，历史结论可按采用的点与规则复算。
        </p>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="loadBoard">
      <label class="filter-item">
        <span>批次编号</span>
        <input v-model="batchKey" data-testid="batch-input" placeholder="如 B-DISP-01" />
      </label>
      <button class="btn primary" data-testid="load-board" type="submit">载入对照看板</button>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </form>

    <template v-if="board">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">对照点位数</span>
          <strong class="stat-value">{{ board.对照点.length }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">位移异常点</span>
          <strong class="stat-value">{{ abnormalCount }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">待并入更正</span>
          <strong class="stat-value">{{ board.待并入更正数 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">复核任务</span>
          <strong class="stat-value">{{ board.复核任务.length }}</strong>
        </article>
      </div>

      <div v-if="board.道路" class="road-banner">
        道路 {{ board.道路['道路编号'] }}：锚点
        ({{ board.道路['锚点纬度'] }}, {{ board.道路['锚点经度'] }})，
        行进方位角 {{ board.道路['方位角'] }}° —— 行进方向左侧为内侧，右侧为外侧
      </div>

      <!-- 对照图：点、质量报告、道路侧别、原坐标/标准坐标并列 -->
      <table class="data-table compare-table">
        <thead>
          <tr>
            <th>点位编号</th>
            <th>采用质量报告</th>
            <th>转换规则</th>
            <th>道路侧别</th>
            <th>原坐标（原签发依据）</th>
            <th>标准坐标（已确认）</th>
            <th>待并入更正</th>
            <th>复核/更正状态</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="point in board.对照点"
            :key="point.点位编号"
            :data-testid="`compare-row-${point.点位编号}`"
            :class="{ 'is-abnormal': point.位移异常 }"
          >
            <td>{{ point.点位编号 }}</td>
            <td>{{ point.采用质量报告 }}</td>
            <td>{{ point.采用转换规则 }}</td>
            <td>
              <span :class="['side-tag', sideClass(point.道路侧别)]">{{ point.道路侧别 }}</span>
            </td>
            <td>
              <div>{{ formatCoord(point.原坐标) }}</div>
              <span :class="['side-tag', sideClass(point.原坐标.side)]">{{ point.原坐标.side }}</span>
            </td>
            <td>
              <div>{{ formatCoord(point.标准坐标) }}</div>
              <span :class="['side-tag', sideClass(point.标准坐标.side)]">{{ point.标准坐标.side }}</span>
            </td>
            <td>
              <template v-if="point.待并入更正">
                <div>{{ formatCoord(point.待并入更正) }}</div>
                <span :class="['side-tag', sideClass(point.待并入更正.side)]">
                  {{ point.待并入更正.side }}
                </span>
                <div class="muted-text">{{ point.待并入更正.source }}</div>
              </template>
              <span v-else class="muted-text">—</span>
            </td>
            <td>
              <div>{{ point.复核状态 }}</div>
              <div class="muted-text">{{ point.更正状态 }}</div>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 复核任务与更正 -->
      <section class="workflow-panel">
        <h3>复核任务（打开即冻结原已确认值）</h3>
        <table class="data-table">
          <thead>
            <tr><th>任务编号</th><th>状态</th><th>覆盖点位</th><th>采用质量报告</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="task in board.复核任务" :key="task.任务编号">
              <td>{{ task.任务编号 }}</td>
              <td>{{ task.状态 }}</td>
              <td>{{ task.点位编号 }}</td>
              <td>{{ task.采用质量报告 }}</td>
              <td>
                <button
                  v-if="task.状态 === '复核中'"
                  class="link"
                  type="button"
                  :data-testid="`save-review-${task.任务编号}`"
                  @click="onSaveReview(task)"
                >
                  保存复核
                </button>
                <span v-else class="muted-text">已结案</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="panel-actions">
          <button class="btn primary" data-testid="open-review" type="button" @click="onOpenReview">
            打开复核（质量分析）
          </button>
          <button class="btn" type="button" @click="loadNextScope">刷新下一次待复核范围</button>
        </div>
      </section>

      <!-- 更正登记 -->
      <section class="workflow-panel">
        <h3>后到更正登记（须独立确认；复核未保存时只挂起，不改写基准）</h3>
        <form class="filter-bar" @submit.prevent="onSubmitCorrection">
          <label class="filter-item">
            <span>点位编号</span>
            <input v-model="correctionForm.pointCode" data-testid="correction-point" required />
          </label>
          <label class="filter-item">
            <span>更正纬度</span>
            <input v-model.number="correctionForm.lat" data-testid="correction-lat" type="number" step="0.000001" required />
          </label>
          <label class="filter-item">
            <span>更正经度</span>
            <input v-model.number="correctionForm.lon" data-testid="correction-lon" type="number" step="0.000001" required />
          </label>
          <button class="btn" data-testid="submit-correction" type="submit">提交独立确认更正</button>
        </form>
      </section>

      <!-- 标准化与出批 -->
      <section class="workflow-panel">
        <h3>正式标准化与出批（输出携带实际采用的质量报告与转换规则）</h3>
        <form class="filter-bar" @submit.prevent="onStandardize">
          <label class="filter-item">
            <span>生效规则ID（内侧确认规则）</span>
            <input v-model.number="insideRuleId" data-testid="inside-rule" type="number" required />
          </label>
          <button class="btn primary" data-testid="standardize" type="submit">正式标准化</button>
        </form>
        <form class="filter-bar" @submit.prevent="onTrial">
          <label class="filter-item">
            <span>试算规则ID（外侧规则）</span>
            <input v-model.number="outsideRuleId" data-testid="outside-rule" type="number" required />
          </label>
          <button class="btn" data-testid="trial" type="submit">另一套规则试算</button>
        </form>
        <div class="panel-actions">
          <button class="btn" data-testid="recompute" type="button" @click="onRecompute">按原依据复算历史结论</button>
          <button class="btn primary" data-testid="issue" type="button" @click="onIssue">完成出批</button>
          <button class="btn" data-testid="verify" type="button" @click="onVerify">核对下游侧别与待复核范围</button>
        </div>
        <pre v-if="lastResult" class="result-box">{{ lastResult }}</pre>
      </section>

      <section v-if="nextScope" class="workflow-panel" data-testid="next-scope">
        <h3>下一次待复核范围（{{ nextScope.待复核点数 }} 点）</h3>
        <table class="data-table">
          <thead>
            <tr><th>点位编号</th><th>道路侧别</th><th>当前标准坐标</th><th>待并入更正坐标</th><th>来源</th><th>独立确认</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in nextScope.范围" :key="item.点位编号">
              <td>{{ item.点位编号 }}</td>
              <td><span :class="['side-tag', sideClass(item.道路侧别)]">{{ item.道路侧别 }}</span></td>
              <td>{{ item.当前标准坐标 ?? '—' }}</td>
              <td>{{ item.待并入更正坐标 }}</td>
              <td>{{ item.更正来源 }}</td>
              <td>{{ item.独立确认 }}</td>
            </tr>
            <tr v-if="!nextScope.范围.length">
              <td colspan="6" class="empty-state">暂无待并入更正，下一次复核范围为空</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>

    <footer class="page-foot">
      <span>位移异常前后坐标对照 · 单次正式标准化不可混用两份质量报告</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  fetchBoard,
  fetchNextScope,
  issueBatch,
  openReview,
  recompute,
  saveReview,
  standardize,
  submitCorrection,
  trialRun,
  verifyDownstream,
} from '@/api/displacement'
import type { ComparisonBoard, NextScope, ReviewTask } from '@/api/displacement'

const batchKey = ref('B-DISP-01')
const board = ref<ComparisonBoard | null>(null)
const nextScope = ref<NextScope | null>(null)
const errorMessage = ref('')
const lastResult = ref('')
const insideRuleId = ref(4)
const outsideRuleId = ref(5)
const correctionForm = ref({ pointCode: 'P-1001', lat: 39.9047, lon: 116.4092 })

const abnormalCount = computed(() =>
  (board.value?.对照点 ?? []).filter((point) => point.位移异常).length,
)

function formatCoord(coord: { lat: number; lon: number }): string {
  return `${coord.lat.toFixed(6)}, ${coord.lon.toFixed(6)}`
}

function sideClass(side: string): string {
  if (side === '内侧') return 'side-inside'
  if (side === '外侧') return 'side-outside'
  return 'side-axis'
}

async function loadBoard() {
  errorMessage.value = ''
  try {
    board.value = await fetchBoard(batchKey.value.trim())
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '看板载入失败'
  }
}

async function loadNextScope() {
  errorMessage.value = ''
  try {
    nextScope.value = await fetchNextScope(batchKey.value.trim())
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '待复核范围读取失败'
  }
}

async function onOpenReview() {
  errorMessage.value = ''
  try {
    const task = await openReview(batchKey.value.trim())
    lastResult.value = JSON.stringify(task, null, 2)
    await loadBoard()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '打开复核失败'
  }
}

async function onSaveReview(task: ReviewTask) {
  errorMessage.value = ''
  try {
    const taskId = Number(task.任务编号.split('-').pop())
    const saved = await saveReview(taskId)
    lastResult.value = JSON.stringify(saved, null, 2)
    await loadBoard()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存复核失败'
  }
}

async function onSubmitCorrection() {
  errorMessage.value = ''
  try {
    const result = await submitCorrection(
      batchKey.value.trim(),
      correctionForm.value.pointCode,
      correctionForm.value.lat,
      correctionForm.value.lon,
    )
    lastResult.value = JSON.stringify(result, null, 2)
    await loadBoard()
    await loadNextScope()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '更正登记失败'
  }
}

async function onStandardize() {
  errorMessage.value = ''
  try {
    const result = await standardize(batchKey.value.trim(), insideRuleId.value)
    lastResult.value = JSON.stringify(result, null, 2)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '正式标准化失败'
  }
}

async function onTrial() {
  errorMessage.value = ''
  try {
    const result = await trialRun(batchKey.value.trim(), outsideRuleId.value)
    lastResult.value = JSON.stringify(result, null, 2)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '试算失败'
  }
}

async function onRecompute() {
  errorMessage.value = ''
  try {
    const result = await recompute(batchKey.value.trim())
    lastResult.value = JSON.stringify(result, null, 2)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复算失败'
  }
}

async function onIssue() {
  errorMessage.value = ''
  try {
    const result = await issueBatch(batchKey.value.trim())
    lastResult.value = JSON.stringify(result, null, 2)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '出批失败'
  }
}

async function onVerify() {
  errorMessage.value = ''
  try {
    const result = await verifyDownstream(batchKey.value.trim())
    lastResult.value = JSON.stringify(result, null, 2)
    nextScope.value = result.下一次待复核范围
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '下游核对失败'
  }
}

onMounted(loadBoard)
</script>

<style scoped>
.road-banner {
  background: #fff;
  border: 1px solid var(--border);
  border-left: 4px solid var(--brand);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  margin-bottom: 12px;
}
.compare-table .is-abnormal {
  background: #fff8f0;
}
.side-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  white-space: nowrap;
}
.side-inside {
  background: #e6f4ea;
  color: #1e7d3c;
}
.side-outside {
  background: #fde8e8;
  color: #b42318;
}
.side-axis {
  background: #eef2f7;
  color: var(--muted);
}
.muted-text {
  color: var(--muted);
  font-size: 12px;
}
.workflow-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
  margin-top: 16px;
}
.workflow-panel h3 {
  margin: 0 0 10px;
  font-size: 14px;
}
.panel-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.result-box {
  margin-top: 12px;
  background: #1e293b;
  color: #e2e8f0;
  border-radius: 6px;
  padding: 12px;
  font-size: 12px;
  max-height: 260px;
  overflow: auto;
}
</style>
