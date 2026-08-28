import React, { useMemo, useRef, useState, useEffect } from 'react'
import { useAsync } from './common'

/**
 * A real map, not a dot cloud: Indian state outlines behind the points, with the three
 * pilot states picked out and everything else drawn as context. Same equirectangular
 * projection with the cos(lat) correction that IndiaMap uses, so a point sits where the
 * place actually is rather than where a min-max rescale of the selection put it.
 *
 * The view is fitted to the pilot states, so neighbouring states show at the edges and
 * the working area fills the frame.
 */
type Ring = [number, number][]
/**
 * The three shape levels do not share a schema: india.json has `rings` and a `pilot` flag,
 * state files have `rings` but no flag, and district files carry a flat `cell` per block
 * plus a district `outline`. `normalise` hides that so the drawing code sees one shape.
 */
type RawFeature = {
  id: string; name: string; pilot?: boolean
  rings?: Ring[]; cell?: Ring; units?: number
}
type RawShapes = {
  features: RawFeature[]
  // District files carry the real boundary here, beside their synthetic blocks.
  outline?: Ring[]; district_id?: string; district?: string
}
type Feature = { id: string; name: string; pilot: boolean; rings: Ring[]; units?: number }
export type MapLevel = 'india' | 'state' | 'district'

function normalise(f: RawFeature, level: MapLevel): Feature {
  return {
    id: f.id, name: f.name, units: f.units,
    rings: f.rings ?? (f.cell ? [f.cell] : []),
    // Only the India file marks pilot states; below that everything on screen is in scope.
    pilot: f.pilot ?? level !== 'india',
  }
}

export type MapPoint = {
  id: string
  name: string
  lon: number
  lat: number
  value: number            // drives radius
  color: string
  sub?: string             // second tooltip line
}

// The viewBox has to match the box the SVG is actually rendered into. Guess the aspect
// and the browser letterboxes the map, shrinking the pilot states into the middle of a
// mostly empty frame -- so measure the container instead of assuming a width.
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    // Only react to real width changes. Feeding every sub-pixel resize back into state
    // re-renders the map, which re-projects every polygon -- and that can chase its own
    // tail until the tab locks up.
    const ro = new ResizeObserver(([e]) => {
      const next = Math.round(e.contentRect.width)
      setW(w => (Math.abs(w - next) > 1 ? next : w))
    })
    ro.observe(el)
    setW(Math.round(el.getBoundingClientRect().width))
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

export function GeoMap({ points, selected, onSelect, height = 420, legend,
                        level = 'india', parent, onDrill, selectedShape, shapeFill,
                        shapeTitle }: {
  points: MapPoint[]
  selected?: string
  onSelect?: (id: string) => void
  height?: number
  legend?: React.ReactNode
  level?: MapLevel
  parent?: string
  onDrill?: (id: string, name: string) => void
  selectedShape?: string
  // Colour a polygon by a value -- returns undefined to leave it at the default tint, so a
  // choropleth caller can also say "no data here" by simply not returning a colour.
  shapeFill?: (id: string) => string | undefined
  shapeTitle?: (id: string, name: string) => string
}) {
  const url = `/api/shapes/${level}` + (parent ? `?parent=${encodeURIComponent(parent)}` : '')
  const raw = useAsync(() => fetch(url).then(r => r.json()) as Promise<RawShapes>, [url])
  const shapes = {
    ...raw,
    // A district file's `features` are three synthetic block quadrilaterals -- drawing them
    // covers the district in a rectangle that is not a place. The real boundary is `outline`,
    // and inside it the things worth seeing are the micro-market points, so at this level the
    // map is one shape: the district itself.
    data: raw.data ? {
      features: level === 'district' && raw.data.outline
        ? [{ id: raw.data.district_id ?? parent ?? '', name: raw.data.district ?? '',
             pilot: true, rings: raw.data.outline }]
        : raw.data.features.map(f => normalise(f, level)),
    } : undefined,
  }

  const [box, W] = useWidth<HTMLDivElement>()
  const H = height
  const proj = useMemo(() => {
    const feats = shapes.data?.features ?? []
    // At India level the three pilot states are the subject and the rest is context, so the
    // fit ignores the context. Drilled in, every shape on screen is the subject.
    const pilot = level === 'india' ? feats.filter(f => f.pilot) : feats
    const pts: [number, number][] = []
    for (const f of (pilot.length ? pilot : feats)) for (const r of f.rings) for (const c of r) pts.push(c)
    if (!pts.length) return null
    const lons = pts.map(p => p[0]), lats = pts.map(p => p[1])
    const pad = 0.18
    let lo0 = Math.min(...lons), lo1 = Math.max(...lons)
    let la0 = Math.min(...lats), la1 = Math.max(...lats)
    const dx = (lo1 - lo0) * pad, dy = (la1 - la0) * pad
    lo0 -= dx; lo1 += dx; la0 -= dy; la1 += dy
    // Longitude degrees shrink with latitude; without this India leans.
    const k = Math.cos(((la0 + la1) / 2) * Math.PI / 180)
    const spanX = (lo1 - lo0) * k, spanY = la1 - la0
    const s = Math.min(W / spanX, H / spanY)
    const ox = (W - spanX * s) / 2, oy = (H - spanY * s) / 2
    return {
      x: (lon: number) => ox + (lon - lo0) * k * s,
      y: (lat: number) => oy + (la1 - lat) * s,
    }
  }, [shapes.data, H, W, level])

  // India's outlines are ~500k coordinates. Projecting them is cheap once and ruinous on
  // every render, so the path strings are built only when the geometry or the frame moves.
  // This has to sit above any early return: a hook skipped on the error path desynchronises
  // React's hook order for the whole component.
  const shapePaths = useMemo(() => {
    if (!proj) return []
    const draw = (rings: Ring[]) => rings
      .map(ring => 'M' + ring.map(([lo, la]) =>
        `${proj.x(lo).toFixed(1)},${proj.y(la).toFixed(1)}`).join('L') + 'Z')
      .join(' ')
    return (shapes.data?.features ?? []).map(f => ({ ...f, d: draw(f.rings) }))
  }, [shapes.data, proj])

  if (shapes.err) return <div className="err">{String(shapes.err)}</div>

  const ready = proj !== null && W > 0
  const maxV = Math.max(...points.map(p => p.value), 1)
  const r = (v: number) => 2.2 + 9 * Math.sqrt(Math.max(v, 0) / maxV)

  return (
    <div ref={box}>
      {!ready && <div className="loading" style={{ height }}>loading map…</div>}
      {ready && <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height, display: 'block' }}
           role="img" aria-label="Map of the pilot states">
        {/* Two passes: the out-of-scope states are backdrop and must never sit on top of a
            shape you can click, and at India level a state's rings are its districts, so
            drawing fills and outlines together leaves a scribble inside every state. */}
        {shapePaths.filter(f => !f.pilot).map(f => (
          <path key={f.id} d={f.d} fill="var(--panel)" stroke="var(--border)"
                strokeWidth={0.5} opacity={0.35} />
        ))}
        {shapePaths.filter(f => f.pilot).map(f => {
          const on = f.id === selectedShape
          const paint = shapeFill?.(f.id)
          const fill = on ? 'var(--accent-soft)' : (paint ?? 'rgba(122,162,247,.16)')
          return (
            <path key={f.id} d={f.d} fill={fill}
                  // A state on the India map is the union of its district rings, never
                  // dissolved, so stroking them draws every internal border. Matching the
                  // stroke to the fill hides the seams and leaves the silhouette, which is
                  // all this level is asking you to click.
                  stroke={on ? 'var(--accent)'
                          : level === 'india' && !paint ? fill : 'var(--border-strong)'}
                  strokeWidth={on ? 2 : level === 'india' && !paint ? 1 : 0.8}
                  style={{ cursor: onDrill ? 'pointer' : 'default' }}
                  onClick={() => onDrill?.(f.id, f.name)}>
              {(onDrill || shapeTitle) && <title>{shapeTitle?.(f.id, f.name) ?? f.name}</title>}
            </path>
          )
        })}
        {points.map(p => (
          <circle key={p.id} cx={proj.x(p.lon)} cy={proj.y(p.lat)} r={r(p.value)}
                  fill={p.color} fillOpacity={selected && selected !== p.id ? 0.28 : 0.78}
                  stroke={selected === p.id ? 'var(--text)' : 'rgba(0,0,0,.35)'}
                  strokeWidth={selected === p.id ? 1.8 : 0.5}
                  style={{ cursor: onSelect ? 'pointer' : 'default' }}
                  onClick={() => onSelect?.(p.id)}>
            <title>{`${p.name}${p.sub ? '\n' + p.sub : ''}`}</title>
          </circle>
        ))}
      </svg>}
      {legend && <div className="legend" style={{ marginTop: 8 }}>{legend}</div>}
    </div>
  )
}

export default GeoMap
