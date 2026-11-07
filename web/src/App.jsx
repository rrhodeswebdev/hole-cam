import { useEffect, useState } from 'react'
import Viewer from './Viewer.jsx'
import { toolColor } from './colors.js'

const fmt = (n) => Number(n.toFixed(3)).toString()

function errorText(detail, status) {
  if (typeof detail === 'string') return detail
  return `The server rejected the request (HTTP ${status}).`
}

export default function App() {
  const [materials, setMaterials] = useState({})
  const [material, setMaterial] = useState('6061')
  const [file, setFile] = useState(null)
  const [job, setJob] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch('/materials')
      .then((r) => r.json())
      .then(setMaterials)
      .catch(() => setError('Can’t reach the API. Start it with: uv run uvicorn hole_cam.api:app'))
  }, [])

  async function submit(e) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setError(null)
    setJob(null)
    const body = new FormData()
    body.append('file', file)
    body.append('material', material)
    try {
      const r = await fetch('/jobs', { method: 'POST', body })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(errorText(data.detail, r.status))
      setJob(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([job.gcode], { type: 'text/plain' }))
    const a = Object.assign(document.createElement('a'), { href: url, download: file.name.replace(/\.(step|stp)$/i, '') + '.nc' })
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
            <input type="file" accept=".step,.stp" onChange={(e) => setFile(e.target.files[0] ?? null)} />
          </label>
          <label>
            Material
            <select value={material} onChange={(e) => setMaterial(e.target.value)}>
              {Object.entries(materials).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
          </label>
          <button type="submit" disabled={!file || busy}>{busy ? 'Planning…' : 'Plan the job'}</button>
          <p className="muted small" aria-live="polite">{busy && 'Finding holes and asking Claude for a plan. This can take a minute.'}</p>
        </form>

        {error && <p className="alert" role="alert">{error}</p>}
        {job && <Results job={job} onDownload={download} />}
      </aside>

      <main className="stage">
        {job
          ? <Viewer mesh={job.mesh} toolpath={job.toolpath} />
          : <div className="empty"><p>Upload a part to see it here with its toolpath.</p></div>}
        {job?.toolpath.length > 0 && (
          <p className="legend small"><span className="dash" /> rapid <span className="solid" /> cutting</p>
        )}
      </main>
    </div>
  )
}

function Results({ job, onDownload }) {
  const { holes, skipped, plan, plan_errors, gcode, findings, export_ok, tool_numbers } = job
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
        {skipped.map((s, i) => <p key={i} className="muted small">Skipped Ø{fmt(s.diameter)}: {s.reason}</p>)}
      </section>

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
                    <strong>T{t} {op.op === 'spot_drill' ? 'Spot drill' : 'Drill'}</strong>{' '}
                    <span className="muted">{op.tool_id}{op.op === 'drill' && ` · ${op.cycle}${op.cycle === 'G83' ? ` peck ${fmt(op.peck)}` : ''}`} · holes {op.hole_ids.join(', ')}</span>
                    <p className="small">{op.reason}</p>
                  </div>
                </li>
              )
            })}
          </ol>
          {(plan.warnings ?? []).map((w, i) => <p key={i} className="note small">{w}</p>)}
        </section>
      )}

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
          <button onClick={onDownload} disabled={!export_ok}>Download .nc</button>
          {!export_ok && <p className="muted small">Download is blocked until every check passes.</p>}
          <details>
            <summary>G-code ({gcode.split('\n').length - 1} lines)</summary>
            <pre>{gcode}</pre>
          </details>
        </section>
      )}
    </>
  )
}
