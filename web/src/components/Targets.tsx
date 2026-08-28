import React, { useState, useEffect } from 'react'
import {
  BarChart, Bar as RBar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi, Bar, Info, TIP } from './common'
import { useStore } from '../lib/store'

/**
 * PLAN · Targets — Grow archetypes only, because Defend needs no recommendation and
 * No-product-fit cannot be sold into.
 *
 * The funnel is an identity in the data (deliveries = share x demand, enquiries =
 * deliveries / conversion, activities = enquiries / enquiry rate), so a target inverts
 * it exactly: this is arithmetic, not a second model. The levers are then ranked by the
 * units each one closes, benchmarked against Grow archetypes in the same HP belt.
 */
// Every number on this screen is a count of activities, enquiries or units -- fmt.units
// renders a decimal below 1000, which reads as an error on "390.0 deliveries".
const KIND_COLOR: Record<string, string> = {
  volume: 'var(--c1)', efficiency: 'var(--good)', coverage: 'var(--c3)', ceiling: 'var(--text-3)',
}

export default function Targets() {
  const productLine = useStore(s => s.productLine)
  const b = useAsync(() => api.planBuckets({ product: productLine }), [productLine])
  const grow = (b.data?.archetypes ?? []).filter((r: any) => r.bucket === 'Grow')

  const [sel, setSel] = useState<string>()
  const [target, setTarget] = useState<number>()      // committed target
  const [draft, setDraft] = useState('')              // what's in the box

  useEffect(() => {                                   // default to the biggest Grow archetype
    if (!sel && grow.length) setSel(grow[0].archetype_id)
  }, [grow.length])

  const t = useAsync(() => api.planTargets({ archetype_id: sel!, target_units: target }),
                     [sel, target], !!sel)

  useEffect(() => { if (t.data) setDraft(String(t.data.target.units)) }, [t.data?.archetype_id])

  const commit = () => {
    const n = Number(draft.replace(/[^\d.]/g, ''))
    setTarget(Number.isFinite(n) && n > 0 ? n : undefined)
  }

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        What has to change for a Grow archetype to hit a number. The funnel back-solve uses
        that archetype's own conversion rates; benchmarks come from Grow archetypes in the
        same HP belt. Every funnel figure is{' '}
        <span className="pill pill-client">modelled · ITL pending</span>.
      </div>

      <Async state={b}>{() => (
        <div className="row" style={{ gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <label className="dim" style={{ fontSize: 12 }}>Grow archetype</label>
          <select value={sel ?? ''} onChange={e => { setSel(e.target.value); setTarget(undefined) }}
                  style={{ minWidth: 280 }}>
            {grow.map((r: any) => (
              <option key={r.archetype_id} value={r.archetype_id}>
                {r.base_name} · {r.hp_belt} — {fmt.count(r.potential_units_yr)} units/yr
              </option>
            ))}
          </select>
          <label className="dim" style={{ fontSize: 12, marginLeft: 12 }}>Target units /yr</label>
          <input value={draft} onChange={e => setDraft(e.target.value)} onBlur={commit}
                 onKeyDown={e => { if (e.key === 'Enter') commit() }}
                 style={{ width: 110, textAlign: 'right' }} />
          <button onClick={commit}>Set target</button>
          {target != null && <button onClick={() => setTarget(undefined)}>Reset to default</button>}
        </div>
      )}</Async>

      <Async state={t}>{(d: any) => {
        const c = d.current, g = d.target
        const funnel = [
          { step: 'BD activities', today: c.activities, required: g.activities },
          { step: 'Enquiries', today: c.enquiries, required: g.enquiries },
          { step: 'Deliveries', today: c.deliveries, required: g.units },
        ]
        const maxLever = Math.max(...d.levers.map((l: any) => l.units), 1)
        return (
          <>
            <div className="grid g4">
              <Kpi k="Today" v={fmt.count(c.deliveries)}
                   s={`units/yr · ${(c.share * 100).toFixed(1)}% share`} />
              <Kpi k="Target" v={fmt.count(g.units)}
                   s={`${(g.share * 100).toFixed(1)}% share${target == null ? ' · default: a quarter of the way to the leader' : ''}`} />
              <Kpi k="Gap to close" v={fmt.count(g.delta_deliveries)}
                   s={`${fmt.count(g.delta_activities)} more BD activities a year`} />
              <Kpi k="Leader" v={c.leader ?? '—'}
                   s={c.leader_share == null ? 'no competitor data' : `${(c.leader_share * 100).toFixed(0)}% vs our ${(c.share * 100).toFixed(1)}%`} />
            </div>

            <div className="split">
              <Card
                title={<>The funnel, today vs required
                  <Info wide text={<>
                    <b>Grey is today, blue is what the target demands.</b> The funnel is an
                    identity in the data — deliveries = share × demand, enquiries =
                    deliveries ÷ conversion, activities = enquiries ÷ enquiry rate — so this
                    is arithmetic, not a second model. Read the gap at the top bar: that is
                    the field workload the number implies, and if it looks impossible the
                    target needs the conversion or coverage lever instead of more feet.
                  </>} /></>}
                note={`conversion ${(c.conversion_rate * 100).toFixed(0)}% · enquiry rate ${(c.enquiry_rate * 100).toFixed(0)}%`}>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={funnel} layout="vertical"
                            margin={{ top: 4, right: 24, bottom: 4, left: 70 }}>
                    <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 11 }} />
                    <YAxis type="category" dataKey="step" tick={{ fontSize: 11 }} width={70} interval={0} />
                    <Tooltip {...TIP}
                             formatter={(v: any) => Math.round(v).toLocaleString('en-IN')} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <RBar dataKey="today" name="today" fill="var(--text-3)" radius={[0, 2, 2, 0]} />
                    <RBar dataKey="required" name="required" fill="var(--c1)" radius={[0, 2, 2, 0]} />
                  </BarChart>
                </ResponsiveContainer>
                <div className="pb-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr', marginTop: 8 }}>
                  <div className="pb-cell"><span className="pb-k">Extra activities</span><span>{fmt.count(g.delta_activities)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Extra enquiries</span><span>{fmt.count(g.delta_enquiries)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Extra deliveries</span><span>{fmt.count(g.delta_deliveries)}</span></div>
                </div>
              </Card>

              <Card
                title={<>What to pull, biggest first
                  <Info wide text={<>
                    <b>Each lever priced in units a year.</b> Volume assumes today's
                    conversion holds as activity scales — treat it as that lever's ceiling.
                    Conversion and coverage are benchmarked against Grow archetypes in the
                    same HP belt, so they are gaps against peers we already close elsewhere,
                    not aspirations. Product fit is shown as a cap, not an action: below the
                    floor no amount of selling moves the archetype.
                  </>} /></>}
                note="units a year each lever closes, at this archetype's own rates">
                <table>
                  <thead><tr>
                    <th>Lever</th>
                    <th style={{ textAlign: 'right' }}>Units</th>
                    <th style={{ width: '34%' }}></th>
                  </tr></thead>
                  <tbody>
                    {d.levers.map((l: any) => (
                      <tr key={l.lever}>
                        <td>
                          <b>{l.lever}</b>
                          <div className="dim" style={{ fontSize: 11 }}>{l.detail}</div>
                        </td>
                        <td style={{ textAlign: 'right' }}>{l.units ? fmt.count(l.units) : '—'}</td>
                        <td><Bar value={l.units} max={maxLever} color={KIND_COLOR[l.kind]} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="dim" style={{ fontSize: 11, marginTop: 10 }}>
                  Coverage is {(c.sales_coverage * 100).toFixed(0)}% and product fit{' '}
                  {(c.product_fit * 100).toFixed(0)}%. The volume lever assumes today's
                  conversion holds as activity scales — it won't perfectly, so treat it as
                  the ceiling of that lever, not a promise.
                </p>
              </Card>
            </div>
          </>
        )
      }}</Async>
    </div>
  )
}
