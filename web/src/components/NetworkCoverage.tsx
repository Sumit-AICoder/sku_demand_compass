import React, { useMemo, useState } from 'react'
import { api, fmt } from '../lib/api'
import { useStore } from '../lib/store'
import { Card, Kpi, Async, useAsync, Bar, Info } from './common'
import { GeoMap, MapPoint } from './GeoMap'

/**
 * REVIEW · Network coverage — where our dealers are against where the demand is.
 *
 * Two kinds of number share this screen and the difference matters. The dealer COUNTS are
 * real, from the locator. The coverage INDICES are not: sales coverage is a distance decay
 * off a simulated dealer point set, and service coverage is that discounted and noised
 * because ITL has not shared the service master. This screen used to badge the whole sales
 * view "real · dealer locator", which read as a claim about the coverage bars.
 *
 * The map is district-grain because the dealer data is: the locator geocodes to a district
 * and carries no coordinates. The one dealer file that does have lat/lon is simulated, so
 * plotting it as dealer pins would be the most convincing wrong thing on the screen.
 */
const DIAG_COLOR: Record<string, string> = {
  'Defend': 'var(--good)', 'Sales issue': 'var(--c1)',
  'Product issue': 'var(--warn)', 'Monitor': 'var(--text-3)',
}

export default function NetworkCoverage() {
  const { productLine } = useStore()
  const [type, setType] = useState('sales')
  const cov = useAsync(() => api.reviewCoverage(productLine, type), [productLine, type])
  const isTractor = productLine === 'tractors'

  // District centroids, coloured by the toggled coverage index and sized by the demand
  // sitting behind them, so the actionable gap -- weak coverage over real demand -- is the
  // thing that stands out rather than the biggest district.
  const points: MapPoint[] = useMemo(() =>
    (cov.data?.districts ?? []).filter((r: any) => r.lon && r.lat).map((r: any) => ({
      id: r.district_id, name: `${r.district} · ${r.state}`,
      lon: r.lon, lat: r.lat, value: Number(r.demand) || 0,
      // Grey, not transparent and not on the red-amber-green ramp: a district with no dealer
      // rows has to stay visible (it still has demand) without being scored on data we do
      // not have. Vanishing would read as "no demand here", which is the opposite.
      color: r.has_dealer_data
        ? (r.coverage < 0.4 ? 'var(--bad)' : r.coverage < 0.6 ? 'var(--warn)' : 'var(--good)')
        : 'var(--text-3)',
      sub: `${(r.coverage * 100).toFixed(0)}% ${type} coverage (modelled)\n`
        + (r.has_dealer_data
            ? `${r.own_dealers} ours · ${r.competitor_dealers} rival dealers`
            : `no ${productLine} dealer rows — count unknown, not zero`)
        + `\n${fmt.count(r.demand)} units/yr demand`,
    })), [cov.data, type, productLine])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Dealer counts are <span className="pill pill-real">real · dealer locator</span>; the{' '}
        {type} coverage index is{' '}
        {type === 'sales'
          ? <span className="pill pill-client">modelled · distance to the nearest dealer</span>
          : <span className="pill pill-client">dummy · ITL service master pending</span>}.
        {isTractor && <> Sonalika’s own tractor network is <span className="pill pill-client">pending ITL</span> (the rival network is real).</>}
      </div>

      <div className="switch" style={{ width: 'fit-content' }} role="tablist">
        {['sales', 'service'].map(t => (
          <button key={t} role="tab" aria-selected={type === t}
                  className={type === t ? 'on' : ''} onClick={() => setType(t)}>
            {t[0].toUpperCase() + t.slice(1)} coverage
          </button>
        ))}
      </div>

      <Async state={cov}>{(d: any) => {
        const avgCov = d.archetypes.length
          ? d.archetypes.reduce((s: number, r: any) => s + r.coverage, 0) / d.archetypes.length : 0
        const gaps = d.archetypes.filter((r: any) => r.diagnosis === 'Sales issue' && r.coverage < 0.5).length
        return (
          <>
            <div className="grid g3">
              <Kpi k="Sonalika dealers" v={fmt.count(d.own_dealers)}
                   s={isTractor ? 'tractor network pending ITL' : `own ${type} network`} />
              <Kpi k="Competitor dealers" v={fmt.count(d.competitor_dealers)} s={`${d.oems.length}+ rival OEMs`} />
              <Kpi k={`Avg ${type} coverage`} v={`${(avgCov * 100).toFixed(0)}%`} s="across archetypes" />
              <Kpi k="Coverage gaps" v={gaps} s="sales-issue archetypes < 50% covered" />
            </div>

            <Card title={<>Where the network is
                  <Info wide text={<>One bubble per district, coloured by its {type} coverage and
                    sized by the demand sitting there — so a big red bubble is the gap worth
                    closing. A hollow bubble is a district the dealer file does not cover at
                    all, which means the count is <b>unknown</b>, not zero.</>} /></>}
                  note={`${d.districts.length} districts · colour = ${type} coverage · size = demand`}>
              <GeoMap points={points} height={420}
                      legend={<>
                        <span><i style={{ background: 'var(--bad)' }} />under 40%</span>
                        <span><i style={{ background: 'var(--warn)' }} />40–60%</span>
                        <span><i style={{ background: 'var(--good)' }} />over 60%</span>
                        <span><i style={{ background: 'var(--text-3)' }} />no dealer data</span>
                        <span className="muted">· bubble = demand</span>
                      </>} />
              {d.covered_states.length < 3 && (
                <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
                  The {productLine} dealer file covers {d.covered_states.join(' and ')} only, so
                  every other district shows modelled coverage with no dealer count behind it.
                  That is missing data, not an empty network.
                </p>
              )}
            </Card>

            <div className="split">
              <Card title="Coverage by archetype" note="worst-covered first — the actionable gaps">
                <div style={{ maxHeight: 460, overflow: 'auto' }}>
                  <table>
                    <thead><tr>
                      <th>Archetype</th><th>Diagnosis</th>
                      <th>Major competitor
                        <Info wide text={<>The strongest <b>branded</b> rival, not the leader —
                          the unbranded local-fabricator segment leads every archetype, so a
                          leader column would read the same on all 46 rows. Same source and
                          same exclusion as the Define archetype table, so the two agree.</>} /></th>
                      <th style={{ width: 130 }}>{type} coverage</th>
                      {type === 'sales' && <th style={{ textAlign: 'right' }}>% in covered dist.</th>}
                      <th style={{ textAlign: 'right' }}>Micro-mkts</th>
                    </tr></thead>
                    <tbody>
                      {d.archetypes.map((r: any) => (
                        <tr key={r.archetype_id}
                            className={r.diagnosis === 'Sales issue' && r.coverage < 0.5 ? 'row-ws' : ''}>
                          <td>{r.base_name} · {r.hp_belt}
                            <div className="dim" style={{ fontSize: 11 }}>{r.subzone_id} {r.subzone}</div></td>
                          <td><span className="pill" style={{ background: DIAG_COLOR[r.diagnosis], color: '#fff', marginLeft: 0 }}>{r.diagnosis}</span></td>
                          <td>{r.rival ?? '—'}
                            {r.rival_share != null && <div className="dim" style={{ fontSize: 11 }}>
                              {(r.rival_share * 100).toFixed(1)}% share</div>}</td>
                          <td>
                            <Bar value={r.coverage} max={1} color={r.coverage < 0.4 ? 'var(--bad)' : r.coverage < 0.6 ? 'var(--warn)' : 'var(--good)'} />
                            <span className="dim" style={{ fontSize: 11 }}> {(r.coverage * 100).toFixed(0)}%</span>
                          </td>
                          {type === 'sales' && <td style={{ textAlign: 'right' }} className="dim">{(r.pct_covered * 100).toFixed(0)}%</td>}
                          <td style={{ textAlign: 'right' }}>{fmt.count(r.n_micromarkets)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>

              <Card title={`Rival ${productLine} OEMs`} note="real · dealer locator">
                <table>
                  <thead><tr><th>OEM</th><th style={{ textAlign: 'right' }}>Dealers</th><th style={{ textAlign: 'right' }}>Districts</th></tr></thead>
                  <tbody>
                    {d.oems.map((o: any) => (
                      <tr key={o.oem}>
                        <td>{o.oem}</td>
                        <td style={{ textAlign: 'right' }}>{fmt.count(o.dealers)}</td>
                        <td style={{ textAlign: 'right' }} className="dim">{o.districts}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="dim" style={{ fontSize: 12, padding: '10px 14px 0' }}>
                  The highlighted rows are <b>sales-issue archetypes with weak {type} coverage</b> —
                  where the product is proven but the network isn’t there to sell/service it. That is
                  the fastest lever: expand coverage, don’t change the product.
                </p>
              </Card>
            </div>
          </>
        )
      }}</Async>
    </div>
  )
}
