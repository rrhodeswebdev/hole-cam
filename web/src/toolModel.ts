// 3D models of the cutting tools for the playback: drills, reamers and taps in a collet holder.
import * as THREE from 'three'
import { toolColor } from './colors.ts'
import type { ToolInfo } from './types.ts'

const HELIX = (30 * Math.PI) / 180 // flute helix angle of a standard jobber drill
const FLUTE_DEPTH = 0.72 // how far the flutes cut in, as a fraction of the radius
const THREAD_DEPTH = 0.12 // a tap's thread depth, as a fraction of the radius

// Fluted part of a cutting tool, tip at the origin pointing down: a fluted cross-section swept up a right-hand
// helix, shrinking over the first tipLength mm (to a point for a drill, to a short chamfer for a reamer or tap).
// A drill has 2 flutes on a helix; a reamer has 6 straight ones; a tap has 4 straight ones and a thread of the given pitch.
function fluteGeometry(r: number, tipLength: number, fluteLength: number,
  { flutes = 2, helix = HELIX, tipScale = 0, pitch = 0 }: { flutes?: number; helix?: number; tipScale?: number; pitch?: number } = {}) {
  const around = 48
  const lead = helix ? (2 * Math.PI * r) / Math.tan(helix) : Infinity
  const zs = Array.from({ length: 8 }, (_, i) => (tipLength * i) / 8)
  for (let z = tipLength; z < fluteLength; z += pitch ? pitch / 8 : 0.5) zs.push(z)
  zs.push(fluteLength)
  const position = []
  for (const z of zs) {
    const scale = Math.min(1, tipScale + ((1 - tipScale) * z) / tipLength)
    const twist = (z / lead) * 2 * Math.PI
    for (let j = 0; j < around; j++) {
      const a = (j / around) * 2 * Math.PI
      // A right-hand thread: the crest is where the angle around and the height agree, one pitch per turn.
      const thread = pitch ? 1 - THREAD_DEPTH * (0.5 + 0.5 * Math.cos(2 * Math.PI * (z / pitch) - a)) : 1
      const rho = r * scale * thread * (1 - FLUTE_DEPTH * Math.max(0, Math.sin(flutes * a)) ** 1.5)
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

const zCylinder = (radius: number, height: number, bottom: number) =>
  new THREE.CylinderGeometry(radius, radius, height, 48).rotateX(Math.PI / 2).translate(0, 0, bottom + height / 2)

// A drill, reamer or tap in its collet: fluted body, a little bare shank, then the collet nut (in the tool's plan
// color) and holder.
export function toolModel(number: number, info: ToolInfo, envMap: THREE.Texture) {
  const r = info.diameter / 2
  const reamer = info.type === 'reamer'
  const tap = info.type === 'tap'
  const tipLength = reamer ? 0.4 : tap ? 3 * (info.pitch ?? 0) : r / Math.tan(((info.point_angle ?? 118) / 2) * (Math.PI / 180))
  const shape = reamer ? { flutes: 6, helix: 0, tipScale: 0.85 } : tap ? { flutes: 4, helix: 0, tipScale: 0.75, pitch: info.pitch ?? 0 } : {}
  const fluteLength = info.flute_length ?? info.diameter * 5
  const steel = new THREE.MeshStandardMaterial({ color: 0xd3d8de, metalness: 0.9, roughness: 0.3, envMap })
  const holder = new THREE.MeshStandardMaterial({ color: 0x6b737d, metalness: 0.8, roughness: 0.4, envMap })
  const nut = new THREE.MeshStandardMaterial({ color: toolColor(number), metalness: 0.5, roughness: 0.4, envMap })
  const nutRadius = r + 7
  const nutBottom = fluteLength + 6
  const group = new THREE.Group()
  group.add(
    new THREE.Mesh(fluteGeometry(r, tipLength, fluteLength, shape), steel),
    new THREE.Mesh(zCylinder(r, 6, fluteLength), steel),
    new THREE.Mesh(zCylinder(nutRadius, 12, nutBottom), nut),
    new THREE.Mesh(zCylinder(nutRadius + 5, 30, nutBottom + 12), holder),
  )
  return group
}
