// Calls to the API (src/hole_cam/api.py). In dev, Vite proxies these paths to it.
import type { Job } from './types.ts'

// Material id -> display name.
export async function getMaterials(): Promise<Record<string, string>> {
  const r = await fetch('/materials')
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

// POST /jobs or /jobs/revise. Throws with the server's message when it rejects the request.
export async function postJob(path: '/jobs' | '/jobs/revise', body: FormData): Promise<Job> {
  const r = await fetch(path, { method: 'POST', body })
  const data: unknown = await r.json().catch(() => ({}))
  if (!r.ok) {
    const detail = (data as { detail?: unknown }).detail
    throw new Error(typeof detail === 'string' ? detail : `The server rejected the request (HTTP ${r.status}).`)
  }
  return data as Job
}
