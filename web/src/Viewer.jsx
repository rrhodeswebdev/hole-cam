import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { toolColor } from './colors.js'

// Part mesh plus toolpath. Z is up and the origin is G54 (front-left corner, top of the part).
export default function Viewer({ mesh, toolpath }) {
  const ref = useRef(null)

  useEffect(() => {
    const el = ref.current
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(window.devicePixelRatio)
    el.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(getComputedStyle(el).getPropertyValue('--viewer-bg').trim())
    scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 2.2))
    const sun = new THREE.DirectionalLight(0xffffff, 1.4)
    sun.position.set(1, -1.5, 2)
    scene.add(sun, new THREE.AxesHelper(15))

    const part = new THREE.Group()
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(mesh.vertices.flat(), 3))
    geometry.setIndex(mesh.triangles.flat())
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
    for (const m of toolpath) {
      const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...m.from), new THREE.Vector3(...m.to)])
      const color = toolColor(m.tool)
      const line = m.kind === 'feed'
        // Cuts happen inside the part; draw them on top so they aren't hidden behind its faces.
        ? new THREE.Line(g, new THREE.LineBasicMaterial({ color, depthTest: false }))
        : new THREE.Line(g, new THREE.LineDashedMaterial({ color, dashSize: 1.2, gapSize: 1.2, transparent: true, opacity: 0.55 }))
      if (m.kind === 'feed') line.renderOrder = 1
      else line.computeLineDistances()
      scene.add(line)
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
    renderer.setAnimationLoop(() => {
      controls.update()
      renderer.render(scene, camera)
    })

    return () => {
      observer.disconnect()
      renderer.setAnimationLoop(null)
      controls.dispose()
      scene.traverse((o) => { o.geometry?.dispose(); o.material?.dispose() })
      renderer.dispose()
      el.removeChild(renderer.domElement)
    }
  }, [mesh, toolpath])

  return <div ref={ref} className="viewer" role="img" aria-label="3D view of the part with the drilling toolpath" />
}
