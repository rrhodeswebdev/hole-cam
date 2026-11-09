import { useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'
import { toolColor } from './colors.ts'
import { clock } from './format.ts'
import { describeSteps, stateAt, timeline } from './playback.ts'
import { toolModel } from './toolModel.ts'
import type { Job, Move, Vec3 } from './types.ts'

const SPEEDS = [0.1, 0.25, 0.5, 1, 2, 5]
const DEFAULT_SPEED = 1 // real machine time
const SPIN = 3 // spindle turns per second on screen (the real rpm would just flicker)
const prefersReducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

// Part mesh plus toolpath, with a playback of the program. Z is up; the origin is G54 (front-left corner, top of the part).
export default function Viewer({ job, onLine }: { job: Job; onLine: (line: number) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const events = useMemo(() => timeline(job.toolpath), [job])
  const total = events.length ? events[events.length - 1].end : 0
  const lines = useMemo(() => (job.gcode ?? '').split('\n'), [job])
  const steps = useMemo(() => describeSteps(events, job.holes, job.tool_info), [events, job])
  const play = useRef({ t: 0, playing: false, speed: DEFAULT_SPEED })
  const [ui, setUi] = useState({ t: 0, playing: false, speed: DEFAULT_SPEED, i: 0 })
  // ui is a frame behind when job changes (revise, undo); the new program can have fewer moves.
  const i = Math.min(ui.i, events.length - 1)

  useEffect(() => {
    const el = ref.current!
    play.current = { t: 0, playing: events.length > 0 && !prefersReducedMotion(), speed: play.current.speed }

    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(window.devicePixelRatio)
    el.prepend(renderer.domElement)

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(getComputedStyle(el).getPropertyValue('--viewer-bg').trim())
    scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 2.2))
    const sun = new THREE.DirectionalLight(0xffffff, 1.4)
    sun.position.set(1, -1.5, 2)
    scene.add(sun, new THREE.AxesHelper(15))
    // Reflections for the tools only, so the steel reads as metal; the part keeps its flat look.
    const pmrem = new THREE.PMREMGenerator(renderer)
    const envMap = pmrem.fromScene(new RoomEnvironment(), 0.04).texture

    const part = new THREE.Group()
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(job.mesh.vertices.flat(), 3))
    geometry.setIndex(job.mesh.triangles.flat())
    geometry.computeVertexNormals()
    part.add(
      new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
        color: 0x9aa5b1, metalness: 0.2, roughness: 0.6, transparent: true, opacity: 0.5,
        side: THREE.DoubleSide, depthWrite: false,
      })),
      new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 25), new THREE.LineBasicMaterial({ color: 0xc9d1d9 })),
    )
    scene.add(part)

    // shortcut: one Line per move; fine for drilling programs, batch into one geometry if programs get long.
    for (const m of events) {
      if (m.kind === 'tool_change') continue
      const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...m.from), new THREE.Vector3(...m.to)])
      const color = toolColor(m.tool)
      const line = m.kind === 'feed'
        // Cuts happen inside the part; draw them on top so they aren't hidden behind its faces.
        ? new THREE.Line(g, new THREE.LineBasicMaterial({ color, depthTest: false, transparent: true, opacity: 0.8 }))
        : new THREE.Line(g, new THREE.LineDashedMaterial({ color, dashSize: 1.2, gapSize: 1.2, transparent: true, opacity: 0.4 }))
      if (m.kind === 'feed') line.renderOrder = 1
      else line.computeLineDistances()
      scene.add(line)
    }

    // Each hole's bore fills in, in the drill's color, as a drill (not a spot drill) feeds down through it.
    const bores = events.length === 0 ? [] : job.holes.map((h) => {
      const top = h.top_z - job.part.top_z
      const cuts = events.flatMap((e, i) =>
        e.kind === 'feed' && job.tool_info[e.tool]?.type === 'drill' &&
        Math.abs(e.to[0] - h.x) < 0.01 && Math.abs(e.to[1] - h.y) < 0.01 ? [i] : [])
      const color = cuts.length ? toolColor(events[cuts[0]].tool) : 0xffffff
      const mesh = new THREE.Mesh(
        new THREE.CylinderGeometry(h.diameter / 2, h.diameter / 2, 1, 32).rotateX(Math.PI / 2).translate(0, 0, -0.5),
        new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.35, transparent: true, opacity: 0.85 }))
      mesh.position.set(h.x, h.y, top)
      scene.add(mesh)
      return { mesh, top, bottom: top - h.depth, cuts }
    })

    const tools: Record<number, THREE.Group> = {}
    let shownTool: number | null = null
    const showTool = (number: number, tip: Vec3 | null) => {
      if (shownTool !== number) {
        if (shownTool !== null) tools[shownTool].visible = false
        if (!tools[number]) scene.add(tools[number] = toolModel(number, job.tool_info[number], envMap))
        shownTool = number
      }
      tools[number].visible = tip !== null
      if (tip) tools[number].position.set(...tip)
      return tools[number]
    }

    // Frame the part, not the clearance moves above it.
    const box = new THREE.Box3().setFromObject(part)
    const size = box.getSize(new THREE.Vector3()).length()
    const center = box.getCenter(new THREE.Vector3())
    const camera = new THREE.PerspectiveCamera(40, 1, size / 100, size * 20)
    camera.up.set(0, 0, 1)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.target.copy(center)

    const resize = () => {
      renderer.setSize(el.clientWidth, el.clientHeight)
      camera.aspect = el.clientWidth / el.clientHeight
      camera.updateProjectionMatrix()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(el)
    resize()
    // Back off further on narrow (portrait) screens so the whole part fits across.
    const distance = size * Math.max(1, 1.4 / camera.aspect)
    camera.position.copy(center).add(new THREE.Vector3(0.7, -1.1, 0.9).multiplyScalar(distance))

    let last = performance.now()
    let lastUi = 0
    let lastLine: number | null = null
    renderer.setAnimationLoop(() => {
      const now = performance.now()
      const p = play.current
      const dt = (now - last) / 1000
      if (p.playing) {
        p.t = Math.min(total, p.t + dt * p.speed)
        if (p.t >= total) p.playing = false
      }
      last = now

      if (events.length) {
        const { i, e, tip } = stateAt(events, p.t)
        // At the end the tool has gone home (G28), which the trace doesn't place in work coordinates.
        const tool = showTool(e.tool, p.t >= total ? null : tip)
        // M03 is clockwise seen from above, so the flutes appear to climb as they would on the machine. A tap
        // backs out with the spindle reversed.
        const reversed = job.tool_info[e.tool]?.type === 'tap' && e.kind === 'feed' && e.to[2] > e.from[2]
        if (p.playing) tool.rotation.z -= (reversed ? -1 : 1) * dt * SPIN * 2 * Math.PI
        for (const bore of bores) {
          let cut = bore.top
          for (const c of bore.cuts) {
            if (c > i) break
            cut = Math.min(cut, c === i ? tip![2] : (events[c] as Move).to[2])
          }
          // The drill tip goes past the bottom of the hole (point, breakthrough); the bore stops at the hole.
          const depth = bore.top - Math.max(cut, bore.bottom)
          bore.mesh.visible = depth > 0.01
          bore.mesh.scale.z = Math.max(depth, 0.01)
        }
        if (e.line !== lastLine) {
          lastLine = e.line
          onLine(e.line)
        }
        if (now - lastUi > 100 || !p.playing) {
          lastUi = now
          setUi((u) => (u.t === p.t && u.playing === p.playing && u.i === i ? u : { ...u, t: p.t, playing: p.playing, i }))
        }
      }
      controls.update()
      renderer.render(scene, camera)
    })

    return () => {
      observer.disconnect()
      renderer.setAnimationLoop(null)
      controls.dispose()
      scene.traverse((o) => {
        if (!(o instanceof THREE.Mesh || o instanceof THREE.Line)) return
        o.geometry.dispose()
        for (const m of [o.material].flat()) m.dispose()
      })
      envMap.dispose()
      pmrem.dispose()
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [job, events, total, onLine])

  const toggle = () => {
    const p = play.current
    if (!p.playing && p.t >= total) p.t = 0
    p.playing = !p.playing
    setUi((u) => ({ ...u, playing: p.playing, t: p.t }))
  }
  const seek = (e: ChangeEvent<HTMLInputElement>) => {
    play.current.t = Number(e.target.value)
    setUi((u) => ({ ...u, t: play.current.t }))
  }
  const setSpeed = (e: ChangeEvent<HTMLSelectElement>) => {
    play.current.speed = Number(e.target.value)
    setUi((u) => ({ ...u, speed: play.current.speed }))
  }

  return (
    <div ref={ref} className="viewer">
      <span className="sr-only">3D view of the part with the drilling toolpath and a playback of the program</span>
      {events.length > 0 && (
        <>
          <div className="step">
            <p className="step-what">{steps[i].what}</p>
            <p className="step-tool">{steps[i].tool}</p>
            <code className="step-line">N{events[i].line}  {lines[events[i].line - 1]}</code>
          </div>
          <p className="legend small"><span className="dash" /> rapid <span className="solid" /> cutting</p>
          <div className="sim" role="group" aria-label="Machining simulation">
            <div className="sim-controls">
              <button type="button" onClick={toggle}>{ui.playing ? 'Pause' : ui.t >= total ? 'Replay' : 'Play'}</button>
              <input type="range" min={0} max={total} step="any" value={ui.t} onChange={seek} aria-label="Program time" />
              <span className="time">{clock(ui.t)} / {clock(total)}</span>
              <select value={ui.speed} onChange={setSpeed} aria-label="Playback speed">
                {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
              </select>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
