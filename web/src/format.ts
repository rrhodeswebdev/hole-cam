// How numbers, times and operations are written on the page.
import type { Job, OpKind, Operation } from './types.ts'

// Up to 3 decimals with no trailing zeros: 6.8, 12, 0.125.
export const fmt = (n: number) => Number(n.toFixed(3)).toString()

// Seconds as m:ss.
export const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

export const OP_NAMES: Record<OpKind, string> = { spot_drill: 'Spot drill', drill: 'Drill', ream: 'Ream', tap: 'Tap' }

// One line per operation, used for the plan list and to compare plans.
export const opLabel = (op: Operation, toolNumbers: Record<string, number>) => op.op === 'spot_drill'
  ? `T${toolNumbers[op.tool_id]} Spot drill · ${fmt(op.depth)} mm deep · holes ${op.hole_ids.join(', ')}`
  : `T${toolNumbers[op.tool_id]} ${OP_NAMES[op.op]} · ${op.cycle}${op.cycle === 'G83' ? ` peck ${fmt(op.peck)}` : ''} · holes ${op.hole_ids.join(', ')}`

export const toolChanges = (job: Job) => job.toolpath.filter((m) => m.kind === 'tool_change').length
