import React, { useState, useMemo } from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi, Info } from './common'
import { GeoMap, MapPoint } from './GeoMap'
import ArchetypeSkus from './ArchetypeSkus'

/**
 * REVIEW · Archetype details — the micro-market signals rolled up to archetype level, with
 * the diagnosis the client asked for: PRODUCT issue (poor fit, can't crack anywhere) vs
 * SALES issue (proven product, execution/coverage gap) vs Defend vs Monitor.
 */
const DIAG_COLOR: Record<string, string> = {
  'Defend': 'var(--good)', 'Sales issue': 'var(--c1)',
  'Product issue': 'var(--warn)', 'Monitor': 'var(--text-3)',
}
// Each of these states the rule that produced the label, not a mood. They are the answer to
// "what does Monitor even mean", and they sit both on the diagnosis cards and on the
// selected archetype, so the question is answered wherever it is asked.
const DIAG_WHY: Record<string, string> = {
  'Product issue': 'Product fit below 48% — Sonalika cannot win this archetype anywhere in it, '
    + 'so more selling will not move it. Needs an adapted or new product.',
  'Sales issue': 'The product fits (48%+) but our share is under 10% — an execution, coverage '
    + 'or effort gap, which is the kind selling can close.',
  'Defend': 'Our share is 10% or more — we are already winning here, so the job is to protect it.',
  'Monitor': 'Not a product or a selling problem: there is simply too little demand here to '
    + 'plan against. The whole archetype earns less than its micro-market count would need to '
    + 'clear the bar — the 20th-percentile micro-market\'s demand, times how many it has. '
    + 'Revisit it when the fleet grows.',
}
// The same 10%-share bar the pipeline uses for cracked_pct (operations.py), so the map's
// green fraction IS the "% of MM won" column beside it.
const WON = 0.10

export default function ArchetypeDetails() {
  const a = useAsync(() => api.reviewArchetypes(), [])
  const [sel, setSel] = useState<string>()
  const mm = useAsync(() => api.reviewMicromarkets({ archetype_id: sel, metric: 'sonalika_sales_units', limit: 700 }),
                      [sel], !!sel)
  const chosen = a.data?.archetypes.find(r => r.archetype_id === sel)
  const points: MapPoint[] = useMemo(() =>
    (mm.data?.micromarkets ?? []).filter((m: any) => m.lon && m.lat).map((m: any) => ({
      id: m.micro_market_id, name: `${m.district} · ${m.micro_market_id}`,
      lon: m.lon, lat: m.lat, value: Number(m.tiv) || 0,
      color: m.sonalika_share >= WON ? 'var(--good)' : 'var(--c1)',
      sub: `${(m.sonalika_share * 100).toFixed(1)}% share · ${fmt.count(m.tiv)} TIV`,
    })), [mm.data])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Each archetype diagnosed from three things: its product fit, our market share, and
        how many of its micro-markets we have won. Hover any diagnosis for the rule behind it.
        Sales-funnel figures are <span className="pill pill-client">modelled · ITL pending</span>.
      </div>

      <Async state={a}>{(d: any) => (
        <>
          <div className="grid g3">
            {d.diagnosis.map((g: any) => (
              <Kpi key={g.diagnosis}
                   k={<span><span style={{ color: DIAG_COLOR[g.diagnosis] }}>●</span> {g.diagnosis}
                        <Info wide text={DIAG_WHY[g.diagnosis]} /></span>}
                   v={`${g.archetypes} archetypes`}
                   s={`${fmt.count(g.demand)} demand · ${fmt.count(g.sales)} sales /yr`} />
            ))}
          </div>

          {/* With no rows the card is simply absent, and the app's own language promises four
              buckets -- so the gap gets explained rather than left to look like a fault. */}
          {!d.diagnosis.some((g: any) => g.diagnosis === 'Defend') && (
            <p className="dim" style={{ fontSize: 12, marginTop: -4 }}>
              No archetype is on <b>Defend</b>: that needs a 10% share and the strongest here
              holds {(Math.max(...d.archetypes.map((r: any) => r.avg_sonalika_share)) * 100).toFixed(1)}%.
              Plan's Defend bucket reads strength <i>relative</i> to the rest of the set, which
              is why it shows Defend archetypes and this table does not.
            </p>
          )}

          <Card title={<>{chosen ? `${chosen.base_name} · ${chosen.hp_belt}` : 'Micro-markets'}
                <Info wide text={<>Every micro-market in the selected archetype. Green is one
                  we have won — 10% share or better, the same bar the <b>% of MM won</b> column
                  counts — so the green fraction of this map is that column. Bubble size is the
                  fleet.</>} /></>}
                note={chosen ? `${fmt.count(chosen.n_micromarkets)} micro-markets · ${(chosen.cracked_pct * 100).toFixed(0)}% won`
                             : 'click an archetype in the table below'}>
            <GeoMap points={points} height={360}
                    legend={<><span><i style={{ background: 'var(--good)' }} />won (share ≥ 10%)</span>
                             <span><i style={{ background: 'var(--c1)' }} />not won</span>
                             <span className="muted">· bubble = tractors in the field</span></>} />
          </Card>

          <div className="split">
            <Card title="Archetypes" note="click one to map it and see the diagnosis">
              <div style={{ maxHeight: 460, overflow: 'auto' }}>
                <table>
                  <thead><tr>
                    <th>Archetype</th><th>Sub-zone</th><th>Diagnosis</th>
                    <th style={{ textAlign: 'right' }}>Share</th>
                    <th style={{ textAlign: 'right' }}>Fit</th>
                    <th style={{ textAlign: 'right' }}>% of MM won</th>
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
                  <div className="pb-cell"><span className="pb-k">% of MM won</span><span>{(chosen.cracked_pct * 100).toFixed(0)}%</span></div>
                  <div className="pb-cell"><span className="pb-k">Activities</span><span>{fmt.units(chosen.activities_yr)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Enquiries</span><span>{fmt.units(chosen.enquiries_yr)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Deliveries</span><span>{fmt.units(chosen.deliveries_yr)}</span></div>
                </div>
              </div>}
            </Card>
          </div>

          <ArchetypeSkus archetypeId={sel} />
        </>
      )}</Async>
    </div>
  )
}
