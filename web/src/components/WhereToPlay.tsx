import React, { useState, useMemo } from 'react'
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, ReferenceLine, Tooltip,
  ResponsiveContainer, CartesianGrid, Cell,
} from 'recharts'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi, Info, TIP } from './common'
import { GeoMap, MapPoint } from './GeoMap'
import { useStore } from '../lib/store'

/**
 * PLAN · Market prioritisation — every archetype in one of three boxes.
 *
 *   Defend          we are strong here; protect it, no recommendation
 *   Grow            the product works but the share doesn't — this is where effort goes
 *   No product fit  we don't have the product to compete; selling harder won't help
 *
 * Picking a Grow archetype opens its micro-markets in descending TIV with the BD funnel,
 * which is the working list a territory owner actually plans against.
 */
const COLOR: Record<string, string> = {
  'Defend': 'var(--good)', 'Grow': 'var(--c1)', 'No product fit': 'var(--warn)',
}
const WHY: Record<string, string> = {
  'Defend': 'We are strongest here. Keep doing what works — no recommendation from us.',
  'Grow': 'The product fits but the share does not — this is where effort pays back.',
  'No product fit': 'We do not have the right product for this archetype. More selling will not move it.',
}
// Counts are integers -- fmt.units renders a decimal below 1000, which reads as an error
// on a column of micro-market counts.
// Grow is the working list, so it sorts to the top whatever the demand ranking says.
const ORDER: Record<string, number> = { 'Grow': 0, 'Defend': 1, 'No product fit': 2 }

const RULE_HELP = (
  <>
    <b>Two ways to read “market leader”.</b><br />
    <b>#1 OEM</b> is the literal test. On today’s modelled shares it leaves Defend empty:
    the unbranded “Local” segment leads all 53 archetypes and we sit at 6–9% everywhere.<br />
    <b>Top-quartile share</b> reads Defend as relative strength instead — the archetypes
    where our own share is highest. That is the default, because an empty box plans nothing.
  </>
)

export default function WhereToPlay() {
  const productLine = useStore(s => s.productLine)
  const [mode, setMode] = useState('stronghold')
  const [only, setOnly] = useState<string>()          // bucket filter
  const [sel, setSel] = useState<string>()            // archetype filter

  const b = useAsync(() => api.planBuckets({ product: productLine, mode }), [productLine, mode])
  const mm = useAsync(() => api.planBucketMicromarkets(sel!), [sel], !!sel)

  const rows = b.data?.archetypes ?? []
  const chosen = rows.find((r: any) => r.archetype_id === sel)
  // Grow archetypes get the recommendation engine's answer for this archetype: the same
  // back-solve the Growth targets tab runs, surfaced where the decision is being made.
  const rec = useAsync(() => api.planTargets({ archetype_id: sel! }),
                       [sel], !!sel && chosen?.bucket === 'Grow')
  const visible = useMemo(
    () => rows.filter((r: any) => !only || r.bucket === only)
              .sort((x: any, y: any) => ORDER[x.bucket] - ORDER[y.bucket]
                                     || y.potential_units_yr - x.potential_units_yr),
    [rows, only])

  // The map answers whichever question is on screen: with no archetype picked it plots all
  // 53 at their TIV-weighted centres so the buckets read geographically; pick one and it
  // becomes that archetype's micro-markets, so you can see concentration vs spread.
  const points: MapPoint[] = useMemo(() => {
    if (chosen && mm.data) {
      return mm.data.micromarkets
        .filter((m: any) => m.lon && m.lat)
        .map((m: any) => ({
          id: m.micro_market_id, name: `${m.district} · ${m.micro_market_id}`,
          lon: m.lon, lat: m.lat, value: Number(m.tiv) || 0,
          color: COLOR[chosen.bucket],
          sub: `${fmt.count(m.tiv)} TIV · ${fmt.count(m.deliveries_yr)} deliveries/yr · ${m.n_villages} villages`,
        }))
    }
    return visible.filter((r: any) => r.lon && r.lat).map((r: any) => ({
      id: r.archetype_id, name: `${r.base_name} · ${r.hp_belt}`,
      lon: r.lon, lat: r.lat, value: Number(r.tiv) || 0, color: COLOR[r.bucket],
      sub: `${r.bucket} · ${fmt.count(r.n_micromarkets)} micro-markets · ${fmt.units(r.potential_units_yr)} units/yr`,
    }))
  }, [visible, chosen, mm.data])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Every archetype in one of three boxes. Sales, share and the BD funnel are{' '}
        <span className="pill pill-client">modelled · ITL pending</span>; the archetype
        definition and its geography are real.
        <span style={{ float: 'right' }}>
          <label className="dim" style={{ fontSize: 12, marginRight: 6 }}>
            Defend rule<Info text={RULE_HELP} wide />
          </label>
          <select value={mode} onChange={e => { setMode(e.target.value); setSel(undefined) }}>
            <option value="stronghold">Top-quartile share (default)</option>
            <option value="leader">#1 OEM in the archetype</option>
          </select>
        </span>
      </div>

      {/* One filter for the whole screen: tiles, map, scatter and both tables follow it. */}
      <div className="row row-centre" style={{ gap: 12 }}>
        <span className="dim" style={{ fontSize: 12 }}>Showing</span>
        <div className="switch">
          {[undefined, 'Defend', 'Grow', 'No product fit'].map(x => (
            <button key={x ?? 'all'} className={only === x ? 'on' : ''}
                    onClick={() => { setOnly(x); setSel(undefined) }}>
              {x ?? 'All buckets'}
            </button>
          ))}
        </div>
        <span className="dim" style={{ fontSize: 11 }}>
          {visible.length} of {rows.length} archetypes
        </span>
      </div>

      <Async state={b}>{(d: any) => (
        <>
          <div className="grid g3">
            {d.totals.map((t: any) => (
              <div key={t.bucket} className={'row-click' + (only === t.bucket ? ' row-on' : '')}
                   onClick={() => { setOnly(only === t.bucket ? undefined : t.bucket); setSel(undefined) }}>
                <Kpi
                  k={<span>
                      <span style={{ color: COLOR[t.bucket] }}>●</span> {t.bucket}
                      <Info text={<><b>{t.bucket}.</b> {WHY[t.bucket]}<br />
                        Click the box to filter the map and the table below to these archetypes.</>} />
                    </span>}
                  v={`${t.archetypes} archetypes`}
                  s={`${fmt.count(t.micromarkets)} micro-markets · ${fmt.count(t.villages)} villages · ${fmt.units(t.demand)} units/yr`} />
              </div>
            ))}
          </div>

          {d.rule.mode === 'leader' && d.totals[0].archetypes === 0 && (
            <div className="stage-note" style={{ borderColor: 'var(--warn)' }}>
              <b>No archetype qualifies.</b> On today's modelled shares the unbranded
              “Local” segment leads every archetype and Sonalika sits between 6% and 9%
              everywhere — so a literal market-leader test leaves Defend empty. Switch the
              rule back to <i>top-quartile share</i> for a usable split, or wait for real
              ITL share data.
            </div>
          )}

          <div className="split">
          <Card
            title={<>Where they are
              <Info wide text={<>
                <b>Each bubble is one archetype</b>, placed at the TIV-weighted centre of its
                micro-markets, sized by fleet and coloured by bucket. Pick an archetype and
                the bubbles become <b>its micro-markets</b>, so you can see whether the
                opportunity is concentrated in a few districts or spread thin across many.
                The three pilot states are picked out; the rest of India is context.
              </>} /></>}
            note={chosen ? `${chosen.base_name} · ${chosen.hp_belt} — ${fmt.count(chosen.n_micromarkets)} micro-markets`
                         : `${points.length} archetypes${only ? ' · ' + only : ''} · bubble = TIV`}>
            <div className="row" style={{ gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
              <select value={sel ?? ''} onChange={e => setSel(e.target.value || undefined)}
                      style={{ minWidth: 300 }}>
                <option value="">All archetypes (showing centres)</option>
                {visible.map((r: any) => (
                  <option key={r.archetype_id} value={r.archetype_id}>
                    {r.bucket} — {r.base_name} · {r.hp_belt} · {r.subzone_id}
                  </option>
                ))}
              </select>
              {sel && <button onClick={() => setSel(undefined)}>Back to all archetypes</button>}
            </div>
            <GeoMap points={points} selected={sel} height={520}
                    onSelect={id => { if (!chosen) setSel(id) }}
                    legend={chosen
                      ? <span className="muted">micro-markets of the selected archetype · bubble = TIV</span>
                      : <>
                          <span className="muted">bucket</span>
                          {['Defend', 'Grow', 'No product fit'].map(x => (
                            <span key={x}><i style={{ background: COLOR[x] }} />{x}</span>
                          ))}
                        </>} />
          </Card>

          <Card
            title={<>The split, in one picture
              <Info wide text={<>
                <b>The rule, drawn.</b> Left of the dashed line the product does not fit —
                those archetypes are out whatever we spend. Higher up means more share.
                So bubbles at the <b>top right</b> are strongholds to defend, and big bubbles
                at the <b>bottom right</b> are the prize: the product works there and we
                still hold almost none of it.
              </>} /></>}
            note={`${d.rule.defend} · ${d.rule.no_fit} · bubble = TIV`}>
            {(() => {
              const pts = visible.map((r: any) => ({
                id: r.archetype_id, fit: r.product_fit * 100,
                share: r.avg_sonalika_share * 100, tiv: r.tiv,
                name: r.base_name + ' · ' + r.hp_belt, bucket: r.bucket,
              }))
              if (!pts.length) return <div className="loading">nothing in this bucket</div>
              const span = (vals: number[], pad: number): [number, number] =>
                [Math.min(...vals) - pad, Math.max(...vals) + pad]
              const xd = span(pts.map((p: any) => p.fit), 2)
              const yd = span(pts.map((p: any) => p.share), 0.5)
              return (
                <ResponsiveContainer width="100%" height={470}>
                  <ScatterChart margin={{ top: 26, right: 28, bottom: 36, left: 16 }}>
                    <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" />
                    <XAxis type="number" dataKey="fit" name="product fit" domain={xd}
                           tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                           label={{ value: 'product fit →', position: 'insideBottom',
                                    offset: -18, fontSize: 11, fill: 'var(--text-3)' }} />
                    <YAxis type="number" dataKey="share" name="our share" domain={yd}
                           tick={{ fontSize: 11 }} width={62}
                           tickFormatter={(v: number) => `${v.toFixed(1)}%`}
                           label={{ value: 'our share →', angle: -90, position: 'insideLeft',
                                    offset: 2, fontSize: 11, fill: 'var(--text-3)' }} />
                    <ZAxis type="number" dataKey="tiv" range={[40, 420]} />
                    <ReferenceLine x={d.rule.fit_min * 100} stroke="var(--warn)" strokeDasharray="4 4"
                                   label={{ value: 'fit floor', position: 'insideTopLeft',
                                            fontSize: 10, fill: 'var(--warn)', offset: 6 }} />
                    <Tooltip cursor={{ strokeDasharray: '3 3' }}
                             {...TIP}
                             formatter={(v: any, n: string) => [
                               n === 'tiv' ? fmt.units(v) : `${Number(v).toFixed(1)}%`, n]}
                             labelFormatter={() => ''} />
                    <Scatter data={pts} onClick={(p: any) => setSel(p?.id)}>
                      {pts.map((p: any) => (
                        <Cell key={p.id} fill={COLOR[p.bucket]}
                              fillOpacity={sel && sel !== p.id ? 0.2 : 0.65}
                              stroke={sel === p.id ? 'var(--text)' : 'none'} />
                      ))}
                    </Scatter>
                  </ScatterChart>
                </ResponsiveContainer>
              )
            })()}
            <div className="legend" style={{ marginTop: 6 }}>
              <span className="muted">bucket</span>
              {['Defend', 'Grow', 'No product fit'].map(x => (
                <span key={x}><i style={{ background: COLOR[x] }} />{x}</span>
              ))}
              <span className="muted" style={{ marginLeft: 10 }}>
                bubble = TIV · dashed line = fit floor
              </span>
            </div>
          </Card>
          </div>

          <div className="split">
            <Card
              title={<>{only ? `${only} archetypes` : 'All archetypes'}
                <Info wide text={<>
                  <b>One row per archetype.</b> “Us / leader” is our share against the
                  leading OEM's — the gap is the headroom someone else is holding.
                  “Fit” below the floor is what puts an archetype in No product fit.
                  Sorted Grow first, then by annual demand, so the top row is the biggest
                  winnable prize. Click a row to map it and open its micro-markets.
                </>} /></>}
              note="Grow first · click one for its micro-markets">
              <div className="row" style={{ gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
                <select value={sel ?? ''} onChange={e => setSel(e.target.value || undefined)}
                        style={{ minWidth: 260 }}>
                  <option value="">Jump to an archetype…</option>
                  {visible.map((r: any) => (
                    <option key={r.archetype_id} value={r.archetype_id}>
                      {r.base_name} · {r.hp_belt} · {r.subzone_id}
                    </option>
                  ))}
                </select>
              </div>
              <div style={{ maxHeight: 420, overflow: 'auto' }}>
                <table>
                  <thead><tr>
                    <th>Archetype</th><th>Bucket</th>
                    <th style={{ textAlign: 'right' }}>Micro-mkts</th>
                    <th style={{ textAlign: 'right' }}>Villages</th>
                    <th style={{ textAlign: 'right' }}>TIV</th>
                    <th style={{ textAlign: 'right' }}>Us / leader</th>
                    <th style={{ textAlign: 'right' }}>Fit</th>
                    <th style={{ textAlign: 'right' }}>Demand /yr</th>
                  </tr></thead>
                  <tbody>
                    {visible.map((r: any) => (
                      <tr key={r.archetype_id}
                          className={sel === r.archetype_id ? 'row-on' : 'row-click'}
                          style={{ opacity: r.low_demand ? 0.5 : 1 }}
                          onClick={() => setSel(r.archetype_id)}>
                        <td>{r.base_name}<div className="dim" style={{ fontSize: 11 }}>{r.hp_belt} · {r.subzone_id}</div></td>
                        <td><span className="pill" style={{ background: COLOR[r.bucket], color: '#fff', marginLeft: 0 }}>{r.bucket}</span></td>
                        <td style={{ textAlign: 'right' }}>{fmt.count(r.n_micromarkets)}</td>
                        <td style={{ textAlign: 'right' }}>{fmt.count(r.n_villages)}</td>
                        <td style={{ textAlign: 'right' }}>{fmt.units(r.tiv)}</td>
                        <td style={{ textAlign: 'right' }}>
                          {(r.avg_sonalika_share * 100).toFixed(1)}%
                          <span className="dim"> / {r.leader_share == null ? '—' : (r.leader_share * 100).toFixed(0) + '%'}</span>
                        </td>
                        <td style={{ textAlign: 'right' }}>{(r.product_fit * 100).toFixed(0)}%</td>
                        <td style={{ textAlign: 'right' }}>{fmt.units(r.potential_units_yr)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card
              title={<>{chosen ? chosen.base_name + ' · ' + chosen.hp_belt : 'Select an archetype'}
                <Info wide text={<>
                  <b>The working list.</b> The selected archetype's micro-markets, biggest
                  fleet (TIV) first, with the funnel behind each: BD activities → enquiries
                  → deliveries, and conversion = deliveries ÷ enquiries. A micro-market high
                  on TIV but low on activities is missing <b>effort</b>, not demand — that is
                  the row to hand a territory owner. Low conversion on healthy activity is a
                  quality or coverage problem instead.
                </>} /></>}
              note={chosen ? `${fmt.count(chosen.n_micromarkets)} micro-markets · ${chosen.states}` : 'click a row'}>
              {!chosen && <p className="dim" style={{ padding: 14 }}>Nothing selected.</p>}
              {chosen && <div>
                <div className="stage-note" style={{ borderColor: COLOR[chosen.bucket], marginBottom: 12 }}>
                  <b style={{ color: COLOR[chosen.bucket] }}>{chosen.bucket}.</b>{' '}
                  {chosen.bucket === 'Grow'
                    ? `Product fit is ${(chosen.product_fit * 100).toFixed(0)}% but we hold only ${(chosen.avg_sonalika_share * 100).toFixed(1)}% against ${chosen.leader ?? 'the leader'} at ${chosen.leader_share == null ? '—' : (chosen.leader_share * 100).toFixed(0) + '%'}. Work the micro-markets below, biggest fleet first.`
                    : WHY[chosen.bucket]}
                </div>

                {chosen.bucket === 'Grow' && <>
                  <p className="pb-k" style={{ margin: '0 0 6px' }}>
                    What to do here
                    <Info wide text={<>
                      <b>The same back-solve the Growth targets tab runs</b>, for this
                      archetype: a target that closes a quarter of the distance to the
                      leader, and the levers that get there — ranked by the units each one
                      is worth at this archetype's own conversion and enquiry rates.
                      Benchmarks come from Grow archetypes in the same HP belt.
                    </>} />
                  </p>
                  {rec.loading && <div className="loading">working out the levers…</div>}
                  {rec.data && (
                    <div className="stage-note" style={{ marginBottom: 12 }}>
                      <div style={{ marginBottom: 8 }}>
                        Getting to <b>{fmt.count(rec.data.target.units)} units/yr</b>{' '}
                        (from {fmt.count(rec.data.current.deliveries)}, a{' '}
                        {(rec.data.target.share * 100).toFixed(1)}% share) needs{' '}
                        <b>{fmt.count(rec.data.target.delta_enquiries)} more enquiries</b> and{' '}
                        <b>{fmt.count(rec.data.target.delta_activities)} more BD activities</b> a year.
                      </div>
                      <table>
                        <tbody>
                          {rec.data.levers.map((l: any, i: number) => (
                            <tr key={l.lever}>
                              <td style={{ width: 22, color: 'var(--text-3)' }}>
                                {l.kind === 'ceiling' ? '—' : i + 1}
                              </td>
                              <td>
                                <b>{l.lever}</b>
                                <div className="dim" style={{ fontSize: 11 }}>{l.detail}</div>
                              </td>
                              <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                                {l.units ? `+${fmt.count(l.units)} units` : ''}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  <p className="pb-k" style={{ margin: '0 0 6px' }}>
                    Micro-markets, biggest TIV first
                    <Info wide text={<>
                      <b>Where to spend the effort inside the archetype.</b> “Do” is read off
                      each micro-market against its own archetype: a big fleet with
                      below-median activity per tractor is <b>under-worked</b>; healthy
                      activity with below-median conversion is a <b>conversion</b> problem
                      (pitch, product match or finance); poor dealer access is{' '}
                      <b>coverage</b>. Rows already at or above par read “on track”.
                    </>} />
                  </p>
                  <div style={{ maxHeight: 420, overflow: 'auto' }}>
                    <Async state={mm}>{(m: any) => {
                      const med = (xs: number[]) => {
                        const v = xs.filter(Number.isFinite).sort((a, b) => a - b)
                        return v.length ? v[Math.floor(v.length / 2)] : 0
                      }
                      const medActPerTiv = med(m.micromarkets.map((r: any) => r.activities_yr / Math.max(r.tiv, 1)))
                      const medConv = med(m.micromarkets.map((r: any) => r.conversion_rate))
                      const medAcc = med(m.micromarkets.map((r: any) => r.dealer_accessibility))
                      const action = (r: any) => {
                        if (r.activities_yr / Math.max(r.tiv, 1) < medActPerTiv) return ['Under-worked', 'var(--c1)']
                        if (r.conversion_rate < medConv) return ['Conversion', 'var(--warn)']
                        if (r.dealer_accessibility < medAcc) return ['Coverage', 'var(--c3)']
                        return ['On track', 'var(--text-3)']
                      }
                      return (
                      <table>
                        <thead><tr>
                          <th>Micro-market</th>
                          <th style={{ textAlign: 'right' }}>TIV</th>
                          <th style={{ textAlign: 'right' }}>BD acts</th>
                          <th style={{ textAlign: 'right' }}>Enq</th>
                          <th style={{ textAlign: 'right' }}>Deliv</th>
                          <th style={{ textAlign: 'right' }}>Conv</th>
                          <th>Do</th>
                        </tr></thead>
                        <tbody>
                          {m.micromarkets.map((r: any) => {
                            const [label, colour] = action(r)
                            return (
                            <tr key={r.micro_market_id}>
                              <td>{r.district}<div className="dim" style={{ fontSize: 11 }}>{r.micro_market_id} · {r.n_villages} villages</div></td>
                              <td style={{ textAlign: 'right' }}>{fmt.count(r.tiv)}</td>
                              <td style={{ textAlign: 'right' }}>{fmt.count(r.activities_yr)}</td>
                              <td style={{ textAlign: 'right' }}>{fmt.count(r.enquiries_yr)}</td>
                              <td style={{ textAlign: 'right' }}>{fmt.count(r.deliveries_yr)}</td>
                              <td style={{ textAlign: 'right' }}>{(r.conversion_rate * 100).toFixed(0)}%</td>
                              <td><span style={{ color: colour, fontSize: 11, fontWeight: 600 }}>{label}</span></td>
                            </tr>
                          )})}
                        </tbody>
                      </table>
                    )}}</Async>
                  </div>
                </>}

                {chosen.bucket !== 'Grow' && (
                  <div className="pb-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                    <div className="pb-cell"><span className="pb-k">Our share</span><span>{(chosen.avg_sonalika_share * 100).toFixed(1)}%</span></div>
                    <div className="pb-cell"><span className="pb-k">Leader</span><span>{chosen.leader ?? '—'}</span></div>
                    <div className="pb-cell"><span className="pb-k">Product fit</span><span>{(chosen.product_fit * 100).toFixed(0)}%</span></div>
                    <div className="pb-cell"><span className="pb-k">TIV</span><span>{fmt.units(chosen.tiv)}</span></div>
                    <div className="pb-cell"><span className="pb-k">Villages</span><span>{fmt.count(chosen.n_villages)}</span></div>
                    <div className="pb-cell"><span className="pb-k">Demand /yr</span><span>{fmt.units(chosen.potential_units_yr)}</span></div>
                  </div>
                )}
              </div>}
            </Card>
          </div>
        </>
      )}</Async>
    </div>
  )
}
