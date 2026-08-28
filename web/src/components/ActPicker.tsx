import React, { useEffect, useMemo } from 'react'
import { api } from '../lib/api'
import { useAsync } from './common'
import { useStore } from '../lib/store'

/**
 * The archetype selector both Act screens share, so the summary and the playbook can never
 * describe different archetypes. Buckets come from the same `_plan_buckets` call the Plan
 * stage uses — one definition of Defend / Grow / No product fit across the whole tool.
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
