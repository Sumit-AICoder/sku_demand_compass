import React, { useState, useMemo } from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, PointMap } from './common'

/**
 * DEFINE · Map Explorer — micro-markets (groups of ~4-5 villages) on the map.
 *
 * Pick a district; each micro-market is a point coloured by the chosen metric (TIV,
 * Sonalika share or demand). Click one to see its villages and profile. TIV / HP mix /
 * share are modelled (ITL pending); agro-climate is real.
 */
const METRICS: Array<[string, string]> = [
  ['tiv', 'TIV (tractors)'],
  ['sonalika_share', 'Sonalika share'],
  ['potential_units_yr', 'Demand potential'],
]

export default function MapExplorer() {
  const districts = useAsync(() => api.defineDistricts(), [])
  const [state, setState] = useState('Punjab')
  const [districtId, setDistrictId] = useState<string>()
  const [metric, setMetric] = useState('tiv')
  const [sel, setSel] = useState<string>()

  const dlist = (districts.data?.districts ?? []).filter(d => d.state === state)
  const did = districtId ?? dlist[0]?.district_id
  const mm = useAsync(() => api.micromarkets({ district: did, metric, limit: 600 }),
                      [did, metric], !!did)
  const detail = useAsync(() => api.micromarketDetail(sel!), [sel], !!sel)

  const items = useMemo(() => (mm.data?.micromarkets ?? []).map(m => ({
    id: m.micro_market_id, name: `${m.micro_market_id} · ${m.hp_belt}`,
    lon: m.lon, lat: m.lat, units: Number(m[metric]) || 0,
  })), [mm.data, metric])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Each point is a <b>micro-market</b> (~4-5 villages grouped by proximity + agro-climate).
        TIV, HP mix and Sonalika share are <span className="pill pill-client">modelled · ITL pending</span>;
        agro-climate is <span className="pill pill-secondary">real</span>.
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
        <Card title="Micro-markets" note="click a point to inspect it">
          <Async state={mm}>{() => (
            <PointMap items={items} selected={sel} onSelect={setSel} height={440} />
          )}</Async>
        </Card>

        <Card title={sel ? `Micro-market ${sel}` : 'Select a micro-market'}
              note={sel ? 'profile + member villages' : 'click a point on the map'}>
          {!sel && <p className="dim" style={{ padding: 14 }}>Nothing selected.</p>}
          {sel && <Async state={detail}>{(d: any) => {
            const m = d.micromarket
            if (!m) return <p className="dim">not found</p>
            return (
              <div>
                <div className="pb-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
                  <div className="pb-cell"><span className="pb-k">Archetype</span><span>{m.archetype}</span></div>
                  <div className="pb-cell"><span className="pb-k">HP belt</span><span>{m.hp_belt} · mean {m.mean_hp?.toFixed(0)} HP</span></div>
                  <div className="pb-cell"><span className="pb-k">TIV</span><span>{fmt.units(m.tiv)} tractors</span></div>
                  <div className="pb-cell"><span className="pb-k">Sonalika share</span><span>{(m.sonalika_share * 100).toFixed(1)}%</span></div>
                  <div className="pb-cell"><span className="pb-k">Demand /yr</span><span>{fmt.units(m.potential_units_yr)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Rainfall</span><span>{Math.round(m.rain_normal_mm)} mm · {m.mean_temp?.toFixed(0)}°C</span></div>
                </div>
                <p className="dim" style={{ fontSize: 12, margin: '10px 0 4px' }}>
                  {m.n_villages} villages · top crops {m.top_crops}
                </p>
                <table>
                  <thead><tr><th>Village</th><th style={{ textAlign: 'right' }}>Demand /yr</th><th style={{ textAlign: 'right' }}>Tractors</th></tr></thead>
                  <tbody>
                    {d.villages.map((v: any) => (
                      <tr key={v.village_id}>
                        <td>{v.village}</td>
                        <td style={{ textAlign: 'right' }}>{fmt.units(v.potential_units_yr)}</td>
                        <td style={{ textAlign: 'right' }} className="dim">{fmt.units(v.addressable)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }}</Async>}
        </Card>
      </div>
    </div>
  )
}
