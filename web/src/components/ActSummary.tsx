import React, { useMemo } from 'react'
import {
  BarChart, Bar as RBar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  ComposedChart, Line, Area, ReferenceLine, Legend, Cell,
} from 'recharts'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi, Info, Bar, TIP } from './common'
import { GeoMap, MapPoint } from './GeoMap'
import { ArchetypePicker, useArchetypes, BUCKET_COLOR } from './ActPicker'
import ArchetypeSkus from './ArchetypeSkus'

/**
 * ACT · Archetype summary — the Summary page, scoped to one archetype.
 *
 * Everything the tool knows about a single archetype in one view: what kind of place it is,
 * how big it is, where we stand against the field, what our funnel looks like, what moves
 * sales here, and what the next six months hold. It is the briefing you read before opening
 * the playbook.
 */
// The uplift endpoint returns raw regressor names; nobody outside the model calls a holiday
// "is_holiday".
const DRIVER: Record<string, string> = {
  temperature: 'Weather (temperature)', is_holiday: 'Festive & holidays',
  is_promotion: 'Promotions run', price_drop_pct: 'Price actions',
  competitor: 'Competitor pressure',
}
const driverLabel = (c: string) => DRIVER[c] ?? c

export default function ActSummary() {
  const { b, rows, sel, setSel, chosen } = useArchetypes()
  const s = useAsync(() => api.actSummary(sel!), [sel], !!sel)
  const up = useAsync(() => api.archetypeUcmUplift(sel!, 180), [sel], !!sel)
  const fc = useAsync(() => api.planForecast({ archetype_id: sel, metric: 'demand' }), [sel], !!sel)
  const tiv = useAsync(() => api.planForecast({ archetype_id: sel, metric: 'registrations' }),
                       [sel], !!sel)
  const mm = useAsync(() => api.planBucketMicromarkets(sel!, 400), [sel], !!sel)

  const points: MapPoint[] = useMemo(() => (mm.data?.micromarkets ?? [])
    .filter((m: any) => m.lon && m.lat)
    .map((m: any) => ({
      id: m.micro_market_id, name: `${m.district} · ${m.micro_market_id}`,
      lon: m.lon, lat: m.lat, value: Number(m.tiv) || 0,
      color: BUCKET_COLOR[chosen?.bucket ?? 'Grow'],
      sub: `${fmt.count(m.tiv)} TIV · ${fmt.count(m.deliveries_yr)} deliveries/yr`,
    })), [mm.data, chosen])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        One archetype, everything we know about it. The definition and its agro-climate are{' '}
        <span className="pill pill-real">real</span>; share, sales and the BD funnel are{' '}
        <span className="pill pill-client">modelled · ITL pending</span>.
      </div>

      <Async state={b}>{() => <ArchetypePicker rows={rows} sel={sel} setSel={setSel} />}</Async>

      <Async state={s}>{(d: any) => {
        const id = d.identity, pos = d.position, fun = d.funnel, sz = d.size
        const crops = [['Wheat', d.agro.wheat], ['Rice', d.agro.rice], ['Cotton', d.agro.cotton],
                       ['Soybean', d.agro.soybean], ['Sugarcane', d.agro.sugarcane]]
          .filter(([, v]) => (v as number) > 0.02)
        const hp = [['20-35', d.agro.hp_20_35], ['35-45', d.agro.hp_35_45],
                    ['45-60', d.agro.hp_45_60], ['60+', d.agro.hp_60_plus]]
        const hpTotal = hp.reduce((a, [, v]) => a + (v as number || 0), 0) || 1
        return (
          <>
            <div className="stage-note" style={{ borderColor: BUCKET_COLOR[id.bucket] }}>
              <b style={{ color: BUCKET_COLOR[id.bucket] }}>{id.bucket}.</b>{' '}
              {/* `defining` already opens with the zone, so naming it again reads as a stutter.
                  Without it the zone still has to be said once. */}
              <b>{id.name}</b> — {id.defining ?? `${id.subzone} · ${id.hp_belt}`}.{' '}
              {!id.defining && <>Zone {id.zone} {id.zone_name}. </>}
              Growing season {id.lgp} days, crops {id.top_crops}.{' '}
              {id.n_districts} districts across{' '}
              {id.states.map((x: any) => x.state).join(', ')}.
            </div>

            <div className="grid g4">
              <Kpi k={<span>Fleet (TIV)<Info text={<>Tractors in the field across this
                archetype's {fmt.count(sz.micromarkets)} micro-markets — the base an implement can
                attach to. {fmt.count(pos.tiv_in_reach)} of them sit within reach of a dealer today.</>} /></span>}
                   v={fmt.count(sz.tiv)} s={`${fmt.count(pos.tiv_in_reach)} within dealer reach`} />
              <Kpi k={<span>Demand<Info text={<>Annual implement demand for this archetype —
                new plus replacement, propensity-weighted. This is the pool every play is
                measured against.</>} /></span>}
                   v={fmt.count(sz.demand_units)} s={`units/yr · ${fmt.cr(sz.demand_value_inr)}`} />
              <Kpi k={<span>Our share<Info text={<>Our share of that demand against the
                leading OEM's. We rank #{pos.rank ?? '—'} here.</>} /></span>}
                   v={`${(pos.share * 100).toFixed(1)}%`}
                   s={`vs ${id.name && pos.leader} at ${pos.leader_share == null ? '—' : (pos.leader_share * 100).toFixed(0) + '%'} · rank #${pos.rank ?? '—'}`} />
              <Kpi k={<span>Product fit<Info text={<>How well our current range suits this
                archetype's HP belt and crops. Below 55% no amount of selling moves it — that
                is what puts an archetype in No product fit.</>} /></span>}
                   v={`${(pos.product_fit * 100).toFixed(0)}%`}
                   s={`${(pos.cracked_pct * 100).toFixed(0)}% of micro-markets won`} />
            </div>

            <div className="split">
              <Card title={<>Where we stand<Info wide text={<>
                    Every OEM's share inside this archetype, from district-level shares
                    weighted by the fleet this archetype actually holds in each district.
                    “Local” is the unbranded segment — in implements it leads almost
                    everywhere, which is the real competitive picture.</>} /></>}
                    note="share of this archetype's implement demand">
                <ResponsiveContainer width="100%" height={230}>
                  <BarChart data={d.leaderboard.slice(0, 8).map((x: any) => ({
                    player: x.player, share: x.share * 100 }))} layout="vertical"
                            margin={{ top: 4, right: 30, bottom: 4, left: 78 }}>
                    <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 11 }} unit="%" />
                    <YAxis type="category" dataKey="player" tick={{ fontSize: 11 }} width={86}
                           interval={0} />
                    <Tooltip {...TIP}
                             formatter={(v: any) => `${Number(v).toFixed(1)}%`} />
                    <RBar dataKey="share" name="share" radius={[0, 2, 2, 0]}>
                      {d.leaderboard.slice(0, 8).map((x: any) => (
                        <Cell key={x.player}
                              fill={x.player === 'Sonalika' ? 'var(--c1)' : 'var(--text-3)'} />
                      ))}
                    </RBar>
                  </BarChart>
                </ResponsiveContainer>
                <table style={{ marginTop: 8 }}>
                  <thead><tr>
                    <th>Closest rival</th>
                    <th style={{ textAlign: 'right' }}>Their units</th>
                    <th style={{ textAlign: 'right' }}>Winnable</th>
                    <th style={{ textAlign: 'right' }}>At risk</th>
                  </tr></thead>
                  <tbody>
                    {d.rivals.slice(0, 4).map((x: any) => (
                      <tr key={x.rival}>
                        <td>{x.rival}</td>
                        <td style={{ textAlign: 'right' }}>{fmt.count(x.their_units)}</td>
                        <td style={{ textAlign: 'right', color: 'var(--good)' }}>{fmt.count(x.winnable)}</td>
                        <td style={{ textAlign: 'right', color: 'var(--bad)' }}>{fmt.count(x.at_risk)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>

              <Card title={<>Our funnel here<Info wide text={<>
                    Activities → enquiries → deliveries for the year, and the two rates that
                    connect them. Conversion is deliveries ÷ enquiries; the enquiry rate is
                    enquiries ÷ activities. Loan approval sits behind conversion in the
                    model's own identity, which is why it appears as a lever in the playbook.</>} /></>}
                    note={`conversion ${(fun.conversion_rate * 100).toFixed(0)}% · enquiry rate ${(fun.enquiry_rate * 100).toFixed(0)}%`}>
                <ResponsiveContainer width="100%" height={190}>
                  <BarChart data={[
                    { step: 'Activities', v: fun.activities },
                    { step: 'Enquiries', v: fun.enquiries },
                    { step: 'Deliveries', v: fun.deliveries },
                  ]} layout="vertical" margin={{ top: 4, right: 40, bottom: 4, left: 78 }}>
                    <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v: number) => fmt.units(v)} />
                    <YAxis type="category" dataKey="step" tick={{ fontSize: 11 }} width={78}
                           interval={0} />
                    <Tooltip {...TIP}
                             formatter={(v: any) => fmt.count(v)} />
                    <RBar dataKey="v" name="count" fill="var(--c1)" radius={[0, 2, 2, 0]} />
                  </BarChart>
                </ResponsiveContainer>
                <div className="pb-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr', marginTop: 8 }}>
                  <div className="pb-cell"><span className="pb-k">Loan approval</span><span>{(pos.approval_rate * 100).toFixed(0)}%</span></div>
                  <div className="pb-cell"><span className="pb-k">Sales coverage</span><span>{(pos.sales_coverage * 100).toFixed(0)}%</span></div>
                  <div className="pb-cell"><span className="pb-k">Service coverage</span><span>{(pos.service_coverage * 100).toFixed(0)}%</span></div>
                </div>
              </Card>
            </div>

            <div className="split">
              <Card title={<>What kind of place this is<Info wide text={<>
                    The agro-climate and fleet mix that define the archetype in the first
                    place. Rainfall, temperature and crop shares are observed data; the HP mix
                    is the modelled tractor fleet by power band, which is what decides the
                    implements that fit.</>} /></>}
                    note="agro-climate real · fleet mix modelled">
                <div className="pb-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                  <div className="pb-cell"><span className="pb-k">Rainfall</span><span>{Math.round(d.agro.rain_mm)} mm</span></div>
                  <div className="pb-cell"><span className="pb-k">Mean temp</span><span>{d.agro.temp?.toFixed(1)} °C</span></div>
                  <div className="pb-cell"><span className="pb-k">Irrigation</span><span>{(d.agro.irrigation * 100).toFixed(0)}%</span></div>
                  <div className="pb-cell"><span className="pb-k">Mean HP</span><span>{d.agro.mean_hp?.toFixed(1)}</span></div>
                  <div className="pb-cell"><span className="pb-k">Growing season</span><span>{id.lgp} days</span></div>
                  <div className="pb-cell"><span className="pb-k">Villages</span><span>{fmt.count(sz.villages)}</span></div>
                </div>
                <p className="pb-k" style={{ margin: '12px 0 6px' }}>Crop mix</p>
                {crops.map(([name, v]) => (
                  <div key={name as string} className="row" style={{ gap: 8, marginBottom: 4 }}>
                    <span style={{ width: 78, fontSize: 12 }}>{name}</span>
                    <span style={{ flex: 1 }}><Bar value={v as number} max={1} /></span>
                    <span className="dim" style={{ fontSize: 11, width: 34, textAlign: 'right' }}>
                      {((v as number) * 100).toFixed(0)}%</span>
                  </div>
                ))}
                <p className="pb-k" style={{ margin: '12px 0 6px' }}>Fleet by HP band</p>
                {hp.map(([name, v]) => (
                  <div key={name as string} className="row" style={{ gap: 8, marginBottom: 4 }}>
                    <span style={{ width: 78, fontSize: 12 }}>{name} HP</span>
                    <span style={{ flex: 1 }}><Bar value={(v as number) / hpTotal} max={1} color="var(--c3)" /></span>
                    <span className="dim" style={{ fontSize: 11, width: 34, textAlign: 'right' }}>
                      {(((v as number) / hpTotal) * 100).toFixed(0)}%</span>
                  </div>
                ))}
              </Card>

              <Card title={<>What moves sales here<Info wide text={<>
                    The archetype's own driver model, comparing the last 180 days with the 180
                    before. Because it is fit in levels, each bar is additive units — they add
                    back to the change in sales exactly. Read the badge: the daily history
                    behind it is simulated until ITL supplies real sales.</>} /></>}
                    note="last 180 days vs prior 180 · additive units">
                <Async state={up} empty="no fitted model for this archetype">{(u: any) => (
                  <>
                    <ResponsiveContainer width="100%" height={210}>
                      <BarChart data={u.components.map((c: any) => ({
                        name: driverLabel(c.component), v: c.delta_units }))} layout="vertical"
                                margin={{ top: 4, right: 40, bottom: 4, left: 120 }}>
                        <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" horizontal={false} />
                        <XAxis type="number" tick={{ fontSize: 11 }} />
                        <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={130}
                               interval={0} />
                        <Tooltip {...TIP}
                                 formatter={(v: any) => `${Number(v) > 0 ? '+' : ''}${fmt.count(v)} units`} />
                        <ReferenceLine x={0} stroke="var(--text-3)" />
                        <RBar dataKey="v" name="change vs prior 180 days" radius={[0, 2, 2, 0]}>
                          {u.components.map((c: any) => (
                            <Cell key={c.component}
                                  fill={c.delta_units >= 0 ? 'var(--good)' : 'var(--bad)'} />
                          ))}
                        </RBar>
                      </BarChart>
                    </ResponsiveContainer>
                    <p className="dim" style={{ fontSize: 11 }}>
                      Sales {u.total_growth_pct > 0 ? 'up' : 'down'}{' '}
                      {Math.abs(u.total_growth_pct).toFixed(1)}% on the prior window —{' '}
                      {fmt.count(u.prior_units)} → {fmt.count(u.current_units)} units.{' '}
                      <span className="pill pill-client">simulated history</span>
                    </p>
                  </>
                )}</Async>
              </Card>
            </div>

            <ArchetypeSkus archetypeId={sel} />

            <Card title={<>The next six months<Info wide text={<>
                  This archetype's slice of the district forecast: implement demand month by
                  month, and the tractor registrations that grow the fleet behind it. The
                  fleet line is what “TIV growth” means — it moves with the market, not with
                  anything we do.</>} /></>}
                  note="demand units/month · shaded band is the 90% interval">
              <div className="grid g3" style={{ marginBottom: 10 }}>
                <Kpi k="Demand, next 6 months" v={fmt.count(fc.data?.total?.baseline)} s="implement units" />
                <Kpi k="Fleet added, next 6 months" v={fmt.count(tiv.data?.total?.baseline)}
                     s="tractor registrations — market TIV growth" />
                <Kpi k="Micro-markets" v={fmt.count(sz.micromarkets)}
                     s={`${fmt.count(sz.villages)} villages · ${id.n_districts} districts`} />
              </div>
              <Async state={fc}>{(f: any) => {
                const series = [
                  ...f.history.map((h: any) => ({ month: h.month, actual: h.actual })),
                  ...f.forecast.map((r: any) => ({ month: r.month, baseline: r.baseline,
                                                   band: [r.lo, r.hi] as [number, number] })),
                ]
                const last = f.history[f.history.length - 1]
                if (last) {
                  const i = series.findIndex((x: any) => x.month === last.month)
                  series[i] = { ...series[i], baseline: last.actual }
                }
                return (
                  <ResponsiveContainer width="100%" height={230}>
                    <ComposedChart data={series} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
                      <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" />
                      <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} width={62} tickFormatter={(v: number) => fmt.units(v)} />
                      <Tooltip {...TIP}
                               formatter={(v: any) => Array.isArray(v)
                                 ? `${fmt.units(v[0])} – ${fmt.units(v[1])}` : fmt.units(v)} />
                      <Legend wrapperStyle={{ fontSize: 11 }} />
                      <Area dataKey="band" name="90% band" stroke="none" fill="var(--c1)" fillOpacity={0.12} />
                      <ReferenceLine x={f.history_ends} stroke="var(--text-3)" strokeDasharray="3 3" />
                      <Line dataKey="actual" name="actual" stroke="var(--text-2)" strokeWidth={2} dot={false} connectNulls />
                      <Line dataKey="baseline" name="forecast" stroke="var(--c1)" strokeWidth={2}
                            strokeDasharray="5 4" dot={false} connectNulls />
                    </ComposedChart>
                  </ResponsiveContainer>
                )
              }}</Async>
            </Card>

            <Card title={<>Where it is<Info text={<>Every micro-market in this archetype,
                  sized by fleet. Scattered archetypes need a different coverage plan from
                  concentrated ones — that is what this map is for.</>} /></>}
                  note={`${fmt.count(sz.micromarkets)} micro-markets · bubble = TIV`}>
              <GeoMap points={points} height={460} />
            </Card>
          </>
        )
      }}</Async>
    </div>
  )
}
