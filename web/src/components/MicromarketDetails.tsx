import React, { useState, useMemo } from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, PointMap, Bar } from './common'

/**
 * REVIEW · Micro-market details — every operational signal at micro-market level: sales,
 * TIV, market share, and the activities → enquiries → deliveries funnel, with the selected
 * micro-market on the map. Sales/activities/enquiries/deliveries are modelled (ITL pending).
 */
const METRICS: Array<[string, string]> = [
  ['sonalika_sales_units', 'Sonalika sales'],
  ['sonalika_share', 'Market share'],
  ['tiv', 'TIV (tractors)'],
  ['potential_units_yr', 'Demand potential'],
  ['activities_yr', 'Activities'],
]
const DIAG_COLOR: Record<string, string> = {
  'Defend': 'var(--good)', 'Sales issue': 'var(--c1)',
  'Product issue': 'var(--warn)', 'Monitor': 'var(--text-3)',
}

export default function MicromarketDetails() {
  const districts = useAsync(() => api.defineDistricts(), [])
  const [state, setState] = useState('Punjab')
  const [districtId, setDistrictId] = useState<string>()
  const [metric, setMetric] = useState('sonalika_sales_units')
  const [sel, setSel] = useState<string>()

  const dlist = (districts.data?.districts ?? []).filter(d => d.state === state)
  const did = districtId ?? dlist[0]?.district_id
  const mm = useAsync(() => api.reviewMicromarkets({ district: did, metric, limit: 700 }),
                      [did, metric], !!did)
  const detail = useAsync(() => api.reviewMicromarket(sel!), [sel], !!sel)

  const items = useMemo(() => (mm.data?.micromarkets ?? []).map(m => ({
    id: m.micro_market_id, name: `${m.micro_market_id} · ${m.diagnosis}`,
    lon: m.lon, lat: m.lat, units: Number(m[metric]) || 0,
  })), [mm.data, metric])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Every operational signal per micro-market. Sales, activities, enquiries and deliveries
        are <span className="pill pill-client">modelled · ITL pending</span>; TIV/share modelled,
        agro-climate real.
      </div>

      <div className="filters" style={{ marginLeft: 0 }}>
        <select value={state} onChange={e => { setState(e.target.value); setDistrictId(undefined); setSel(undefined) }}>
          {['Punjab', 'Madhya Pradesh', 'Maharashtra'].map(s => <option key={s}>{s}</option>)}
        </select>
        <select value={did ?? ''} onChange={e => { setDistrictId(e.target.value); setSel(undefined) }}>
          {dlist.map(d => <option key={d.district_id} value={d.district_id}>{d.district}</option>)}
        </select>
        <select value={metric} onChange={e => setMetric(e.target.value)}>
          {METRICS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <span className="dim" style={{ fontSize: 12, alignSelf: 'center' }}>
          {items.length} micro-markets · colour = {METRICS.find(m => m[0] === metric)?.[1]}
        </span>
      </div>

      <div className="split">
        <Card title="Micro-markets" note="click a point for full detail">
          <Async state={mm}>{() => <PointMap items={items} selected={sel} onSelect={setSel} height={440} />}</Async>
        </Card>

        <Card title={sel ? `Micro-market ${sel}` : 'Select a micro-market'}
              note={sel ? 'operational detail' : 'click a point on the map'}>
          {!sel && <p className="dim" style={{ padding: 14 }}>Nothing selected.</p>}
          {sel && <Async state={detail}>{(d: any) => {
            const m = d.micromarket
            if (!m) return <p className="dim">not found</p>
            const funnel = [
              ['Activities', m.activities_yr], ['Enquiries', m.enquiries_yr], ['Deliveries', m.deliveries_yr],
            ] as Array<[string, number]>
            const fmax = m.activities_yr || 1
            return (
              <div>
                <div style={{ marginBottom: 10 }}>
                  <span className="pill" style={{ background: DIAG_COLOR[m.diagnosis], color: '#fff', marginLeft: 0 }}>
                    {m.diagnosis}</span>
                  <span className="dim" style={{ fontSize: 12, marginLeft: 8 }}>
                    {m.archetype} · {m.subzone_id} {m.subzone}</span>
                </div>
                <div className="pb-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                  <div className="pb-cell"><span className="pb-k">Sonalika sales</span><span>{fmt.units(m.sonalika_sales_units)} /yr</span></div>
                  <div className="pb-cell"><span className="pb-k">Market share</span><span>{(m.sonalika_share * 100).toFixed(1)}%</span></div>
                  <div className="pb-cell"><span className="pb-k">TIV</span><span>{fmt.units(m.tiv)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Demand /yr</span><span>{fmt.units(m.potential_units_yr)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Conversion</span><span>{(m.conversion_rate * 100).toFixed(0)}%</span></div>
                  <div className="pb-cell"><span className="pb-k">Product fit</span><span>{(m.product_fit * 100).toFixed(0)}%</span></div>
                </div>
                <p className="pb-k" style={{ marginTop: 12 }}>Sales funnel (per year)</p>
                <table>
                  <tbody>
                    {funnel.map(([label, v]) => (
                      <tr key={label}>
                        <td style={{ width: 90 }}>{label}</td>
                        <td style={{ width: 130 }}><Bar value={v} max={fmax} color="var(--c1)" /></td>
                        <td style={{ textAlign: 'right' }}>{fmt.units(v)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="dim" style={{ fontSize: 12, marginTop: 8 }}>
                  {m.n_villages} villages · {m.hp_belt} · mean {m.mean_hp?.toFixed(0)} HP · dealer access {(m.dealer_accessibility * 100).toFixed(0)}%
                </p>
              </div>
            )
          }}</Async>}
        </Card>
      </div>
    </div>
  )
}
