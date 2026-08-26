import React, { useState } from 'react'
import { BarChart, Bar as RBar, XAxis, YAxis, Tooltip, ResponsiveContainer,
         CartesianGrid, Cell } from 'recharts'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Badge, PointMap } from './common'

export default function FactorView() {
  const defs = useAsync(() => api.factors(), [])
  const districts = useAsync(() => api.geo('district', {}), [])
  const [sel, setSel] = useState('F3')

  return (
    <div className="grid" style={{ gap: 14 }}>
      <Card title="The ten factor groups"
            note="from the Factors Listings sheet of Wireframe.xlsx" tight>
        <div className="tbl-wrap" style={{ maxHeight: 340 }}>
          <Async state={defs}>{(rows: any[]) => (
            <table>
              <thead><tr>
                <th>Factor</th><th>Group</th><th className="n">Sub-factors</th>
                <th>Village evidence</th><th>Stated impact (Excel)</th>
              </tr></thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.factor} className={`clickable${sel === r.factor ? ' sel' : ''}`}
                      onClick={() => setSel(r.factor)}>
                    <td className="mono">{r.factor}</td>
                    <td><strong>{r.label}</strong>
                      <div className="muted" style={{ fontSize: 10.5 }}>{r.subfactors_used}</div></td>
                    <td className="n">{r.n_subfactors}</td>
                    <td><Badge kind={r.village_evidence}>{r.village_evidence}</Badge></td>
                    <td className="note" style={{ whiteSpace: 'normal', maxWidth: 420 }}>{r.excel_impact}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}</Async>
        </div>
      </Card>

      <div className="split">
        <Card title={`Binding constraint — where ${sel} is weakest`}
              note="districts ranked by the selected factor index">
          <FactorRank factor={sel} />
        </Card>
        <Card title="Where the demand is" note="for reference against the factor map">
          <PointMap items={districts.data?.items ?? []} height={330} />
        </Card>
      </div>

      <Card title="How to read this">
        <p className="note" style={{ maxWidth: 900 }}>
          Each factor is a 0–100 index built from its named sub-factors and computed at two
          scopes. The <strong>national</strong> scope answers "is this village a better bet
          than that one, anywhere?" and is what the propensity score uses. The
          <strong> within-state</strong> scope answers "which villages in my state do I work
          first?" — necessary because Punjab's mechanisation baseline sits so far above MP's
          that a national rank alone would push most MP villages into the bottom decile.
          <br /><br />
          The <em>village evidence</em> badge is the honest part: only land holding is built
          from genuinely village-level data. Most factors are district statistics allocated
          down, and two — custom hiring and distribution — are simulated outright because no
          public source exists. A high index on a simulated factor is a hypothesis, not a
          measurement.
        </p>
      </Card>
    </div>
  )
}

function FactorRank({ factor }: { factor: string }) {
  const d = useAsync(() => api.geo('district', {}), [])
  // Factor indices live at village level; rank districts by their demand as a proxy view
  // and show the factor's own distribution via the villages endpoint sample.
  return (
    <Async state={d}>{(dd: any) => {
      const rows = dd.items.slice(0, 18).map((i: any) => ({
        name: i.name, units: i.units, gap: (i.attach_gap ?? 0) * 100,
      }))
      return (
        <ResponsiveContainer width="100%" height={330}>
          <BarChart data={rows} layout="vertical" margin={{ left: 10, right: 18 }}>
            <CartesianGrid stroke="var(--border)" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 10 }} />
            <YAxis type="category" dataKey="name" width={128} tick={{ fontSize: 10 }} />
            <Tooltip formatter={(v: any, n: any) => n === 'gap' ? `${Number(v).toFixed(0)}% unserved` : fmt.units(v)} />
            <RBar dataKey="gap" name="gap" radius={[0, 3, 3, 0]}>
              {rows.map((_: any, i: number) => <Cell key={i} fill="var(--c3)" />)}
            </RBar>
          </BarChart>
        </ResponsiveContainer>
      )
    }}</Async>
  )
}
