import React from 'react'
import { BarChart, Bar as RBar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
         PieChart, Pie, CartesianGrid, Legend } from 'recharts'
import { fmt } from '../lib/api'
import { useStore } from '../lib/store'
import { Card, Async, useAsync } from './common'
import Narrative from './Narrative'

const ACTION_COLOR: Record<string, string> = {
  'Convert now': 'var(--good)',
  'Build access': 'var(--c1)',
  'Defend': 'var(--c4)',
  'Monitor': 'var(--text-3)',
}

/**
 * The landing page for someone who has ten seconds, not ten minutes.
 * Answers, in order: how big is this, how much is unclaimed, and what do we do Monday.
 */
export default function Executive() {
  const { setView } = useStore()
  const k = useAsync<any>(() => fetch('/api/kpis').then(r => r.json()), [])
  const sum = useAsync<any>(() => fetch('/api/villages/summary').then(r => r.json()), [])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <Narrative view="executive" />

      <Async state={k}>{(d: any) => (
        <>
          <section>
            <h2 className="sec">The size of the prize</h2>
            <div className="kpis">
              <Big k="Annual demand" v={fmt.units(d.demand.units_per_year)} u="implements a year"
                   s={`across ${d.coverage.villages.toLocaleString('en-IN')} villages`} />
              <Big k="Market value" v={`₹${d.demand.value_crore.toLocaleString('en-IN')}`} u="crore a year"
                   s="at indicative product prices" />
              <Big k="Tractors in play" v={fmt.units(d.coverage.tractors)} u="tractors"
                   s="the fleet an implement can attach to" />
            </div>
          </section>

          <section>
            <h2 className="sec">How much is still unclaimed</h2>
            <div className="kpis">
              <Big k="Already equipped" v={`${d.penetration.pct}%`} u="of the fleet"
                   s="tractors that already have the implement" />
              <Big k="Unserved demand" v={fmt.units(d.penetration.unserved_units)} u="implements"
                   s="the headroom left to sell into" />
              <Big k="Implements per tractor" v={d.penetration.implements_per_tractor} u="today"
                   s="the attach rate to grow" />
              <Big k="Distance to a dealer" v={`${d.coverage_quality.avg_km_to_dealer} km`} u="on average"
                   s="how easily demand can be captured" />
            </div>
          </section>

          <section>
            <h2 className="sec">What to do about it</h2>
            <p className="lead">
              Every village falls into one of four boxes, from two questions: is there
              unserved demand here, and is a dealer close enough to capture it?
            </p>
            <div className="action-grid">
              {(d.actions ?? []).slice().sort((a: any, b: any) => b.units - a.units)
                .map((a: any) => (
                <button key={a.action_segment} className="action-card"
                        style={{ borderTopColor: ACTION_COLOR[a.action_segment] }}
                        onClick={() => setView('villages')}>
                  <div className="action-name" style={{ color: ACTION_COLOR[a.action_segment] }}>
                    {a.action_segment}
                  </div>
                  <div className="action-v">{a.villages.toLocaleString('en-IN')}</div>
                  <div className="action-u">villages</div>
                  <div className="action-s">
                    {fmt.units(a.units)} units a year<br />
                    {fmt.units(a.headroom)} unserved
                  </div>
                </button>
              ))}
            </div>
          </section>

          <div className="split">
            <Card title="Where the demand sits" note="the three pilot states">
              <StateBars />
            </Card>
            <Card title="New business vs protecting what we have"
                  note="replacement is defended; new demand is contested">
              <ResponsiveContainer width="100%" height={230}>
                <PieChart>
                  <Pie data={[
                    { name: 'New demand', value: d.demand.new_units },
                    { name: 'Replacement', value: d.demand.replacement_units },
                  ]} dataKey="value" nameKey="name" innerRadius={58} outerRadius={92}
                     paddingAngle={2}>
                    <Cell fill="var(--c1)" /><Cell fill="var(--c3)" />
                  </Pie>
                  <Tooltip formatter={(v: any) => `${fmt.units(v)} units`} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
              <p className="note">
                {d.demand.replacement_share_pct}% of demand replaces implements already in the
                field. That business is defended by service and parts coverage rather than won
                by new selling.
              </p>
            </Card>
          </div>

          <Card title="Best pockets to work first"
                note="finer than a region — groups of villages that behave alike">
            <Async state={sum}>{(s: any) => (
              <div className="tbl-wrap">
                <table>
                  <thead><tr>
                    <th>Pocket</th><th className="n">Villages</th>
                    <th className="n">Units / yr</th><th className="n">Unserved</th>
                    <th className="n">Km to dealer</th><th>Do what</th><th>Best product</th>
                  </tr></thead>
                  <tbody>
                    {(s.micro ?? []).slice(0, 8).map((r: any) => (
                      <tr key={r.micro_id} className="clickable" onClick={() => setView('villages')}>
                        <td><strong>{r.micro_id}</strong></td>
                        <td className="n">{r.villages.toLocaleString('en-IN')}</td>
                        <td className="n">{fmt.units(r.units)}</td>
                        <td className="n muted">{fmt.units(r.headroom)}</td>
                        <td className="n">{r.avg_km.toFixed(0)}</td>
                        <td><span className="pill" style={{
                          color: ACTION_COLOR[r.main_action],
                          borderColor: ACTION_COLOR[r.main_action] }}>{r.main_action}</span></td>
                        <td className="muted mono">{r.top_sku}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}</Async>
          </Card>
        </>
      )}</Async>
    </div>
  )
}

function StateBars() {
  const s = useAsync<any>(() => fetch('/api/geo/state').then(r => r.json()), [])
  return (
    <Async state={s}>{(d: any) => (
      <ResponsiveContainer width="100%" height={230}>
        <BarChart data={d.items} layout="vertical" margin={{ left: 10, right: 26 }}>
          <CartesianGrid stroke="var(--border)" horizontal={false} />
          <XAxis type="number" tick={{ fontSize: 11 }} />
          <YAxis type="category" dataKey="name" width={112} tick={{ fontSize: 12 }} />
          <Tooltip formatter={(v: any) => `${fmt.units(v)} units a year`} />
          <RBar dataKey="units" radius={[0, 5, 5, 0]}>
            {d.items.map((_: any, i: number) => <Cell key={i} fill={`var(--c${i + 1})`} />)}
          </RBar>
        </BarChart>
      </ResponsiveContainer>
    )}</Async>
  )
}

function Big({ k, v, u, s }: { k: string; v: React.ReactNode; u?: string; s?: string }) {
  return (
    <div className="kpi big">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      {u && <div className="u">{u}</div>}
      {s && <div className="s">{s}</div>}
    </div>
  )
}
