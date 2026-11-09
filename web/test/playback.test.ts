import assert from 'node:assert/strict'
import { test } from 'node:test'
import { describeSteps, stateAt, timeline } from '../src/playback.ts'
import type { Hole, ToolInfo, ToolpathEvent, Vec3 } from '../src/types.ts'

const hole: Hole = { id: 1, x: 10, y: 10, diameter: 6.8, depth: 10, through: false, top_z: 0 }
const toolInfo: Record<number, ToolInfo> = {
  1: { type: 'drill', diameter: 6.8, point_angle: 118, flute_length: 50, pitch: null },
  2: { type: 'tap', diameter: 8, point_angle: null, flute_length: 20, pitch: 1.25 },
}
const at = (z: number): Vec3 => [10, 10, z]
const rapid = (line: number, from: Vec3, to: Vec3, tool = 1): ToolpathEvent => ({ tool, kind: 'rapid', line, from, to, seconds: 1 })
const feed = (line: number, from: Vec3, to: Vec3, tool = 1): ToolpathEvent => ({ tool, kind: 'feed', line, from, to, seconds: 2 })

// A G83 hole in two pecks, then the same hole tapped: the shape of the checker's trace (checks.py).
const toolpath: ToolpathEvent[] = [
  { tool: 1, kind: 'tool_change', line: 5, seconds: 4 },
  rapid(7, [0, 0, 25], at(25)),
  rapid(8, at(25), at(2)),
  feed(8, at(2), at(-4)),
  rapid(8, at(-4), at(2)),
  rapid(8, at(2), at(-3.75)),
  feed(8, at(-3.75), at(-10)),
  rapid(8, at(-10), at(2)),
  { tool: 2, kind: 'tool_change', line: 11, seconds: 4 },
  feed(12, at(2), at(-8), 2),
  feed(12, at(-8), at(2), 2),
]

test('timeline gives each event back-to-back start and end times', () => {
  const events = timeline(toolpath)
  assert.deepEqual(events.slice(0, 4).map((e) => [e.start, e.end]), [[0, 4], [4, 5], [5, 6], [6, 8]])
  assert.equal(events.at(-1)?.end, 21)
})

test('describeSteps names each move, counting pecks', () => {
  assert.deepEqual(describeSteps(timeline(toolpath), [hole], toolInfo), [
    { what: 'Tool change: T1 · 6.8 mm drill', tool: 'Swapping tools in the spindle' },
    { what: 'Moving to hole 1', tool: 'T1 · 6.8 mm drill' },
    { what: 'Down to the start of hole 1', tool: 'T1 · 6.8 mm drill' },
    { what: 'Drilling hole 1 · peck 1 of 2', tool: 'T1 · 6.8 mm drill' },
    { what: 'Pulling out to clear chips', tool: 'T1 · 6.8 mm drill' },
    { what: 'Back down to keep drilling', tool: 'T1 · 6.8 mm drill' },
    { what: 'Drilling hole 1 · peck 2 of 2', tool: 'T1 · 6.8 mm drill' },
    { what: 'Pulling out of hole 1', tool: 'T1 · 6.8 mm drill' },
    { what: 'Tool change: T2 · 8 mm tap', tool: 'Swapping tools in the spindle' },
    { what: 'Tapping hole 1', tool: 'T2 · 8 mm tap' },
    { what: 'Reversing out of hole 1', tool: 'T2 · 8 mm tap' },
  ])
})

test('describeSteps calls a move to a spot with no hole "the next position"', () => {
  const [step] = describeSteps([rapid(3, [0, 0, 25], [50, 50, 25])], [hole], toolInfo)
  assert.equal(step?.what, 'Moving to the next position')
})

test('stateAt places the tool tip partway through a move, and nowhere during a tool change', () => {
  const events = timeline(toolpath)
  assert.deepEqual(stateAt(events, 7), { i: 3, e: events[3], tip: [10, 10, -1] }) // halfway down the first peck
  assert.equal(stateAt(events, 1).tip, null)
})

test('stateAt holds the first and last positions outside the program', () => {
  const events = timeline(toolpath)
  assert.deepEqual(stateAt(events, 99).tip, at(2))
  assert.equal(stateAt(events, 99).i, events.length - 1)
  assert.equal(stateAt(events, -1).i, 0)
})
