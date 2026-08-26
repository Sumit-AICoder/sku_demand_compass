import React, { useEffect, useState } from 'react'

export function Card({ title, note, children, tight }: {
  title?: React.ReactNode; note?: React.ReactNode
  children: React.ReactNode; tight?: boolean
}) {
  return (
    <div className="card">
      {title && <h3>{title}{note && <small>{note}</small>}</h3>}
      <div className={`body${tight ? ' tight' : ''}`}>{children}</div>
    </div>
  )
}

export function Kpi({ k, v, s }: { k: string; v: React.ReactNode; s?: React.ReactNode }) {
  return <div className="kpi"><div className="k">{k}</div><div className="v">{v}</div>{s && <div className="s">{s}</div>}</div>
}

export function Badge({ kind, children }: { kind: string; children?: React.ReactNode }) {
  return <span className={`badge ${kind}`}>{children ?? kind}</span>
}

/** Small async data hook: keeps every panel's loading/error handling identical. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[], enabled = true) {
  const [data, setData] = useState<T | undefined>()
  const [err, setErr] = useState<string | undefined>()
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!enabled) { setData(undefined); return }
    let live = true
    setLoading(true); setErr(undefined)
    fn().then(d => { if (live) { setData(d); setLoading(false) } })
        .catch(e => { if (live) { setErr(String(e)); setLoading(false) } })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { data, err, loading }
}

export function Async<T>({ state, children, empty }: {
  state: { data?: T; err?: string; loading: boolean }
  children: (d: T) => React.ReactNode
  empty?: React.ReactNode
}) {
  if (state.err) return <div className="err">{state.err}</div>
  if (state.loading) return <div className="loading">loading…</div>
  if (state.data === undefined) return <>{empty ?? <div className="loading">—</div>}</>
  if (Array.isArray(state.data) && state.data.length === 0)
    return <>{empty ?? <div className="loading">no rows</div>}</>
  return <>{children(state.data)}</>
}

export function Bar({ value, max, color }: { value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0
  return <div className="bar"><i style={{ width: `${pct}%`, background: color }} /></div>
}

/**
 * Point map rendered as inline SVG from real village/district coordinates.
 * Deliberately not a tile map: the geometry we have is real coordinates, and a
 * projection + colour ramp conveys the same spatial story without a heavy
 * dependency or an external tile host.
 */
export function PointMap({ items, onSelect, selected, height = 380 }: {
  items: Array<{ id: string; name: string; lon: number; lat: number; units: number }>
  onSelect?: (id: string) => void
  selected?: string
  height?: number
}) {
  if (!items.length) return <div className="loading">no geography</div>
  const lons = items.map(i => i.lon), lats = items.map(i => i.lat)
  const pad = 0.06
  const lo0 = Math.min(...lons), lo1 = Math.max(...lons)
  const la0 = Math.min(...lats), la1 = Math.max(...lats)
  const dx = (lo1 - lo0) || 1, dy = (la1 - la0) || 1
  const W = 100, H = height / 4.2

  const x = (lon: number) => ((lon - lo0 + dx * pad) / (dx * (1 + 2 * pad))) * W
  const y = (lat: number) => H - ((lat - la0 + dy * pad) / (dy * (1 + 2 * pad))) * H

  const vals = items.map(i => i.units).filter(v => v > 0).sort((a, b) => a - b)
  const q = (p: number) => vals.length ? vals[Math.floor(p * (vals.length - 1))] : 0
  const ramp = ['#dbe6f2', '#a9c6e8', '#6fa3d9', '#3d7ec4', '#1f5da8', '#0d3f7d']
  const stops = [q(.2), q(.4), q(.6), q(.8), q(.95)]
  const col = (v: number) => {
    let i = 0
    while (i < stops.length && v > stops[i]) i++
    return ramp[i]
  }
  const maxU = Math.max(...items.map(i => i.units), 1)
  const r = (v: number) => 0.45 + 1.5 * Math.sqrt(Math.max(v, 0) / maxU)

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height, display: 'block' }}>
        {items.map(i => (
          <circle
            key={i.id} cx={x(i.lon)} cy={y(i.lat)} r={r(i.units)}
            fill={col(i.units)} stroke={selected === i.id ? '#c0392b' : 'rgba(0,0,0,.18)'}
            strokeWidth={selected === i.id ? 0.6 : 0.08}
            style={{ cursor: onSelect ? 'pointer' : 'default' }}
            onClick={() => onSelect?.(i.id)}
          ><title>{`${i.name}\n${Math.round(i.units).toLocaleString('en-IN')} units/yr`}</title></circle>
        ))}
      </svg>
      <div className="legend" style={{ marginTop: 8 }}>
        <span className="muted">demand potential</span>
        {ramp.map((c, i) => <span key={c}><i style={{ background: c }} />{i === 0 ? 'low' : i === ramp.length - 1 ? 'high' : ''}</span>)}
      </div>
    </div>
  )
}
