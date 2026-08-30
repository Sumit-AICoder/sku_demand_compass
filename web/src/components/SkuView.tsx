import React, { useState } from 'react'
import { BarChart, Bar as RBar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
         CartesianGrid, Legend } from 'recharts'
import { api, fmt } from '../lib/api'
import { useStore } from '../lib/store'
import { Card, Async, useAsync, Badge, Bar } from './common'
import Narrative from './Narrative'
import SkuImage from './SkuImage'
import { ArchetypePicker } from './ActPicker'

const BUCKETS = ['Grow', 'Defend', 'No product fit'] as const

export default function SkuView() {
  const { category, setSku, productLine } = useStore()
  const [scope, setScope] = useState<'state' | 'district' | 'bucket' | 'archetype'>('state')
  const geo = useAsync(() => api.geo(scope === 'state' ? 'state' : 'district', {}),
                       [scope], scope === 'state' || scope === 'district')
  const [nodeId, setNodeId] = useState<string>()
  const [bucket, setBucket] = useState<typeof BUCKETS[number]>('Grow')
  const buckets = useAsync(() => api.planBuckets({ product: productLine }),
                           [productLine, scope], scope === 'archetype')
  const archRows = buckets.data?.archetypes ?? []
  const [archId, setArchId] = useState<string>()
  const rows = useAsync(
    () => scope === 'bucket' ? api.bucketSkus(bucket, 40)
        : scope === 'archetype' ? api.archetypeSkus(archId!, 40)
        : api.scores({ level: scope, id: nodeId, category, limit: 40 }),
    [scope, nodeId, category, bucket, archId], scope !== 'archetype' || !!archId)

  return (
    <div className="grid" style={{ gap: 14 }}>
      <Narrative view="sku" params={{ category }} />
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={scope} onChange={e => { setScope(e.target.value as any); setNodeId(undefined) }}>
          <option value="state">By state</option>
          <option value="district">By district</option>
          <option value="bucket">By bucket — Defend/Grow/No fit</option>
          <option value="archetype">By archetype</option>
        </select>
        {scope === 'bucket' ? (
          <select value={bucket} onChange={e => setBucket(e.target.value as any)}>
            {BUCKETS.map(b => <option key={b} value={b}>{b}</option>)}
          </select>
        ) : scope === 'archetype' ? (
          <Async state={buckets}>{() => (
            <ArchetypePicker rows={archRows} sel={archId} setSel={setArchId} />
          )}</Async>
        ) : (
          <select value={nodeId ?? ''} onChange={e => setNodeId(e.target.value || undefined)}>
            <option value="">All {scope}s</option>
            {(geo.data?.items ?? []).map(i =>
              <option key={i.id} value={i.id}>{i.name}{i.parent ? ` — ${i.parent}` : ''}</option>)}
          </select>
        )}
        <span className="note">
          {scope === 'bucket'
            ? 'Which products carry the Grow list, what Defend already holds, and what doesn’t fit at all.'
            : scope === 'archetype'
            ? 'Every SKU in the selected archetype’s own basket, ranked by weighted demand.'
            : 'New demand is unserved headroom converting this year; replacement is the installed base retiring on its life cycle.'}
        </span>
      </div>

      <Card title="SKU demand potential" tight
            note="click a SKU to filter every other view">
        <div className="tbl-wrap" style={{ maxHeight: 560 }}>
          <Async state={rows} empty={scope === 'archetype' && !archId ? 'Select an archetype above.' : undefined}>
            {(r: any[]) => {
            const max = Math.max(...r.map(x => x.units), 1)
            return <table>
              <thead><tr>
                <th>SKU</th><th>Category</th><th className="n">HP band</th>
                <th className="n">Units/yr</th><th className="n">New</th>
                <th className="n">Replace</th><th className="n">Headroom</th>
                <th className="n">Penetration</th><th className="n">Value</th>
                <th>Maturity</th><th style={{ width: 90 }} />
              </tr></thead>
              <tbody>
                {r.map(x => {
                  const pen = x.addressable ? (1 - x.headroom / x.addressable) * 100 : 0
                  return (
                    <tr key={x.sku_id} className="clickable" onClick={() => setSku(x.sku_id)}>
                      <td>
                        <div className="sku-cell">
                          <SkuImage skuId={x.sku_id} category={x.category} size={44} />
                          <span>{x.name}</span>
                        </div>
                      </td>
                      <td className="muted">{x.category_label}</td>
                      <td className="n muted">{x.hp_min}–{x.hp_max > 900 ? '∞' : x.hp_max}</td>
                      <td className="n">{fmt.units(x.units)}</td>
                      <td className="n muted">{fmt.units(x.new_units)}</td>
                      <td className="n muted">{fmt.units(x.replacement_units)}</td>
                      <td className="n muted">{fmt.units(x.headroom)}</td>
                      <td className="n">{fmt.pct(pen, 0)}</td>
                      <td className="n">{fmt.cr(x.value)}</td>
                      <td><span className="chip">{x.maturity}</span></td>
                      <td><Bar value={x.units} max={max} /></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          }}</Async>
        </div>
      </Card>

      <div className="split">
        <Card title="New vs replacement demand"
              note="replacement-heavy SKUs are defended, new-heavy SKUs are contested">
          <Async state={rows}>{(r: any[]) => (
            <ResponsiveContainer width="100%" height={380}>
              <BarChart data={r.slice(0, 16)} layout="vertical" margin={{ left: 12, right: 16 }}>
                <CartesianGrid stroke="var(--border)" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 10 }} />
                <YAxis type="category" dataKey="name" width={172} tick={{ fontSize: 9.5 }} />
                <Tooltip formatter={(v: any) => fmt.units(v)} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <RBar dataKey="new_units" name="New" stackId="a" fill="var(--c1)" />
                <RBar dataKey="replacement_units" name="Replacement" stackId="a" fill="var(--c3)" />
              </BarChart>
            </ResponsiveContainer>
          )}</Async>
        </Card>

        <Card title="Weight composition by SKU"
              note="how much of each SKU's score is driven by UCM-estimated weights">
          <WeightMix />
        </Card>
      </div>
    </div>
  )
}

function WeightMix() {
  const skus = useAsync(() => api.skus(), [])
  const [sel, setSel] = useState<string>()
  const chosen = sel ?? skus.data?.[0]?.sku_id
  const geo = useAsync(() => api.geo('village', { parent: 'x' }), [])   // unused, keeps shape
  const drv = useAsync(async () => {
    const d = await api.geo('district', {})
    const b = await api.geo('block', { parent: d.items[0].id })
    const v = await api.geo('village', { parent: b.items[0].id })
    return api.drivers(v.items[0].id, chosen!)
  }, [chosen], !!chosen)

  return (
    <>
      <select value={chosen ?? ''} onChange={e => setSel(e.target.value)}
              style={{ marginBottom: 10, maxWidth: '100%' }}>
        {(skus.data ?? []).map(s => <option key={s.sku_id} value={s.sku_id}>{s.name}</option>)}
      </select>
      <Async state={drv}>{(d: any) => (
        <>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={d.contributions} layout="vertical" margin={{ left: 10, right: 16 }}>
              <XAxis type="number" tick={{ fontSize: 10 }} />
              <YAxis type="category" dataKey="label" width={168} tick={{ fontSize: 9.5 }} />
              <Tooltip formatter={(v: any) => Number(v).toFixed(3)} />
              <RBar dataKey="weight" radius={[0, 3, 3, 0]}>
                {d.contributions.map((c: any, i: number) => (
                  <Cell key={i} fill={c.origin === 'ucm' ? 'var(--ucm)' : 'var(--prior)'} />
                ))}
              </RBar>
            </BarChart>
          </ResponsiveContainer>
          <div className="legend">
            <span><i style={{ background: 'var(--ucm)' }} />UCM-estimated</span>
            <span><i style={{ background: 'var(--prior)' }} />judgmental prior</span>
          </div>
        </>
      )}</Async>
    </>
  )
}
