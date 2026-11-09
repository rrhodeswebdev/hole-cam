// What the API (src/hole_cam/api.py) sends back for POST /jobs and /jobs/revise.

export type Vec3 = [number, number, number]

export type Hole = { id: number; x: number; y: number; diameter: number; depth: number; through: boolean; top_z: number }

export type OpKind = 'spot_drill' | 'drill' | 'ream' | 'tap'

export type Operation = {
  op: OpKind
  tool_id: string
  hole_ids: number[]
  depth: number
  cycle: 'G81' | 'G83' | 'G84' | 'G85'
  peck: number
  reason: string
}

export type Plan = { operations: Operation[]; warnings?: string[] }

export type Callout = {
  callout: string
  hole_ids: number[]
  plus_mm: number
  minus_mm: number
  pitch_mm: number
  tap: boolean
  ream: boolean
}

export type ToolInfo = {
  type: 'spot_drill' | 'drill' | 'reamer' | 'tap'
  diameter: number
  point_angle: number | null
  flute_length: number | null
  pitch: number | null
}

// One entry of the safety checker's trace: a move, or a tool change (which has no position).
export type Move = { tool: number; kind: 'rapid' | 'feed'; line: number; from: Vec3; to: Vec3; seconds: number }
export type ToolChange = { tool: number; kind: 'tool_change'; line: number; seconds: number }
export type ToolpathEvent = Move | ToolChange

export type Job = {
  part: { top_z: number; bottom_z: number; setup?: string }
  holes: Hole[]
  skipped: { diameter: number; reason: string }[]
  mesh: { vertices: Vec3[]; triangles: Vec3[] }
  plan: Plan | null
  plan_errors: string[]
  gcode: string | null
  findings: { line: number; check: string; message: string }[]
  toolpath: ToolpathEvent[]
  export_ok: boolean
  tool_numbers: Record<string, number>
  tool_info: Record<number, ToolInfo>
  cycle_seconds: number | null
  drawing: Callout[]
  drill_oversize: number
  summary?: string // only from /jobs/revise
}
