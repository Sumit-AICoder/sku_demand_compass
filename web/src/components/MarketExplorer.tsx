import React, { useMemo } from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Info, Bar } from './common'
import { MapPoint } from './GeoMap'
import { MapDrill, useDrill } from './MapDrill'

/**
 * REVIEW · Market explorer.
 *
 * Define's first tab answers "what kind of place is this". This one answers the next
 * question — "how are we doing here, and what explains it" — with the same drill and the
 * same click, so the two stages read as one motion rather than two tools.
 *
 * Three buckets, in the order they earn attention: what we sold, who farms here, and what
 * grows here. The old Micro-market details screen had the first of those on a dot cloud with
 * no zoom and neither of the other two, so there was no way to ask why a market was weak.
 *
 * Demand stays on this screen where it came off Define's: market share IS sales ÷ demand,
 * and the funnel is sized off it.
 */
const METRICS: Array<[string, string]> = [
  ['sonalika_sales_units', 'Sonalika sales'],
  ['sonalika_share', 'Market share'],
  ['potential_units_yr', 'Demand'],
  ['activities_yr', 'BD activities'],
  ['tiv', 'Tractors (TIV)'],
  ['product_fit', 'Product fit'],
]
// The DES crop table is a foodgrain extract, so these are the only shares it can fill in.
// Cotton, soybean and sugarcane are structurally zero there and belong to the modelled
// dominant-crop line instead, which is why the panel keeps the two apart.
const FOODGRAINS: Array<[string, string]> = [
  ['crop_wheat_share', 'Wheat'], ['crop_rice_share', 'Rice'], ['crop_maize_share', 'Maize'],
  ['crop_gram_share', 'Gram'], ['crop_bajra_share', 'Bajra'], ['crop_jowar_share', 'Jowar'],
]
const DIAG_COLOR: Record<string, string> = {
  'Defend': 'var(--good)', 'Sales issue': 'var(--c1)',
  'Product issue': 'var(--warn)', 'Monitor': 'var(--text-3)',
}

const pct = (v: number | null | undefined, dp = 1) =>
  v == null ? '—' : `${(v * 100).toFixed(dp)}%`

export default function MarketExplorer() {
  const drill = useDrill()
  const [metric, setMetric] = React.useState('sonalika_sales_units')

  const mms = useAsync(
    () => api.reviewMicromarkets({ district: drill.district!.id, metric, limit: 700 }),
    [drill.district?.id, metric], !!drill.district)
  const profile = useAsync(() => api.reviewProfile(drill.sel!.level, drill.sel!.id),
                           [drill.sel?.level, drill.sel?.id], !!drill.sel)

  const points: MapPoint[] = useMemo(() => {
    if (!drill.district || drill.layer !== 'micromarket') return []
    const rows = (mms.data?.micromarkets ?? []).filter((m: any) => m.lon && m.lat)
    return rows.map((m: any) => ({
      id: m.micro_market_id,
      name: `${m.district} · ${m.micro_market_id}`,
      lon: m.lon, lat: m.lat,
      value: Math.max(Number(m[metric]) || 0, 0),
      color: drill.sel?.id === m.micro_market_id ? 'var(--accent)'
             : DIAG_COLOR[m.diagnosis] ?? 'var(--c1)',
      sub: `${fmt.count(m.sonalika_sales_units)} sold of ${fmt.count(m.potential_units_yr)} demand`
           + ` · ${m.diagnosis}`,
    }))
  }, [drill.district, drill.layer, drill.sel, mms.data, metric])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Zoom in and click for the three things that decide a market: what we sold, who farms
        there, and what grows there. The funnel and market share are{' '}
        <span className="pill pill-client">modelled · ITL pending</span>, farm structure is{' '}
        <span className="pill pill-alloc">allocated</span>, soil and climate are{' '}
        <span className="pill pill-real">real</span>.
      </div>

      <div className="row row-centre" style={{ gap: 12 }}>
        <span className="dim" style={{ fontSize: 11 }}>Bubble size</span>
        <select value={metric} onChange={e => setMetric(e.target.value)}>
          {METRICS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <span className="dim" style={{ fontSize: 11 }}>colour = diagnosis</span>
      </div>

      <MapDrill drill={drill} points={points}
                mapNote={drill.district
                  ? `${drill.district.name} · ${drill.layer === 'micromarket'
                      ? METRICS.find(m => m[0] === metric)?.[1] : 'district boundary'}`
                  : undefined}
                mapInfo={<>India → state → district, then the micro-markets inside it.
                  Bubbles are sized by the metric you pick and coloured by their diagnosis,
                  so a district of amber dots is a product problem and a district of blue
                  ones is a selling problem.</>}
                counter={drill.district
                  ? `${mms.data?.micromarkets?.length ?? 0} micro-markets in ${drill.district.name}`
                  : undefined}
                legend={drill.district && drill.layer === 'micromarket'
                  ? <>
                      {(['Sales issue', 'Product issue', 'Monitor', 'Defend'] as const).map(k => (
                        <span key={k}><i style={{ background: DIAG_COLOR[k] }} />{k}</span>
                      ))}
                      <span className="muted">
                        · bubble = {METRICS.find(m => m[0] === metric)?.[1].toLowerCase()}
                      </span>
                    </>
                  : undefined}
                right={
        <Card title={<>{profile.data?.name ?? 'Select a place'}
              <Info wide text={<>The same three buckets at either grain. Rainfall,
                temperature, soil and the crop table are district measurements — a
                micro-market inherits them and the footer says so. Everything under
                <b> what we sold</b> and <b>who farms here</b> is its own.</>} /></>}
              note={profile.data ? `${profile.data.level === 'district' ? 'district' : 'micro-market'} profile`
                                 : 'click the map'}>
          {!drill.sel && <p className="dim" style={{ padding: 14 }}>
            Nothing selected. Click a state to zoom in, then a district.</p>}
          {drill.sel && <Async state={profile}>{(d: any) => {
            const s = d.sales, dm = d.demographics
            const maxStep = Math.max(s.activities, 1)
            const grains = FOODGRAINS.filter(([k]) => (d.agro?.[k] ?? 0) > 0.01)
            return (
              <>
                {d.archetype?.diagnosis && (
                  <div className="stage-note"
                       style={{ borderColor: DIAG_COLOR[d.archetype.diagnosis], marginBottom: 12 }}>
                    <b style={{ color: DIAG_COLOR[d.archetype.diagnosis] }}>
                      {d.archetype.diagnosis}.</b>{' '}
                    {d.archetype.name}
                  </div>
                )}

                <p className="pb-k" style={{ margin: '2px 0 6px' }}>What we sold</p>
                <div className="pb-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                  <div className="pb-cell"><span className="pb-k">Sales /yr</span><span>{fmt.count(s.sales_units)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Demand /yr</span><span>{fmt.count(s.demand)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Market share</span><span>{pct(s.share)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Tractors (TIV)</span><span>{fmt.count(s.tiv)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Product fit</span><span>{pct(s.product_fit, 0)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Unserved</span><span>{fmt.count(s.unserved)}</span></div>
                </div>

                <p className="pb-k" style={{ margin: '14px 0 4px' }}>
                  BD funnel /yr
                  <Info text={<>Activities become enquiries become deliveries. Deliveries and
                    sales units are one number, not two — the last bar is the sale.</>} />
                </p>
                {([['Activities', s.activities], ['Enquiries', s.enquiries],
                   ['Deliveries', s.deliveries]] as Array<[string, number]>).map(([k, v]) => (
                  <div key={k} className="row" style={{ gap: 8, alignItems: 'center', marginBottom: 4 }}>
                    <span className="dim" style={{ fontSize: 11, width: 78 }}>{k}</span>
                    <span style={{ flex: 1 }}><Bar value={v} max={maxStep} color="var(--c1)" /></span>
                    <span style={{ fontSize: 11, width: 62, textAlign: 'right' }}>{fmt.count(v)}</span>
                  </div>
                ))}
                <p className="dim" style={{ fontSize: 11, marginTop: 4 }}>
                  {pct(s.enquiry_rate, 0)} of activities become enquiries ·{' '}
                  {pct(s.conversion_rate, 0)} of enquiries convert
                </p>

                <p className="pb-k" style={{ margin: '14px 0 6px' }}>
                  Who farms here
                  <Info wide text={<>Published state totals — Census 2011 rural population,
                    the state × tier operational-holding mix — split down to villages by
                    model and summed back up to whatever you clicked. The totals are real;
                    the split is not.</>} />
                  <span className="pill pill-alloc" style={{ marginLeft: 6 }}>allocated</span>
                </p>
                <div className="pb-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                  <div className="pb-cell"><span className="pb-k">Rural population</span><span>{fmt.count(dm.population)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Households</span><span>{fmt.count(dm.households)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Holdings</span><span>{fmt.count(dm.holdings)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Avg holding</span><span>{dm.avg_holding_ha?.toFixed(2) ?? '—'} ha</span></div>
                  <div className="pb-cell"><span className="pb-k">Small &amp; marginal</span><span>{pct(dm.small_marginal_share, 0)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Farm income</span><span>₹{fmt.count(dm.farm_income_inr)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Tractors /1000 ha</span><span>{dm.tractor_density?.toFixed(0) ?? '—'}</span></div>
                  <div className="pb-cell"><span className="pb-k">Fleet age</span><span>{dm.fleet_mean_age?.toFixed(1) ?? '—'} yr</span></div>
                  <div className="pb-cell"><span className="pb-k">Loan approval</span><span>{pct(dm.approval_rate, 0)}</span></div>
                </div>

                <p className="pb-k" style={{ margin: '14px 0 6px' }}>
                  What grows here
                  <span className="pill pill-real" style={{ marginLeft: 6 }}>real</span>
                </p>
                <div className="pb-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                  <div className="pb-cell"><span className="pb-k">Rainfall</span><span>{Math.round(d.agro?.rain_normal_mm ?? 0)} mm</span></div>
                  <div className="pb-cell"><span className="pb-k">Mean temp</span><span>{(d.agro?.mean_temp ?? 0).toFixed(1)} °C{d.agro?.temp_is_allocated ? ' ~' : ''}</span></div>
                  <div className="pb-cell"><span className="pb-k">Irrigation</span><span>{pct(d.irrigation, 0)}</span></div>
                </div>
                <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
                  <b>Soil:</b> {d.soil?.soil_type ?? '—'} · {d.soil?.climate ?? '—'} ·
                  growing period {d.soil?.lgp_days ?? '—'} days · AESR {d.soil?.aesr_code ?? '—'}
                </p>

                <p className="pb-k" style={{ margin: '12px 0 4px' }}>
                  Foodgrain area mix
                  <Info wide text={<>Real district crop area from DES — but the source is a
                    <b> foodgrain</b> extract, so cotton, soybean and sugarcane cannot appear
                    here even where they dominate. The <b>most-grown</b> line below comes from
                    the modelled village crop mix, which does cover them. Two sources, kept
                    apart rather than merged into one misleading chart.</>} />
                </p>
                {grains.map(([k, label]) => (
                  <div key={k} className="row" style={{ gap: 8, alignItems: 'center', marginBottom: 3 }}>
                    <span className="dim" style={{ fontSize: 11, width: 60 }}>{label}</span>
                    <span style={{ flex: 1 }}><Bar value={d.agro[k]} max={1} color="var(--c2)" /></span>
                    <span className="dim" style={{ fontSize: 11, width: 34, textAlign: 'right' }}>
                      {((d.agro[k] ?? 0) * 100).toFixed(0)}%</span>
                  </div>
                ))}
                <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>
                  Most-grown here: <b>{d.dominant_crop ?? '—'}</b>{' '}
                  <span className="pill pill-client">modelled</span>
                </p>

                <p className="pb-k" style={{ margin: '14px 0 4px' }}>
                  Who we are up against
                  <Info wide text={<>The strongest <b>branded</b> rival. The outright leader is
                    the unbranded local-fabricator segment almost everywhere, so naming it
                    would say the same thing on every row and point at no one you can plan
                    against. Competitor shares are measured per district, so a micro-market
                    shows its district's — the same number the archetype table names, rather
                    than a second estimate that disagrees with it.</>} />
                </p>
                <p style={{ fontSize: 13, margin: 0 }}>
                  <b>{d.competitor?.rival ?? '—'}</b>
                  {d.competitor?.rival_share != null && (
                    <span className="dim"> at {pct(d.competitor.rival_share, 0)}</span>)}
                  <span className="dim"> · outright leader {d.competitor?.leader ?? '—'}</span>
                  {d.competitor?.grain === 'district' && d.level === 'micromarket' && (
                    <span className="dim"> · district grain</span>)}
                </p>

                <p className="dim" style={{ fontSize: 11, marginTop: 14 }}>
                  Zone {d.geography?.zone} {d.geography?.zone_name} · sub-zone{' '}
                  {d.geography?.subzone_id} · {d.scope?.state}
                  <br />{d.provenance?.grain}.
                </p>
              </>
            )
          }}</Async>}
        </Card>}
      />
    </div>
  )
}
