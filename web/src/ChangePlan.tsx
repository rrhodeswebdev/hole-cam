// Changing the plan in plain English: the request box, and what Claude changed or why it was rejected.
import { useState, type FormEvent } from 'react'
import { clock, opLabel, toolChanges } from './format.ts'
import type { Job } from './types.ts'

export type Busy = 'plan' | 'revise' | null
export type Revision = { request: string; summary: string } & ({ previous: Job } | { errors: string[] })

const MAX_REQUEST = 500 // characters; the API's limit too (MAX_REQUEST in api.py)
const EXAMPLES = ['Do the deep holes first', 'Peck every hole, 2 mm at a time', 'Spot drill 1 mm deep', 'Skip spot drilling to save time']

// Ask Claude to change the plan in plain English, and show what changed.
export default function ChangePlan({ job, busy, revision, onRevise, onUndo }: {
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
        <textarea id="change-request" rows={2} maxLength={MAX_REQUEST} value={request} placeholder="e.g. Do the deep holes first"
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
