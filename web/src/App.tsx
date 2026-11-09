import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import Viewer, { clock } from './Viewer.tsx'
import { toolColor } from './colors.ts'
import type { Callout, Job, Operation } from './types.ts'

type Busy = 'plan' | 'revise' | null
type Planned = { file: File; drawing: File | null; material: string }
type Revision = { request: string; summary: string } & ({ previous: Job } | { errors: string[] })

const fmt = (n: number) => Number(n.toFixed(3)).toString()
const EXAMPLES = ['Do the deep holes first', 'Peck every hole, 2 mm at a time', 'Spot drill 1 mm deep', 'Skip spot drilling to save time']

// One line per operation, used for the plan list and to compare plans.
const OP_NAMES = { spot_drill: 'Spot drill', drill: 'Drill', ream: 'Ream', tap: 'Tap' }
const opLabel = (op: Operation, toolNumbers: Record<string, number>) => op.op === 'spot_drill'
  ? `T${toolNumbers[op.tool_id]} Spot drill · ${fmt(op.depth)} mm deep · holes ${op.hole_ids.join(', ')}`
  : `T${toolNumbers[op.tool_id]} ${OP_NAMES[op.op]} · ${op.cycle}${op.cycle === 'G83' ? ` peck ${fmt(op.peck)}` : ''} · holes ${op.hole_ids.join(', ')}`
// Plain loop rather than Object.groupBy, which older browsers (before Chrome 117 / Safari 17.4) lack.
const countBy = <T,>(list: T[], key: (x: T) => string) =>
  list.reduce<Record<string, number>>((acc, x) => { acc[key(x)] = (acc[key(x)] ?? 0) + 1; return acc }, {})
const toolChanges = (job: Job) => job.toolpath.filter((m) => m.kind === 'tool_change').length

function errorText(detail: unknown, status: number) {
  if (typeof detail === 'string') return detail
  return `The server rejected the request (HTTP ${status}).`
}

export default function App() {
  const [materials, setMaterials] = useState<Record<string, string>>({})
  const [material, setMaterial] = useState('6061')
  const [file, setFile] = useState<File | null>(null)
  const [drawing, setDrawing] = useState<File | null>(null) // optional PDF
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState<Busy>(null) // null, 'plan' or 'revise'
  const [error, setError] = useState<string | null>(null)
  const [activeLine, setActiveLine] = useState<number | null>(null)
  const [planned, setPlanned] = useState<Planned | null>(null) // the file, drawing and material the current job came from
  const [revision, setRevision] = useState<Revision | null>(null) // { request, summary, previous } or { request, summary, errors }

  useEffect(() => {
    fetch('/materials')
      .then((r) => r.json())
      .then(setMaterials)
      .catch(() => setError('Can’t reach the API. Start it with: uv run uvicorn hole_cam.api:app'))
  }, [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    setBusy('plan')
    setError(null)
    setJob(null)
    setActiveLine(null)
    setRevision(null)
    const body = new FormData()
    body.append('file', file)
    body.append('material', material)
    if (drawing) body.append('drawing', drawing)
    try {
      const r = await fetch('/jobs', { method: 'POST', body })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(errorText(data.detail, r.status))
      setJob(data)
      setPlanned({ file, drawing, material })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function revise(request: string) {
    if (!planned || !job) return false
    setBusy('revise')
    setError(null)
    const body = new FormData()
    body.append('file', planned.file)
    body.append('material', planned.material)
    if (planned.drawing) body.append('drawing', planned.drawing)
    body.append('plan', JSON.stringify(job.plan))
    body.append('request', request)
    try {
      const r = await fetch('/jobs/revise', { method: 'POST', body })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(errorText(data.detail, r.status))
      if (data.plan_errors.length) {
        // Keep the working plan; show what Claude tried and why it was rejected.
        setRevision({ request, summary: data.summary, errors: data.plan_errors })
      } else {
        setRevision({ request, summary: data.summary, previous: job })
        setActiveLine(null)
        setJob(data)
      }
      return true
    } catch (err) {
      setError((err as Error).message)
      return false
    } finally {
      setBusy(null)
    }
  }

  function undo() {
    if (!revision || !('previous' in revision)) return
    setJob(revision.previous)
    setActiveLine(null)
    setRevision(null)
  }

  function download() {
    if (!job?.gcode || !planned) return
    const url = URL.createObjectURL(new Blob([job.gcode], { type: 'text/plain' }))
    const a = Object.assign(document.createElement('a'), { href: url, download: planned.file.name.replace(/\.(step|stp)$/i, '') + '.nc' })
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="app">
      <aside className="panel">
        <header>
          <h1>Hole-CAM</h1>
          <p className="muted">STEP part in, checked drilling program out.</p>
        </header>

        <form onSubmit={submit} className="card">
          <label>
            Part (STEP)
            <input type="file" accept=".step,.stp" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <label>
            Drawing (PDF, optional)
            <input type="file" accept=".pdf,application/pdf" onChange={(e) => setDrawing(e.target.files?.[0] ?? null)} />
          </label>
          <label>
            Material
            <select value={material} onChange={(e) => setMaterial(e.target.value)}>
              {Object.entries(materials).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
          </label>
          <button type="submit" disabled={!file || !!busy}>{busy === 'plan' ? 'Planning…' : 'Plan the job'}</button>
          <p className="muted small">{busy === 'plan' && `Finding holes and asking Claude for a plan${drawing ? ' from the drawing' : ''}. This can take a minute.`}</p>
        </form>

        {error && <p className="alert" role="alert">{error}</p>}
        {job && (
          <Results job={job} onDownload={download} activeLine={activeLine}
            change={<ChangePlan job={job} busy={busy} revision={revision} onRevise={revise} onUndo={undo} />} />
        )}
      </aside>

      <main className="stage">
        {job
          // setActiveLine is stable, so the viewer's scene is not rebuilt on every line change
          ? <Viewer job={job} onLine={setActiveLine} />
          : !busy && <div className="empty"><p>Upload a part to see it here with its toolpath.</p></div>}
        {busy && <Working what={busy} drawing={busy === 'plan' ? drawing : planned?.drawing} />}
      </main>
    </div>
  )
}

function Results({ job, onDownload, activeLine, change }: { job: Job; onDownload: () => void; activeLine: number | null; change: ReactNode }) {
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

      {job.drawing?.length > 0 && <FromDrawing callouts={job.drawing} oversize={job.drill_oversize} />}

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

// Ask Claude to change the plan in plain English, and show what changed.
function ChangePlan({ job, busy, revision, onRevise, onUndo }: {
  job: Job; busy: Busy; revision: Revision | null; onRevise: (request: string) => Promise<boolean>; onUndo: () => void
}) {
  const [request, setRequest] = useState('')
  async function submit(e: FormEvent) {
    e.preventDefault()
    if (await onRevise(request.trim())) setRequest('')
  }
  return (
    <section className="card">
      <h2>Change the plan</h2>
      <form onSubmit={submit} className="change">
        <label className="sr-only" htmlFor="change-request">What should change?</label>
        <textarea id="change-request" rows={2} maxLength={500} value={request} placeholder="e.g. Do the deep holes first"
          onChange={(e) => setRequest(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); e.currentTarget.form?.requestSubmit() } }} />
        <div className="examples">
          {EXAMPLES.map((x) => <button key={x} type="button" className="chip" onClick={() => setRequest(x)} disabled={!!busy}>{x}</button>)}
        </div>
        <button type="submit" disabled={!!busy || !request.trim()}>{busy === 'revise' ? 'Revising…' : 'Revise with Claude'}</button>
      </form>
      {revision && <RevisionResult job={job} revision={revision} onUndo={onUndo} />}
    </section>
  )
}

function RevisionResult({ job, revision, onUndo }: { job: Job; revision: Revision; onUndo: () => void }) {
  const { request, summary } = revision
  if ('errors' in revision) {
    return (
      <div className="revision" role="alert">
        <p className="small muted">You asked: “{request}”</p>
        <p><strong>Claude:</strong> {summary}</p>
        <p className="bad small"><strong>Rejected by the validator</strong>, so your plan is unchanged:</p>
        <ul>{revision.errors.map((e, i) => <li key={i} className="small">{e}</li>)}</ul>
      </div>
    )
  }
  const { previous } = revision
  const before = (previous.plan?.operations ?? []).map((op) => opLabel(op, previous.tool_numbers))
  const after = (job.plan?.operations ?? []).map((op) => opLabel(op, job.tool_numbers))
  const removed = before.filter((l) => !after.includes(l))
  const added = after.filter((l) => !before.includes(l))
  const reordered = !removed.length && !added.length && before.join() !== after.join()
  const hadProgram = Boolean(previous.gcode)
  const dt = (job.cycle_seconds ?? 0) - (previous.cycle_seconds ?? 0)
  return (
    <div className="revision" aria-live="polite">
      <p className="small muted">You asked: “{request}”</p>
      <p><strong>Claude:</strong> {summary}</p>
      {removed.length + added.length > 0 && (
        <ul className="diff">
          {removed.map((l) => <li key={'-' + l} className="removed"><span aria-hidden="true">−</span><span className="sr-only">Removed:</span> {l}</li>)}
          {added.map((l) => <li key={'+' + l} className="added"><span aria-hidden="true">+</span><span className="sr-only">Added:</span> {l}</li>)}
        </ul>
      )}
      {reordered && <p className="small">Same operations in a new order.</p>}
      {!removed.length && !added.length && !reordered && <p className="small">No change to the operations.</p>}
      <p className="small">
        {/* A rejected plan has no program, so there's no cycle time or tool changes to compare against. */}
        Cycle time {hadProgram && <>{clock(previous.cycle_seconds ?? 0)} → </>}<strong>{clock(job.cycle_seconds ?? 0)}</strong>
        {hadProgram && Math.abs(dt) >= 0.5 && <span className={dt < 0 ? 'ok-text' : 'bad'}> ({dt < 0 ? '−' : '+'}{Math.round(Math.abs(dt))} s)</span>}
        {' · '}tool changes {hadProgram && `${toolChanges(previous)} → `}{toolChanges(job)}
        {' · '}{job.findings.length ? <span className="bad">safety checks failed</span> : <span className="ok-text">checks pass</span>}
      </p>
      <button type="button" className="secondary" onClick={onUndo}>Undo</button>
    </div>
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

// Over the 3D area while Claude works: what's happening and how long it's taken.
function Working({ what, drawing }: { what: 'plan' | 'revise'; drawing?: File | null }) {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const start = Date.now()
    const id = setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <div className={`working ${what}`}>
      <span className="spinner" aria-hidden="true" />
      <p role="status">{what === 'plan'
        ? (drawing ? 'Finding holes while Claude reads the drawing and plans' : 'Finding holes and asking Claude for a plan')
        : 'Claude is revising the plan'}</p>
      <p className="small muted-dark" aria-hidden="true">{clock(seconds)} · usually under a minute</p>
    </div>
  )
}
