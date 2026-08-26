import React, { useState } from 'react'
import { BarChart, Bar as RBar, XAxis, YAxis, Tooltip, ResponsiveContainer,
         CartesianGrid, ReferenceLine, Cell } from 'recharts'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Badge, Kpi } from './common'

export default function ClusterView() {
  const clusters = useAsync(() => api.clusters(), [])
  const [sel, setSel] = useState<number>()
  const chosen = sel ?? clusters.data?.[0]?.cluster
  const basket = useAsync(() => api.clusterSkus(chosen!), [chosen], chosen !== undefined)
  const white = useAsync(() => api.whitespace({ cluster_id: chosen, limit: 60 }),
                         [chosen], chosen !== undefined)
  const p0 = clusters.data?.[0]

  return (
    <div className="grid" style={{ gap: 14 }}>
      {p0 && (
        <div className="kpis">
          <Kpi k="Archetypes" v={clusters.data!.length} s="k chosen for business legibility" />
          <Kpi k="Bootstrap stability" v={fmt.num(p0.bootstrap_ari, 3)}
               s={<span className={p0.bootstrap_ari >= 0.7 ? 'pos' : 'neg'}>adjusted Rand index — 0.70 required</span>} />
          <Kpi k="Spatial coherence" v={fmt.num(p0.spatial_coherence, 2)}
               s="share of neighbours in the same archetype" />
          <Kpi k="Method" v={p0.method} s="mixed numeric + categorical" />
        </div>
      )}

      <Card title="Village archetypes" tight
            note="clustering carries district-level UCM intelligence down to the village">
        <div className="tbl-wrap" style={{ maxHeight: 340 }}>
          <Async state={clusters}>{(rows: any[]) => (
            <table>
              <thead><tr>
                <th>Archetype</th><th className="n">Villages</th><th className="n">Share</th>
                <th>States</th><th>Crops</th><th className="n">Holding</th>
                <th className="n">Attach</th><th className="n">kW/ha</th>
                <th className="n">Irrigation</th><th>Defining features (z-scores)</th>
              </tr></thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.cluster} className={`clickable${chosen === r.cluster ? ' sel' : ''}`}
                      onClick={() => setSel(r.cluster)}>
                    <td><strong>{r.archetype}</strong></td>
                    <td className="n">{r.n_villages.toLocaleString('en-IN')}</td>
                    <td className="n muted">{r.share_pct}%</td>
                    <td className="muted">{r.states}</td>
                    <td className="muted">{r.top_crops}</td>
                    <td className="n">{r.avg_holding_ha}</td>
                    <td className="n">{r.attach_rate}</td>
                    <td className="n">{r.farm_power_kw_ha}</td>
                    <td className="n">{r.irrigation_reliability}</td>
                    <td className="note" style={{ whiteSpace: 'normal', maxWidth: 340, fontSize: 10.5 }}>
                      {r.defining_features}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}</Async>
        </div>
      </Card>

      <div className="split">
        <Card title="SKU basket this archetype over-indexes on"
              note="index vs the national mix — 1.0 means average">
          <Async state={basket}>{(rows: any[]) => (
            <ResponsiveContainer width="100%" height={340}>
              <BarChart data={rows} layout="vertical" margin={{ left: 10, right: 20 }}>
                <CartesianGrid stroke="var(--border)" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 10 }} />
                <YAxis type="category" dataKey="name" width={175} tick={{ fontSize: 9.5 }} />
                <Tooltip formatter={(v: any) => `${Number(v).toFixed(2)}× national`} />
                <ReferenceLine x={1} stroke="var(--border-strong)" strokeDasharray="3 3" />
                <RBar dataKey="index_vs_national" radius={[0, 3, 3, 0]}>
                  {rows.map((r: any, i: number) => (
                    <Cell key={i} fill={r.index_vs_national >= 1 ? 'var(--c2)' : 'var(--c3)'} />
                  ))}
                </RBar>
              </BarChart>
            </ResponsiveContainer>
          )}</Async>
        </Card>

        <Card title="Whitespace — under-penetrated villages" tight
              note="low attach rate inside a high-potential archetype">
          <div className="tbl-wrap" style={{ maxHeight: 360 }}>
            <Async state={white}>{(rows: any[]) => (
              <table>
                <thead><tr>
                  <th>Village</th><th>District</th><th className="n">Attach</th>
                  <th className="n">Peers</th><th className="n">Gap</th><th className="n">Units/yr</th>
                </tr></thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.village_id}>
                      <td className="mono" style={{ fontSize: 11 }}>{r.village}</td>
                      <td className="muted">{r.district}</td>
                      <td className="n">{fmt.num(r.attach_rate)}</td>
                      <td className="n muted">{fmt.num(r.peer_attach_rate)}</td>
                      <td className="n neg">−{fmt.num(r.gap_to_peers)}</td>
                      <td className="n">{fmt.units(r.units)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}</Async>
          </div>
        </Card>
      </div>

      <Card title="Why archetypes matter here">
        <p className="note" style={{ maxWidth: 900 }}>
          The UCM can only be fitted where time series exist, which is district level. A
          village therefore inherits its <strong>archetype's</strong> factor response, not
          merely its district's — two villages in the same district but different archetypes
          get different weights. The whitespace list is the operational output: a village
          whose attach rate sits well below its own archetype's peers is a targeting
          priority, not a weak market, because the archetype proves the demand is
          convertible there.
        </p>
      </Card>
    </div>
  )
}
