import React, { useEffect, useMemo } from 'react'
import { api, fmt } from '../lib/api'
import { useAsync } from './common'
import { useStore } from '../lib/store'

/**
 * The archetype selector the Act stage shares with Plan's forecast, plus the district and
 * micro-market narrowing the playbook is generated at. Buckets come from the same
 * `_plan_buckets` call the Plan stage uses -- one definition of Defend / Grow / No product
 * fit across the whole tool.
 */
export const BUCKET_COLOR: Record<string, string> = {
  'Defend': 'var(--good)', 'Grow': 'var(--c1)', 'No product fit': 'var(--warn)',
}

export function useArchetypes() {
  const productLine = useStore(s => s.productLine)
  const sel = useStore(s => s.actArchetype)
  const setSel = useStore(s => s.setActArchetype)
  const b = useAsync(() => api.planBuckets({ product: productLine }), [productLine])
  const rows = b.data?.archetypes ?? []

  useEffect(() => {                       // open on the biggest Grow archetype
    if (!sel && rows.length) {
      const grow = rows.filter((r: any) => r.bucket === 'Grow')
      setSel((grow[0] ?? rows[0]).archetype_id)
    }
  }, [rows.length])

  const chosen = rows.find((r: any) => r.archetype_id === sel)
  return { b, rows, sel, setSel, chosen }
}

/**
 * The scope the playbook is written for. Districts and micro-markets are read from the
 * selected archetype's own membership, so a narrower selection can never point outside the
 * archetype it belongs to.
 */
export function useActScope(archetypeId?: string) {
  const district = useStore(s => s.actDistrict)
  const micro = useStore(s => s.actMicroMarket)
  const setDistrict = useStore(s => s.setActDistrict)
  const setMicro = useStore(s => s.setActMicroMarket)
  const mm = useAsync(() => api.planBucketMicromarkets(archetypeId!, 4000),
                      [archetypeId], !!archetypeId)
  const rows = mm.data?.micromarkets ?? []

  const districts = useMemo(() => {
    const by = new Map<string, { id: string; name: string; state: string; units: number; n: number }>()
    for (const r of rows) {
      const d = by.get(r.district_id) ??
        { id: r.district_id, name: r.district, state: r.state, units: 0, n: 0 }
      d.units += r.potential_units_yr ?? 0
      d.n += 1
      by.set(r.district_id, d)
    }
    return [...by.values()].sort((x, y) => y.units - x.units)
  }, [rows])

  const micros = useMemo(
    () => rows.filter((r: any) => !district || r.district_id === district)
              .sort((x: any, y: any) => y.potential_units_yr - x.potential_units_yr),
    [rows, district])

  return { mm, districts, micros, district, micro, setDistrict, setMicro }
}

export function ArchetypePicker({ rows, sel, setSel }: {
  rows: any[]; sel?: string; setSel: (a?: string) => void
}) {
  const [bucket, setBucket] = React.useState<string>()
  const visible = useMemo(
    () => rows.filter((r: any) => !bucket || r.bucket === bucket)
              .sort((x: any, y: any) => y.potential_units_yr - x.potential_units_yr),
    [rows, bucket])

  return (
    <div className="row row-centre" style={{ gap: 12 }}>
      <span className="dim" style={{ fontSize: 12 }}>Archetype</span>
      <div className="switch">
        {[undefined, 'Defend', 'Grow', 'No product fit'].map(x => (
          <button key={x ?? 'all'} className={bucket === x ? 'on' : ''}
                  onClick={() => setBucket(x)}>{x ?? 'All buckets'}</button>
        ))}
      </div>
      <select value={sel ?? ''} onChange={e => setSel(e.target.value || undefined)}
              style={{ minWidth: 340 }}>
        {visible.map((r: any) => (
          <option key={r.archetype_id} value={r.archetype_id}>
            {r.base_name} · {r.hp_belt} · {r.subzone_id} — {Math.round(r.potential_units_yr).toLocaleString('en-IN')} units/yr
          </option>
        ))}
      </select>
    </div>
  )
}

/** District and micro-market narrowing, sitting under the archetype row. */
export function ScopePicker({ scope }: { scope: ReturnType<typeof useActScope> }) {
  const { districts, micros, district, micro, setDistrict, setMicro } = scope
  return (
    <div className="row row-centre" style={{ gap: 12, flexWrap: 'wrap' }}>
      <span className="dim" style={{ fontSize: 12 }}>Narrow to</span>
      <select value={district ?? ''} onChange={e => setDistrict(e.target.value || undefined)}
              style={{ minWidth: 260 }}>
        <option value="">All {districts.length} districts</option>
        {districts.map(d => (
          <option key={d.id} value={d.id}>
            {d.name}, {d.state} — {d.n} micro-markets · {fmt.count(d.units)} units/yr
          </option>
        ))}
      </select>
      <select value={micro ?? ''} onChange={e => setMicro(e.target.value || undefined)}
              style={{ minWidth: 260 }} disabled={!micros.length}>
        <option value="">All {micros.length} micro-markets</option>
        {micros.slice(0, 400).map((m: any) => (
          <option key={m.micro_market_id} value={m.micro_market_id}>
            {m.micro_market_id} · {m.district} — {fmt.count(m.potential_units_yr)} units/yr
          </option>
        ))}
      </select>
      {(district || micro) && (
        <button className="linkish" onClick={() => setDistrict(undefined)}>
          reset to the whole archetype
        </button>
      )}
    </div>
  )
}
