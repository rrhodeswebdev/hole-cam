// The program's playback, worked out from the safety checker's trace: when each move runs, where the tool is at
// any moment, and what each move is doing in plain words. No rendering here.
import type { Hole, Move, ToolInfo, ToolpathEvent, Vec3 } from './types.ts'

export type Timed = ToolpathEvent & { start: number; end: number }

// Give every move and tool change a start and end time, so any moment of the program can be looked up.
export function timeline(toolpath: ToolpathEvent[]): Timed[] {
  let t = 0
  return toolpath.map((e) => {
    const start = t
    t += e.seconds
    return { ...e, start, end: t }
  })
}

const TOOL_NAMES: Record<ToolInfo['type'], string> = { spot_drill: 'spot drill', drill: 'drill', reamer: 'reamer', tap: 'tap' }
const toolName = (n: number, info: ToolInfo) => `T${n} · ${info?.diameter} mm ${TOOL_NAMES[info?.type] ?? 'tool'}`
const VERBS: Record<ToolInfo['type'], string> = { spot_drill: 'Spot drilling', drill: 'Drilling', reamer: 'Reaming', tap: 'Tapping' }

// A plain-words description of each move, e.g. "Drilling hole 4 · peck 2 of 5" or "Pulling out to clear chips".
export function describeSteps(events: ToolpathEvent[], holes: Hole[], toolInfo: Record<number, ToolInfo>) {
  const near = (a: number[], b: number[]) => Math.abs(a[0] - b[0]) < 0.01 && Math.abs(a[1] - b[1]) < 0.01
  const holeAt = (p: number[]) => holes.find((h) => near([h.x, h.y], p))
  const key = (e: Move) => `${e.line} ${e.to[0]} ${e.to[1]}`
  const feeds: Record<string, number> = {}
  const down = (e: ToolpathEvent) => e.kind === 'feed' && e.to[2] < e.from[2] // reamers and taps also feed back out
  for (const e of events) if (e.kind === 'feed' && down(e)) feeds[key(e)] = (feeds[key(e)] ?? 0) + 1
  const done: Record<string, number> = {}
  return events.map((e) => {
    const tool = toolName(e.tool, toolInfo[e.tool])
    if (e.kind === 'tool_change') return { what: `Tool change: ${tool}`, tool: 'Swapping tools in the spindle' }
    const hole = holeAt(e.to)
    const where = hole ? `hole ${hole.id}` : 'the next position'
    const k = key(e)
    if (e.kind === 'feed' && !down(e)) {
      return { what: toolInfo[e.tool]?.type === 'tap' ? `Reversing out of ${where}` : `Feeding back out of ${where}`, tool }
    }
    if (e.kind === 'feed') {
      done[k] = (done[k] ?? 0) + 1
      const verb = VERBS[toolInfo[e.tool]?.type] ?? 'Drilling'
      return { what: `${verb} ${where}${feeds[k] > 1 ? ` · peck ${done[k]} of ${feeds[k]}` : ''}`, tool }
    }
    if (!near(e.from, e.to)) return { what: `Moving to ${where}`, tool }
    if (e.to[2] > e.from[2]) {
      return { what: done[k] && done[k] < feeds[k] ? 'Pulling out to clear chips' : `Pulling out of ${where}`, tool }
    }
    return { what: done[k] ? 'Back down to keep drilling' : `Down to the start of ${where}`, tool }
  })
}

// The event running at time t, and where the tool tip is (null during a tool change).
export function stateAt(events: Timed[], t: number) {
  let i = events.findIndex((e) => e.end > t)
  if (i === -1) i = events.length - 1
  const e = events[i]
  const k = e.seconds > 0 ? Math.min(1, Math.max(0, (t - e.start) / e.seconds)) : 1
  const tip = e.kind === 'tool_change' ? null : e.from.map((v, j) => v + (e.to[j] - v) * k) as Vec3
  return { i, e, tip }
}
