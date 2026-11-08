import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'
import { toolColor } from './colors.js'

const SPEEDS = [0.1, 0.25, 0.5, 1, 2, 5]
const DEFAULT_SPEED = 1 // real machine time
const HELIX = (30 * Math.PI) / 180 // flute helix angle of a standard jobber drill
const FLUTE_DEPTH = 0.72 // how far the flutes cut in, as a fraction of the radius
const SPIN = 3 // spindle turns per second on screen (the real rpm would just flicker)
const prefersReducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

export const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

// Give every move and tool change a start and end time, so any moment of the program can be looked up.
function timeline(toolpath) {
  let t = 0
  return toolpath.map((e) => {
    const start = t
    t += e.seconds
    return { ...e, start, end: t }
  })
}

const TOOL_NAMES = { spot_drill: 'spot drill', drill: 'drill', reamer: 'reamer' }
const toolName = (n, info) => `T${n} · ${info?.diameter} mm ${TOOL_NAMES[info?.type] ?? 'tool'}`
const VERBS = { spot_drill: 'Spot drilling', drill: 'Drilling', reamer: 'Reaming' }

// A plain-words description of each move, e.g. "Drilling hole 4 · peck 2 of 5" or "Pulling out to clear chips".
export function describeSteps(events, holes, toolInfo) {
  const near = (a, b) => Math.abs(a[0] - b[0]) < 0.01 && Math.abs(a[1] - b[1]) < 0.01
  const holeAt = (p) => holes.find((h) => near([h.x, h.y], p))
  const key = (e) => `${e.line} ${e.to[0]} ${e.to[1]}`
  const feeds = {}
  const down = (e) => e.kind === 'feed' && e.to[2] < e.from[2] // a reamer also feeds back out
  for (const e of events) if (down(e)) feeds[key(e)] = (feeds[key(e)] ?? 0) + 1
  const done = {}
  return events.map((e) => {
    const tool = toolName(e.tool, toolInfo[e.tool])
    if (e.kind === 'tool_change') return { what: `Tool change: ${tool}`, tool: 'Swapping tools in the spindle' }
    const hole = holeAt(e.to)
    const where = hole ? `hole ${hole.id}` : 'the next position'
    const k = key(e)
    if (e.kind === 'feed' && !down(e)) return { what: `Feeding back out of ${where}`, tool }
    if (e.kind === 'feed') {
      done[k] = (done[k] ?? 0) + 1
      const verb = VERBS[toolInfo[e.tool]?.type] ?? 'Drilling'
      return { what: `${verb} ${where}${feeds[k] > 1 ? ` · peck ${done[k]} of ${feeds[k]}` : ''}`, tool }
    }
    if (!near(e.from, e.to)) return { what: `Moving to ${where}`, tool }
    if (e.to[2] > e.from[2]) {
      return { what: done[k] && done[k] < feeds[k] ? 'Pulling out to clear chips' : `Pulling out of ${where}`, tool }
    }
    return { what: done[k] ? 'Back down to keep drilling' : `Down to the start of ${where}`, tool }
  })
}

// The event running at time t, and where the tool tip is (null during a tool change).
function stateAt(events, t) {
  let i = events.findIndex((e) => e.end > t)
  if (i === -1) i = events.length - 1
  const e = events[i]
  const k = e.seconds > 0 ? Math.min(1, Math.max(0, (t - e.start) / e.seconds)) : 1
  const tip = e.kind === 'tool_change' ? null : e.from.map((v, j) => v + (e.to[j] - v) * k)
  return { i, e, tip }
}

// Fluted part of a cutting tool, tip at the origin pointing down: a fluted cross-section swept up a right-hand
// helix, shrinking over the first tipLength mm (to a point for a drill, to a short chamfer for a reamer).
// A drill has 2 flutes on a helix; a reamer has 6 straight ones.
function fluteGeometry(r, tipLength, fluteLength, { flutes = 2, helix = HELIX, tipScale = 0 } = {}) {
  const around = 48
  const lead = helix ? (2 * Math.PI * r) / Math.tan(helix) : Infinity
  const zs = Array.from({ length: 8 }, (_, i) => (tipLength * i) / 8)
  for (let z = tipLength; z < fluteLength; z += 0.5) zs.push(z)
  zs.push(fluteLength)
  const position = []
  for (const z of zs) {
    const scale = Math.min(1, tipScale + ((1 - tipScale) * z) / tipLength)
    const twist = (z / lead) * 2 * Math.PI
    for (let j = 0; j < around; j++) {
      const a = (j / around) * 2 * Math.PI
      const rho = r * scale * (1 - FLUTE_DEPTH * Math.max(0, Math.sin(flutes * a)) ** 1.5)
      position.push(rho * Math.cos(a + twist), rho * Math.sin(a + twist), z)
    }
  }
  const index = []
  for (let i = 0; i < zs.length - 1; i++) {
    for (let j = 0; j < around; j++) {
      const a = i * around + j, b = i * around + ((j + 1) % around)
      index.push(a, b, b + around, a, b + around, a + around)
    }
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(position, 3))
  g.setIndex(index)
  g.computeVertexNormals()
  return g
}

const zCylinder = (radius, height, bottom) =>
  new THREE.CylinderGeometry(radius, radius, height, 48).rotateX(Math.PI / 2).translate(0, 0, bottom + height / 2)

// A drill or reamer in its collet: fluted body, a little bare shank, then the collet nut (in the tool's plan
// color) and holder.
function toolModel(number, info, envMap) {
  const r = info.diameter / 2
  const reamer = info.type === 'reamer'
  const tipLength = reamer ? 0.4 : r / Math.tan(((info.point_angle ?? 118) / 2) * (Math.PI / 180))
  const fluteLength = info.flute_length ?? info.diameter * 5
  const steel = new THREE.MeshStandardMaterial({ color: 0xd3d8de, metalness: 0.9, roughness: 0.3, envMap })
  const holder = new THREE.MeshStandardMaterial({ color: 0x6b737d, metalness: 0.8, roughness: 0.4, envMap })
  const nut = new THREE.MeshStandardMaterial({ color: toolColor(number), metalness: 0.5, roughness: 0.4, envMap })
  const nutRadius = r + 7
  const nutBottom = fluteLength + 6
  const group = new THREE.Group()
  group.add(
    new THREE.Mesh(fluteGeometry(r, tipLength, fluteLength, reamer ? { flutes: 6, helix: 0, tipScale: 0.85 } : {}), steel),
    new THREE.Mesh(zCylinder(r, 6, fluteLength), steel),
    new THREE.Mesh(zCylinder(nutRadius, 12, nutBottom), nut),
    new THREE.Mesh(zCylinder(nutRadius + 5, 30, nutBottom + 12), holder),
  )
  return group
}

// Part mesh plus toolpath, with a playback of the program. Z is up; the origin is G54 (front-left corner, top of the part).
export default function Viewer({ job, onLine }) {
  const ref = useRef(null)
  const events = useMemo(() => timeline(job.toolpath), [job])
  const total = events.length ? events[events.length - 1].end : 0
  const lines = useMemo(() => (job.gcode ?? '').split('\n'), [job])
  const steps = useMemo(() => describeSteps(events, job.holes, job.tool_info ?? {}), [events, job])
  const play = useRef({ t: 0, playing: false, speed: DEFAULT_SPEED })
  const [ui, setUi] = useState({ t: 0, playing: false, speed: DEFAULT_SPEED, i: 0 })

  useEffect(() => {
    const el = ref.current
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

    const tools = {}
    let shownTool = null
    const showTool = (number, tip) => {
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
    let lastLine = null
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
        // M03 is clockwise seen from above, so the flutes appear to climb as they would on the machine.
        if (p.playing) tool.rotation.z -= dt * SPIN * 2 * Math.PI
        for (const bore of bores) {
          let cut = bore.top
          for (const c of bore.cuts) {
            if (c > i) break
            cut = Math.min(cut, c === i ? tip[2] : events[c].to[2])
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
      scene.traverse((o) => { o.geometry?.dispose(); o.material?.dispose() })
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
  const seek = (e) => {
    play.current.t = Number(e.target.value)
    setUi((u) => ({ ...u, t: play.current.t }))
  }
  const setSpeed = (e) => {
    play.current.speed = Number(e.target.value)
    setUi((u) => ({ ...u, speed: play.current.speed }))
  }

  return (
    <div ref={ref} className="viewer">
      <span className="sr-only">3D view of the part with the drilling toolpath and a playback of the program</span>
      {events.length > 0 && (
        <>
          <div className="step">
            <p className="step-what">{steps[ui.i].what}</p>
            <p className="step-tool">{steps[ui.i].tool}</p>
            <code className="step-line">N{events[ui.i].line}  {lines[events[ui.i].line - 1]}</code>
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
