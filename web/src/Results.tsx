// The results panel: the holes found, what was read off the drawing, the plan, and the program with its checks.
import { useEffect, useMemo, useRef, type ReactNode } from 'react'
import { toolColor } from './colors.ts'
import { OP_NAMES, clock, fmt, opLabel, toolChanges } from './format.ts'
import type { Callout, Job } from './types.ts'

// Plain loop rather than Object.groupBy, which older browsers (before Chrome 117 / Safari 17.4) lack.
const countBy = <T,>(list: T[], key: (x: T) => string) =>
  list.reduce<Record<string, number>>((acc, x) => { acc[key(x)] = (acc[key(x)] ?? 0) + 1; return acc }, {})

export default function Results({ job, onDownload, activeLine, change }: { job: Job; onDownload: () => void; activeLine: number | null; change: ReactNode }) {
  const { holes, skipped, plan, plan_errors, gcode, findings, export_ok, tool_numbers, cycle_seconds } = job
  const changes = toolChanges(job)
  return (
    <>
      <section className="card">
        <h2>Holes <span className="muted">{holes.length} found{skipped.length > 0 && `, ${skipped.length} skipped`}</span></h2>
        <table>
          <thead><tr><th>#</th><th>Ø mm</th><th>Depth</th><th>Type</th><th>X, Y</th></tr></thead>
          <tbody>
            {holes.map((h) => (
              <tr key={h.id}><td>{h.id}</td><td>{fmt(h.diameter)}</td><td>{fmt(h.depth)}</td><td>{h.through ? 'through' : 'blind'}</td><td>{fmt(h.x)}, {fmt(h.y)}</td></tr>
            ))}
          </tbody>
        </table>
        {job.part.setup && job.part.setup !== '+Z' && (
          <p className="note small">Turned so the model's {job.part.setup} side faces up: that side has the most holes to drill.</p>
        )}
        {Object.entries(countBy(skipped, (s) => `Ø${fmt(s.diameter)}: ${s.reason}`)).map(([k, n]) => (
          <p key={k} className="muted small">Skipped {n > 1 && `${n}× `}{k}</p>
        ))}
      </section>

      {job.drawing.length > 0 && <FromDrawing callouts={job.drawing} oversize={job.drill_oversize} />}

      {plan && (
        <section className="card">
          <h2>Plan</h2>
          <ol className="ops">
            {plan.operations.map((op, i) => {
              const t = tool_numbers[op.tool_id]
              return (
                <li key={i}>
                  <span className="swatch" style={{ background: toolColor(t) }} aria-hidden="true" />
                  <div>
                    <strong>T{t} {OP_NAMES[op.op]}</strong>{' '}
                    <span className="muted">{op.tool_id} · {opLabel(op, tool_numbers).split(' · ').slice(1).join(' · ')}</span>
                    <p className="small">{op.reason}</p>
                  </div>
                </li>
              )
            })}
          </ol>
          {(plan.warnings ?? []).map((w, i) => <p key={i} className="note small">{w}</p>)}
        </section>
      )}

      {plan && change}

      {plan_errors.length > 0 && (
        <section className="card" role="alert">
          <h2>Plan rejected</h2>
          <ul>{plan_errors.map((e, i) => <li key={i} className="small">{e}</li>)}</ul>
        </section>
      )}

      {gcode && (
        <section className="card">
          <h2>Safety checks</h2>
          {findings.length === 0
            ? <p className="ok">All 6 checks passed.</p>
            : <ul className="findings">{findings.map((f, i) => <li key={i} className="small"><strong>Line {f.line}</strong> {f.message}</li>)}</ul>}
          <p className="small">Estimated cycle time <strong>{clock(cycle_seconds ?? 0)}</strong> · {changes} tool change{changes === 1 ? '' : 's'}</p>
          <button onClick={onDownload} disabled={!export_ok}>Download .nc</button>
          {!export_ok && <p className="muted small">Download is blocked until every check passes.</p>}
          <details>
            <summary>G-code ({gcode.split('\n').length - 1} lines)</summary>
            <Program gcode={gcode} activeLine={activeLine} />
          </details>
        </section>
      )}
    </>
  )
}

// The program with the line the simulation is on highlighted and kept in view.
function Program({ gcode, activeLine }: { gcode: string; activeLine: number | null }) {
  const pre = useRef<HTMLPreElement>(null)
  const lines = useMemo(() => gcode.split('\n'), [gcode])
  useEffect(() => {
    const el = pre.current?.querySelector<HTMLElement>('.active')
    if (el && pre.current) pre.current.scrollTop = el.offsetTop - pre.current.clientHeight / 2
  }, [activeLine])
  return (
    <pre ref={pre}>
      {lines.map((line, i) => (
        <span key={i} className={i + 1 === activeLine ? 'active' : undefined}>{line}{'\n'}</span>
      ))}
    </pre>
  )
}

// What Claude read off the drawing, and what the tolerance or thread needs: the Drill/Ream/Tap call is made by code, not Claude.
function FromDrawing({ callouts, oversize }: { callouts: Callout[]; oversize: number }) {
  const dev = (v: number) => (v > 0 ? '+' : v < 0 ? '−' : '') + fmt(Math.abs(v))
  return (
    <section className="card">
      <h2>From the drawing <span className="muted">read by Claude</span></h2>
      <table>
        <thead><tr><th>Callout</th><th>Holes</th><th>Tolerance mm</th><th>Needs</th></tr></thead>
        <tbody>
          {callouts.map((c, i) => (
            <tr key={i}>
              <td>{c.callout}</td>
              <td>{c.hole_ids.join(', ')}</td>
              <td>{c.tap ? `thread, ${fmt(c.pitch_mm)} pitch` : `${dev(c.plus_mm)} / ${dev(c.minus_mm)}`}</td>
              <td>{c.tap ? <strong>Tap</strong> : c.ream ? <strong>Ream</strong> : 'Drill'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        A drill can cut up to {fmt(oversize)} mm oversize, so a hole with a tighter upper tolerance is drilled under size and reamed.
        {callouts.some((c) => c.tap) && ' A threaded hole is drilled at its tap drill size, then tapped.'}
      </p>
    </section>
  )
}
