import { useEffect, useState, type FormEvent } from 'react'
import { getMaterials, postJob } from './api.ts'
import ChangePlan, { type Busy, type Revision } from './ChangePlan.tsx'
import Results from './Results.tsx'
import Viewer from './Viewer.tsx'
import Working from './Working.tsx'
import type { Job } from './types.ts'

type Planned = { file: File; drawing: File | null; material: string }

export default function App() {
  const [materials, setMaterials] = useState<Record<string, string>>({})
  const [material, setMaterial] = useState('6061')
  const [file, setFile] = useState<File | null>(null)
  const [drawing, setDrawing] = useState<File | null>(null) // optional PDF
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState<string | null>(null)
  const [activeLine, setActiveLine] = useState<number | null>(null)
  const [planned, setPlanned] = useState<Planned | null>(null) // the file, drawing and material the current job came from
  const [revision, setRevision] = useState<Revision | null>(null)

  useEffect(() => {
    getMaterials()
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
      setJob(await postJob('/jobs', body))
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
      const data = await postJob('/jobs/revise', body)
      const summary = data.summary ?? ''
      if (data.plan_errors.length) {
        // Keep the working plan; show what Claude tried and why it was rejected.
        setRevision({ request, summary, errors: data.plan_errors })
      } else {
        setRevision({ request, summary, previous: job })
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
