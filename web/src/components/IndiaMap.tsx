import React, { useMemo, useRef, useState, useEffect } from 'react'
import { fmt } from '../lib/api'

type Level = 'india' | 'state' | 'district' | 'village'
export interface MapNode { level: Level; id: string; name: string }

interface Shape {
  id: string; name: string; rings?: number[][][]; cell?: number[][] | null
  lon?: number; lat?: number; units?: number | null; headroom?: number | null
  top_sku?: string; pilot?: boolean; shared?: boolean
  action_segment?: string; archetype?: string; opportunity?: number
}

/** Sequential ramp, light→dark. Kept deliberately short so steps stay distinguishable. */
const RAMP = ['#e8eef6', '#c5d8ec', '#98bcdf', '#6b9dd0', '#4079bd', '#22559c', '#123a72']
const NO_DATA = 'var(--map-nodata)'

const ACTION_COLOR: Record<string, string> = {
  'Convert now': '#1a7f4b', 'Build access': '#1f6feb',
  'Defend': '#8e44ad', 'Monitor': '#8b98a6',
}

/**
 * Drill-down map of India: country → state → district → block → village.
 *
 * Geometry comes pre-simplified per zoom level, and the value that colours each shape
 * is joined server-side in the same request — so a shape can never show a number from a
 * different filter, which is the commonest way a choropleth quietly misleads.
 *
 * Blocks have no real boundaries (they are constructed), so their Voronoi cells are
 * clipped to the true district outline with an SVG clipPath: real edge, derived interior.
 */
export default function IndiaMap({
  path, onDrill, onUp, sku, category, month, height = 560, onSelectVillage,
}: {
  path: MapNode[]
  onDrill: (n: MapNode) => void
  onUp: (index: number) => void
  sku?: string; category?: string; month?: number
  height?: number
  onSelectVillage?: (id: string) => void
}) {
  const level: Level = path.length === 0 ? 'india'
    : path.length === 1 ? 'state'
    : path.length === 2 ? 'district' : 'village'
  const parent = path.length ? path[path.length - 1].id : undefined

  const [data, setData] = useState<{ features: Shape[]; outline?: number[][][] } | null>(null)
  const [err, setErr] = useState<string>()
  const [loading, setLoading] = useState(false)
  const [hover, setHover] = useState<{ s: Shape; x: number; y: number } | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const qs = new URLSearchParams()
    if (sku) qs.set('sku', sku)
    if (category) qs.set('category', category)
    if (month) qs.set('month', String(month))
    const url = level === 'india' ? `/api/shapes/india?${qs}`
      : level === 'village' ? `/api/shapes/villages/${parent}?${qs}`
      : `/api/shapes/${level}?parent=${encodeURIComponent(parent!)}&${qs}`
    let live = true
    setLoading(true); setErr(undefined)
    fetch(url).then(r => r.ok ? r.json() : Promise.reject(new Error(String(r.status))))
      .then(d => { if (live) { setData(d); setLoading(false) } })
      .catch(e => { if (live) { setErr(String(e)); setLoading(false) } })
    return () => { live = false }
  }, [level, parent, sku, category, month])

  // ---- projection: fit whatever is on screen into the viewbox -------------
  const geo = useMemo(() => {
    if (!data) return null
    const pts: number[][] = []
    data.features.forEach(f => {
      f.rings?.forEach(r => pts.push(...r))
      if (f.cell) pts.push(...f.cell)
      if (f.lon != null && f.lat != null) pts.push([f.lon, f.lat])
    })
    data.outline?.forEach(r => pts.push(...r))
    if (!pts.length) return null
    const lon = pts.map(p => p[0]), lat = pts.map(p => p[1])
    const lo0 = Math.min(...lon), lo1 = Math.max(...lon)
    const la0 = Math.min(...lat), la1 = Math.max(...lat)
    const midLat = (la0 + la1) / 2
    // equirectangular with a cos(lat) correction — India spans enough latitude that
    // ignoring it visibly stretches the north
    const kx = Math.cos((midLat * Math.PI) / 180)
    const W = (lo1 - lo0) * kx, H = la1 - la0
    const pad = 0.04
    const scale = Math.min(1 / (W * (1 + pad)), 1 / (H * (1 + pad)))
    const x = (l: number) => ((l - lo0) * kx * scale + (1 - W * scale) / 2) * 1000
    const y = (l: number) => ((la1 - l) * scale + (1 - H * scale) / 2) * 1000
    return { x, y }
  }, [data])

  /**
   * Colour scale. Quantile bins spread a skewed distribution well, but with few shapes
   * or a narrow range they exaggerate trivial gaps — three states within 5% of each
   * other rendered as lightest-vs-darkest reads as a large difference that is not there.
   * So: linear below 8 shapes or when the spread is under 40%, quantile otherwise.
   */
  const scale = useMemo(() => {
    const vals = (data?.features ?? [])
      .map(f => f.units ?? 0).filter(v => v > 0).sort((a, b) => a - b)
    if (!vals.length) return () => NO_DATA
    const lo = vals[0], hi = vals[vals.length - 1]
    const linear = vals.length < 8 || (hi - lo) / (hi || 1) < 0.4
    if (linear) {
      // anchor at zero so a bar-like reading of the colour is honest
      return (v?: number | null) => {
        if (v == null || v <= 0) return NO_DATA
        const t = hi > 0 ? v / hi : 0
        return RAMP[Math.min(RAMP.length - 1, Math.floor(t * RAMP.length))]
      }
    }
    const q = (p: number) => vals[Math.min(vals.length - 1, Math.floor(p * vals.length))]
    const stops = [q(0.15), q(0.35), q(0.55), q(0.72), q(0.86), q(0.95)]
    return (v?: number | null) => {
      if (v == null || v <= 0) return NO_DATA
      let i = 0; while (i < stops.length && v > stops[i]) i++
      return RAMP[i]
    }
  }, [data])

  const toPath = (rings: number[][][] | number[][] | undefined) => {
    if (!rings || !geo) return ''
    const rs = (Array.isArray(rings[0]?.[0]) ? rings : [rings]) as number[][][]
    return rs.map(r =>
      r.map((p, i) => `${i ? 'L' : 'M'}${geo.x(p[0]).toFixed(1)} ${geo.y(p[1]).toFixed(1)}`)
        .join('') + 'Z').join(' ')
  }

  const canDrill = level !== 'village'
  const drill = (s: Shape) => {
    if (level === 'village') { onSelectVillage?.(s.id); return }
    const next: Level = level === 'india' ? 'state' : level === 'state' ? 'district' : 'village'
    onDrill({ level: next, id: s.id, name: s.name })
  }

  const move = (e: React.MouseEvent, s: Shape) => {
    const r = wrapRef.current?.getBoundingClientRect()
    setHover({ s, x: e.clientX - (r?.left ?? 0), y: e.clientY - (r?.top ?? 0) })
  }

  const maxU = Math.max(...(data?.features ?? []).map(f => f.units ?? 0), 1)

  return (
    <div className="mapwrap" ref={wrapRef}>
      <div className="mapbar">
        <div className="crumbs">
          <button onClick={() => onUp(-1)} className={path.length ? '' : 'cur'}>India</button>
          {path.map((c, i) => (
            <React.Fragment key={c.id}>
              <span className="sep">›</span>
              {i === path.length - 1
                ? <span className="cur">{c.name}</span>
                : <button onClick={() => onUp(i)}>{c.name}</button>}
            </React.Fragment>
          ))}
        </div>
        <span className="maphint">
          {level === 'india' ? 'Click a highlighted state to drill in'
            : level === 'state' ? 'Click a district'
            : level === 'district' ? 'Click a block'
            : 'Each dot is a village — click for its full picture'}
        </span>
      </div>

      {err && <div className="err">Could not load map: {err}</div>}
      {loading && <div className="loading">loading map…</div>}

      {data && geo && (
        <svg viewBox="0 0 1000 1000" style={{ width: '100%', height, display: 'block' }}
             onMouseLeave={() => setHover(null)}>
          <defs>
            {data.outline && (
              <clipPath id="districtClip">
                <path d={toPath(data.outline)} />
              </clipPath>
            )}
          </defs>

          {/* block Voronoi, clipped to the district's real edge */}
          {level === 'district' && (
            <g clipPath={data.outline ? 'url(#districtClip)' : undefined}>
              {data.features.filter(f => f.cell).map(f => (
                <path key={f.id} d={toPath(f.cell!)} fill={scale(f.units)}
                      stroke="var(--map-line)" strokeWidth={1.4}
                      className="shape" onClick={() => drill(f)}
                      onMouseMove={e => move(e, f)} />
              ))}
            </g>
          )}

          {/* polygons for india / state */}
          {(level === 'india' || level === 'state') && data.features.map(f => {
            const dim = level === 'india' && !f.pilot
            return (
              <path key={f.id} d={toPath(f.rings)}
                    fill={dim ? NO_DATA : scale(f.units)}
                    stroke="var(--map-line)" strokeWidth={dim ? 0.6 : 1}
                    className={dim ? 'shape dim' : 'shape'}
                    onClick={() => !dim && drill(f)}
                    onMouseMove={e => !dim && move(e, f)} />
            )
          })}

          {/* district outline drawn on top so the real boundary stays legible */}
          {level === 'district' && data.outline && (
            <path d={toPath(data.outline)} fill="none"
                  stroke="var(--map-edge)" strokeWidth={2.4} pointerEvents="none" />
          )}

          {/* villages as points */}
          {level === 'village' && data.features.map(f => (
            <circle key={f.id} cx={geo.x(f.lon!)} cy={geo.y(f.lat!)}
                    r={3 + 9 * Math.sqrt(Math.max(f.units ?? 0, 0) / maxU)}
                    fill={ACTION_COLOR[f.action_segment ?? ''] ?? 'var(--c1)'}
                    fillOpacity={0.78} stroke="#fff" strokeWidth={0.7}
                    className="shape" onClick={() => drill(f)}
                    onMouseMove={e => move(e, f)} />
          ))}
        </svg>
      )}

      {hover && (
        <div className="maptip" style={{ left: hover.x + 14, top: hover.y + 12 }}>
          <b>{hover.s.name}</b>
          {hover.s.units != null && <div>{fmt.units(hover.s.units)} units / year</div>}
          {hover.s.headroom != null &&
            <div className="muted">{fmt.units(hover.s.headroom)} unserved</div>}
          {hover.s.action_segment && <div className="muted">{hover.s.action_segment}</div>}
          {hover.s.archetype && <div className="muted">{hover.s.archetype}</div>}
          {hover.s.top_sku && <div className="muted">best: {hover.s.top_sku}</div>}
          {hover.s.shared &&
            <div className="warnline">shares its outline with a neighbouring district</div>}
          {canDrill && <div className="tiphint">click to drill in</div>}
        </div>
      )}

      <div className="maplegend">
        {level === 'village' ? (
          <>
            <span className="muted">what to do</span>
            {Object.entries(ACTION_COLOR).map(([k, c]) => (
              <span key={k}><i style={{ background: c, borderRadius: '50%' }} />{k}</span>
            ))}
            <span className="muted">· dot size = demand</span>
          </>
        ) : (
          <>
            <span className="muted">demand / year</span>
            <span><i style={{ background: NO_DATA }} />no data</span>
            {RAMP.map((c, i) => (
              <span key={c}><i style={{ background: c }} />
                {i === 0 ? 'low' : i === RAMP.length - 1 ? 'high' : ''}</span>
            ))}
          </>
        )}
      </div>
    </div>
  )
}
