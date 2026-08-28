import React, { useMemo } from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi, Info, Bar } from './common'
import { MapPoint } from './GeoMap'
import { MapDrill, useDrill } from './MapDrill'

/**
 * DEFINE · Micro-market & district profile.
 *
 * One screen where there were two. The old Map Explorer plotted micro-markets as dots on a
 * blank canvas and the old District profile was a table with no map, so the same question —
 * "what kind of place is this?" — had two homes and neither could zoom.
 *
 * Here the map drills India → state → district, the layer switches between districts and
 * the micro-markets inside one, and clicking anything opens the same profile panel.
 */
const CROPS: Array<[string, string]> = [
  ['crop_wheat_share', 'Wheat'], ['crop_rice_share', 'Rice'], ['crop_cotton_share', 'Cotton'],
  ['crop_soybean_share', 'Soybean'], ['crop_sugarcane_share', 'Sugarcane'],
  ['crop_maize_share', 'Maize'],
]
const HP_BANDS: Array<[string, string]> = [
  ['hp_20_35', '20-35 HP'], ['hp_35_45', '35-45 HP'],
  ['hp_45_60', '45-60 HP'], ['hp_60_plus', '60+ HP'],
]

export default function MapExplorer() {
  // The drill -- breadcrumb, layer toggle, selection -- is the shared shell, so this screen
  // and Review's market explorer navigate identically instead of by coincidence.
  const drill = useDrill()
  const { state, district, layer, sel } = drill

  const districts = useAsync(() => api.defineDistricts(), [])
  const mms = useAsync(() => api.micromarkets({ district: district!.id, metric: 'tiv', limit: 600 }),
                       [district?.id], !!district)
  const profile = useAsync(() => api.defineProfile(sel!.level, sel!.id), [sel?.level, sel?.id], !!sel)

  // Micro-markets are points; districts are the polygons the map already draws, so at the
  // district layer the only points are the district centres you can click.
  const points: MapPoint[] = useMemo(() => {
    if (district && layer === 'micromarket') {
      return (mms.data?.micromarkets ?? []).filter((m: any) => m.lon && m.lat).map((m: any) => ({
        id: m.micro_market_id, name: `${m.district} · ${m.micro_market_id}`,
        lon: m.lon, lat: m.lat, value: Number(m.tiv) || 0,
        color: sel?.id === m.micro_market_id ? 'var(--accent)' : 'var(--c1)',
        sub: `${fmt.count(m.tiv)} TIV · ${m.n_villages} villages · ${m.hp_belt}`,
      }))
    }
    const rows = (districts.data?.districts ?? [])
      .filter((d: any) => !state || d.state === state)
    return rows.filter((d: any) => d.tiv).map((d: any) => ({
      id: d.district_id, name: d.district, lon: 0, lat: 0, value: Number(d.tiv) || 0,
      color: 'var(--c1)', sub: '',
    })).filter(() => false)      // districts are drawn as polygons, not dots
  }, [district, layer, mms.data, districts.data, state, sel])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Zoom in and click: a state opens its districts, a district opens its micro-markets.
        Agro-climate and soil are <span className="pill pill-real">real</span>, dealer counts
        are <span className="pill pill-real">real · district grain</span>, fleet and share are{' '}
        <span className="pill pill-client">modelled · ITL pending</span>.
      </div>

      <MapDrill drill={drill} points={points}
                mapNote={district ? `${district.name} · ${layer === 'micromarket' ? 'micro-markets, bubble = TIV' : 'district boundary'}`
                                  : state ? `${state} · districts` : 'India · pilot states'}
                mapInfo={<>India → state → district. Clicking a state zooms to its districts;
                  clicking a district zooms again and switches on its micro-markets. The three
                  pilot states are highlighted; the rest of India is context you cannot drill
                  into because the model does not cover it.</>}
                counter={district
                  ? `${mms.data?.micromarkets?.length ?? 0} micro-markets in ${district.name}`
                  : undefined}
                legend={district && layer === 'micromarket'
                  ? <span className="muted">each bubble is a micro-market · size = tractors in the field</span>
                  : undefined}
                right={
        <Card title={<>{profile.data?.name ?? 'Select a place'}
              <Info wide text={<>
                The same profile for either grain. Rainfall, temperature and the crop mix are
                <b> district</b> measurements — a micro-market inherits them and the panel says
                so. Dealer counts are real but geocoded to the district, so a micro-market
                shows distance to the nearest dealer rather than an invented count.</>} /></>}
              note={sel ? (sel.level === 'district' ? 'district profile' : 'micro-market profile')
                        : 'click the map'}>
          {!sel && <p className="dim" style={{ padding: 14 }}>
            Nothing selected. Click a state to zoom in, then a district.
          </p>}
          <Async state={profile}>{(d: any) => {
            const crops = CROPS.filter(([k]) => (d.agro?.[k] ?? 0) > 0.02)
            const hpTotal = HP_BANDS.reduce((a, [k]) => a + (d.scope.hp_mix?.[k] ?? 0), 0) || 1
            return (
              <>
                <div className="pb-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                  <div className="pb-cell"><span className="pb-k">Villages</span><span>{fmt.count(d.scope.villages)}</span></div>
                  {d.level === 'district'
                    ? <div className="pb-cell"><span className="pb-k">Micro-markets</span><span>{fmt.count(d.scope.micromarkets)}</span></div>
                    : <div className="pb-cell"><span className="pb-k">Sonalika share</span>
                        <span>{d.scope.sonalika_share != null
                          ? `${(d.scope.sonalika_share * 100).toFixed(1)}%` : '—'}</span></div>}
                  <div className="pb-cell"><span className="pb-k">Tractors (TIV)</span><span>{fmt.count(d.scope.tiv)}</span></div>
                  <div className="pb-cell"><span className="pb-k">HP belt</span><span>{d.scope.hp_belt || '—'}</span></div>
                  <div className="pb-cell"><span className="pb-k">TIV tier</span><span>{d.scope.tiv_tier || '—'}</span></div>
                  <div className="pb-cell"><span className="pb-k">Mean HP</span><span>{d.scope.mean_hp}</span></div>
                </div>

                <p className="pb-k" style={{ margin: '14px 0 6px' }}>Dealers</p>
                {d.dealers.by_line.length ? (
                  <table>
                    <thead><tr>
                      <th>Line</th>
                      <th style={{ textAlign: 'right' }}>Ours</th>
                      <th style={{ textAlign: 'right' }}>Rivals</th>
                      <th style={{ textAlign: 'right' }}>OEMs</th>
                    </tr></thead>
                    <tbody>
                      {d.dealers.by_line.map((x: any) => (
                        <tr key={x.product_line}>
                          <td>{x.product_line}</td>
                          <td style={{ textAlign: 'right' }}>{fmt.count(x.own_dealers)}</td>
                          <td style={{ textAlign: 'right' }}>{fmt.count(x.competitor_dealers)}</td>
                          <td style={{ textAlign: 'right' }}>{fmt.count(x.n_oems)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : <p className="dim" style={{ fontSize: 11 }}>No dealer rows for this district.</p>}
                {d.scope.dealer_km != null && (
                  <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>
                    Nearest dealer about <b>{d.scope.dealer_km} km</b> away.
                  </p>
                )}
                {d.dealers.oems.length > 0 && (
                  <p className="dim" style={{ fontSize: 11, marginTop: 4 }}>
                    Present here: {d.dealers.oems.map((o: any) => `${o.oem} (${o.dealers})`).join(' · ')}
                  </p>
                )}

                <p className="pb-k" style={{ margin: '14px 0 6px' }}>
                  Agro-climate
                  <span className="pill pill-real" style={{ marginLeft: 8 }}>real</span>
                </p>
                <div className="pb-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                  <div className="pb-cell"><span className="pb-k">Rainfall</span><span>{Math.round(d.agro?.rain_normal_mm ?? 0)} mm</span></div>
                  <div className="pb-cell"><span className="pb-k">Mean temp</span><span>{(d.agro?.mean_temp ?? 0).toFixed(1)} °C{d.agro?.temp_is_allocated ? ' ~' : ''}</span></div>
                  <div className="pb-cell"><span className="pb-k">Irrigation</span><span>{((d.scope.irrigation ?? 0) * 100).toFixed(0)}%</span></div>
                </div>
                <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
                  <b>Soil:</b> {d.soil?.soil_type ?? '—'}
                  {d.soil?.climate ? ` · ${d.soil.climate}` : ''}
                  {d.soil?.lgp_days ? ` · growing period ${d.soil.lgp_days} days` : ''}
                  {d.soil?.aesr_code ? ` · AESR ${d.soil.aesr_code}` : ''}
                </p>

                <p className="pb-k" style={{ margin: '14px 0 6px' }}>Crop mix</p>
                {crops.map(([k, label]) => (
                  <div key={k} className="row" style={{ gap: 8, marginBottom: 4 }}>
                    <span style={{ width: 84, fontSize: 12 }}>{label}</span>
                    <span style={{ flex: 1 }}><Bar value={d.agro[k]} max={1} /></span>
                    <span className="dim" style={{ fontSize: 11, width: 34, textAlign: 'right' }}>
                      {(d.agro[k] * 100).toFixed(0)}%</span>
                  </div>
                ))}
                <p className="dim" style={{ fontSize: 11 }}>
                  Most-grown here: <b>{d.scope.dominant_crop || '—'}</b>
                </p>

                <p className="pb-k" style={{ margin: '14px 0 6px' }}>Fleet by HP band</p>
                {HP_BANDS.map(([k, label]) => (
                  <div key={k} className="row" style={{ gap: 8, marginBottom: 4 }}>
                    <span style={{ width: 84, fontSize: 12 }}>{label}</span>
                    <span style={{ flex: 1 }}>
                      <Bar value={(d.scope.hp_mix?.[k] ?? 0) / hpTotal} max={1} color="var(--c3)" />
                    </span>
                    <span className="dim" style={{ fontSize: 11, width: 34, textAlign: 'right' }}>
                      {(((d.scope.hp_mix?.[k] ?? 0) / hpTotal) * 100).toFixed(0)}%</span>
                  </div>
                ))}

                <p className="dim" style={{ fontSize: 11, marginTop: 14 }}>
                  Zone {d.geography?.zone} {d.geography?.zone_name} · sub-zone{' '}
                  {d.geography?.subzone_id} {d.geography?.subzone} · {d.agro?.state}
                  {d.scope.archetype ? <> · archetype <b>{d.scope.archetype}</b></> : null}
                  <br />{d.provenance?.grain}. {d.dealers?.note}
                </p>
              </>
            )
          }}</Async>
        </Card>
      }>
      <Card title={<>Districts
            <Info wide text={<>Every district in scope, with the agro-climate that defines it
              and the fleet it holds. Click a row to open its profile and move the map there —
              the table and the map are two views of the same selection.</>} /></>}
            note="click a row to select it on the map">
        <Async state={districts}>{(dd: any) => {
          const rows = (dd.districts ?? []).filter((r: any) => !state || r.state === state)
          return (
            <div style={{ maxHeight: 320, overflow: 'auto' }}>
              <table>
                <thead><tr>
                  <th>District</th><th>State</th><th>Zone</th>
                  <th style={{ textAlign: 'right' }}>Micro-mkts</th>
                  <th style={{ textAlign: 'right' }}>Villages</th>
                  <th style={{ textAlign: 'right' }}>TIV</th>
                  <th style={{ textAlign: 'right' }}>Rain</th>
                  <th style={{ textAlign: 'right' }}>Temp</th>
                  <th>Crops</th>
                </tr></thead>
                <tbody>
                  {rows.map((r: any) => (
                    <tr key={r.district_id}
                        className={sel?.id === r.district_id ? 'row-on' : 'row-click'}
                        onClick={() => {
                          drill.goto(r.state, { id: r.district_id, name: r.district })
                          drill.select({ level: 'district', id: r.district_id })
                        }}>
                      <td>{r.district}</td>
                      <td className="dim">{r.state}</td>
                      <td className="dim" style={{ fontSize: 11 }}>{r.zone_name}</td>
                      <td style={{ textAlign: 'right' }}>{fmt.count(r.n_micromarkets)}</td>
                      <td style={{ textAlign: 'right' }}>{fmt.count(r.n_villages)}</td>
                      <td style={{ textAlign: 'right' }}>{fmt.units(r.tiv)}</td>
                      <td style={{ textAlign: 'right' }}>{Math.round(r.rain_normal_mm)}</td>
                      <td style={{ textAlign: 'right' }}>{(r.mean_temp ?? 0).toFixed(1)}{r.temp_is_allocated ? '~' : ''}</td>
                      <td className="dim" style={{ fontSize: 11 }}>{r.top_crops}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }}</Async>
      </Card>
      </MapDrill>
    </div>
  )
}
