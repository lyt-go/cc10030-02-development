/** 位移异常复核与正式标准化工作流的接口封装，类型与后端 workflow 路由对齐。 */
import { request } from './client'

export type SideCoordinate = {
  lat: number
  lon: number
  side: string
}

export type ComparisonPoint = {
  点位编号: string
  道路编号: string
  道路侧别: string
  采用质量报告: string
  采用转换规则: string
  位移异常: boolean
  原坐标: SideCoordinate
  标准坐标: SideCoordinate
  待并入更正: (SideCoordinate & { source: string }) | null
  复核状态: string
  更正状态: string
}

export type ReviewTask = {
  任务编号: string
  状态: string
  点位编号: string
  采用质量报告: string
}

export type ComparisonBoard = {
  批次编号: string
  道路: Record<string, string | number> | null
  对照点: ComparisonPoint[]
  复核任务: ReviewTask[]
  待并入更正数: number
}

export type FrozenPoint = {
  点位编号: string
  基准纬度: number
  基准经度: number
  基准侧别: string
  采用质量报告: string
  采用转换规则: string
}

export type ReviewTaskDetail = ReviewTask & {
  status: string
  复核状态: string
  frozen_points: FrozenPoint[]
}

export type NextScopeItem = {
  点位编号: string
  道路侧别: string
  当前标准坐标: [number, number] | null
  待并入更正坐标: [number, number]
  更正来源: string
  独立确认: string
}

export type NextScope = {
  批次编号: string
  待复核点数: number
  范围: NextScopeItem[]
}

export type StandardizeSummary = {
  batch_key: string
  input_points: number
  standardized_points: number
  quality_report: string
  rule_id: number
  rule_code: string
  rule_version: string
}

export type IssueSummary = StandardizeSummary & {
  issued_at: number
  issued_points: number
  side_summary: Record<string, number>
}

export type DownstreamVerification = {
  批次编号: string
  出批报告: string
  出批规则: string
  侧别汇总: Record<string, number>
  逐点侧别: { point_id: string; trans_lat: number; trans_lon: number; side: string }[]
  下一次待复核范围: NextScope
}

async function postJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const detail = (await response.json().catch(() => null))?.detail ?? '操作未生效'
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export async function fetchBoard(batchKey: string): Promise<ComparisonBoard> {
  const response = await request(`/api/displacement/board/${encodeURIComponent(batchKey)}`)
  if (!response.ok) throw new Error('对照看板读取失败')
  return await response.json()
}

export async function openReview(batchKey: string): Promise<ReviewTaskDetail> {
  return postJson('/api/quality/recheck/open', { batch_key: batchKey })
}

export async function saveReview(taskId: number): Promise<ReviewTaskDetail> {
  return postJson('/api/displacement/reviews/save', { task_id: taskId })
}

export async function submitCorrection(
  batchKey: string,
  pointCode: string,
  correctedLat: number,
  correctedLon: number,
): Promise<Record<string, unknown>> {
  return postJson('/api/displacement/corrections', {
    batch_key: batchKey,
    point_code: pointCode,
    corrected_lat: correctedLat,
    corrected_lon: correctedLon,
    source: '现场判定',
    independently_confirmed: true,
  })
}

export async function standardize(batchKey: string, ruleId: number): Promise<StandardizeSummary> {
  return postJson('/api/coord/standardize', { batch_key: batchKey, rule_id: ruleId })
}

export async function trialRun(batchKey: string, ruleId: number): Promise<Record<string, unknown>> {
  return postJson('/api/coord/trial', { batch_key: batchKey, rule_id: ruleId })
}

export async function recompute(batchKey: string): Promise<Record<string, unknown>> {
  return postJson('/api/coord/recompute', { batch_key: batchKey })
}

export async function issueBatch(batchKey: string): Promise<IssueSummary> {
  return postJson('/api/coord/issue', { batch_key: batchKey })
}

export async function verifyDownstream(batchKey: string): Promise<DownstreamVerification> {
  return postJson('/api/coord/verify-downstream', { batch_key: batchKey })
}

export async function fetchNextScope(batchKey: string): Promise<NextScope> {
  const response = await request(`/api/displacement/next-scope/${encodeURIComponent(batchKey)}`)
  if (!response.ok) throw new Error('待复核范围读取失败')
  return await response.json()
}
