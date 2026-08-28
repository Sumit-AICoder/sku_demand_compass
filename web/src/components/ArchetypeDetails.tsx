import React, { useState, useMemo } from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi, PointMap } from './common'

/**
 * REVIEW · Archetype details — the micro-market signals rolled up to archetype level, with
 * the diagnosis the client asked for: PRODUCT issue (poor fit, can't crack anywhere) vs
 * SALES issue (proven product, execution/coverage gap) vs Defend vs Monitor.
 */
const DIAG_COLOR: Record<string, string> = {
  'Defend': 'var(--good)', 'Sales issue': 'var(--c1)',
  'Product issue': 'var(--warn)', 'Monitor': 'var(--text-3)',
}
const DIAG_WHY: Record<string, string> = {
  'Product issue': 'Low product fit — Sonalika cannot crack this archetype anywhere. Needs an adapted/new product, not more selling.',
  'Sales issue': 'Product is proven (good fit) but share is low — an execution, coverage or effort gap that selling can close.',
  'Defend': 'Already winning here — protect the share.',
  'Monitor': 'Too little demand to prioritise now.',
}

export default function ArchetypeDetails() {
  const a = useAsync(() => api.reviewArchetypes(), [])
  const [sel, setSel] = useState<string>()
  const mm = useAsync(() => api.reviewMicromarkets({ archetype_id: sel, metric: 'sonalika_sales_units', limit: 700 }),
                      [sel], !!sel)
  const chosen = a.data?.archetypes.find(r => r.archetype_id === sel)
  const items = useMemo(() => (mm.data?.micromarkets ?? []).map(m => ({
    id: m.micro_market_id, name: m.micro_market_id, lon: m.lon, lat: m.lat,
    units: Number(m.sonalika_sales_units) || 0,
  })), [mm.data])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Each archetype diagnosed as a <b>product issue</b> or a <b>sales issue</b> from its
        product fit, market share and how many of its micro-markets are cracked. Sales-funnel
        figures are <span className="pill pill-client">modelled · ITL pending</span>.
      </div>

      <Async state={a}>{(d: any) => (
        <>
          <div className="grid g3">
            {d.diagnosis.map((g: any) => (
              <Kpi key={g.diagnosis}
                   k={<span><span style={{ color: DIAG_COLOR[g.diagnosis] }}>●</span> {g.diagnosis}</span>}
                   v={`${g.archetypes} archetypes`}
                   s={`${fmt.units(g.demand)} demand · ${fmt.units(g.sales)} sales /yr`} />
            ))}
          </div>

          <div className="split">
            <Card title="Archetypes" note="click one to map it and see the diagnosis">
              <div style={{ maxHeight: 460, overflow: 'auto' }}>
                <table>
                  <thead><tr>
                    <th>Archetype</th><th>Sub-zone</th><th>Diagnosis</th>
                    <th style={{ textAlign: 'right' }}>Share</th>
                    <th style={{ textAlign: 'right' }}>Fit</th>
                    <th style={{ textAlign: 'right' }}>Cracked</th>
                    <th style={{ textAlign: 'right' }}>Sales</th>
                  </tr></thead>
                  <tbody>
                    {d.archetypes.map((r: any) => (
                      <tr key={r.archetype_id}
                          className={sel === r.archetype_id ? 'row-on' : 'row-click'}
                          onClick={() => setSel(r.archetype_id)}>
                        <td>{r.base_name}<div className="dim" style={{ fontSize: 11 }}>{r.hp_belt}</div></td>
                        <td className="dim" style={{ fontSize: 11 }}>{r.subzone_id}</td>
                        <td><span className="pill" style={{ background: DIAG_COLOR[r.diagnosis], color: '#fff', marginLeft: 0 }}>{r.diagnosis}</span></td>
                        <td style={{ textAlign: 'right' }}>{(r.avg_sonalika_share * 100).toFixed(1)}%</td>
                        <td style={{ textAlign: 'right' }}>{(r.product_fit * 100).toFixed(0)}%</td>
                        <td style={{ textAlign: 'right' }}>{(r.cracked_pct * 100).toFixed(0)}%</td>
                        <td style={{ textAlign: 'right' }}>{fmt.units(r.sonalika_sales_units)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card title={chosen ? chosen.base_name + ' · ' + chosen.hp_belt : 'Select an archetype'}
                  note={chosen ? `${chosen.subzone_id} ${chosen.subzone} · ${chosen.states}` : 'click a row'}>
              {!chosen && <p className="dim" style={{ padding: 14 }}>Nothing selected.</p>}
              {chosen && <div>
                <div className="stage-note" style={{ borderColor: DIAG_COLOR[chosen.diagnosis], marginBottom: 12 }}>
                  <b style={{ color: DIAG_COLOR[chosen.diagnosis] }}>{chosen.diagnosis}.</b> {DIAG_WHY[chosen.diagnosis]}
                </div>
                <div className="pb-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                  <div className="pb-cell"><span className="pb-k">Micro-markets</span><span>{fmt.units(chosen.n_micromarkets)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Villages</span><span>{fmt.units(chosen.n_villages)}</span></div>
                  <div className="pb-cell"><span className="pb-k">TIV</span><span>{fmt.units(chosen.tiv)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Share</span><span>{(chosen.avg_sonalika_share * 100).toFixed(1)}%</span></div>
                  <div className="pb-cell"><span className="pb-k">Product fit</span><span>{(chosen.product_fit * 100).toFixed(0)}%</span></div>
                  <div className="pb-cell"><span className="pb-k">Cracked</span><span>{(chosen.cracked_pct * 100).toFixed(0)}% of MMs</span></div>
                  <div className="pb-cell"><span className="pb-k">Activities</span><span>{fmt.units(chosen.activities_yr)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Enquiries</span><span>{fmt.units(chosen.enquiries_yr)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Deliveries</span><span>{fmt.units(chosen.deliveries_yr)}</span></div>
                </div>
                <p className="pb-k" style={{ margin: '12px 0 4px' }}>Micro-markets (colour = Sonalika sales)</p>
                <Async state={mm}>{() => <PointMap items={items} height={280} />}</Async>
              </div>}
            </Card>
          </div>
        </>
      )}</Async>
    </div>
  )
}
