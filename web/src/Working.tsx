import { useEffect, useState } from 'react'
import { clock } from './format.ts'

// Over the 3D area while Claude works: what's happening and how long it's taken.
export default function Working({ what, drawing }: { what: 'plan' | 'revise'; drawing?: File | null }) {
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
