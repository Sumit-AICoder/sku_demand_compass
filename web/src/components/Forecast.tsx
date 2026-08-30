import React, { useState, useEffect, useMemo } from 'react'
import {
  ComposedChart, Line, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  ReferenceLine, Legend, BarChart, Bar as RBar, Cell,
} from 'recharts'
import { api, fmt } from '../lib/api'
import { useStore } from '../lib/store'
import { Card, Async, useAsync, Kpi, Info, TIP, Bar } from './common'
import { ArchetypePicker } from './ActPicker'

/**
 * PLAN · Forecast — six months forward, and what the sliders do to it.
 *
 * Two separable pieces, and the screen says so: the SHAPE is the district UCM's own
 * forecast (trend + estimated seasonal + drivers at a normal year), the SHIFT is each
 * shock propagated through each district's own elasticity. Before this screen the tool
 * had no forward-looking number anywhere — the old tab was called "forecast" and showed
 * a single annual scalar.
 */
const SHOCKS: Array<[string, string]> = [
  ['rainfall_departure', 'Monsoon rainfall'],
  ['reservoir_status', 'Reservoir storage'],
  ['ndvi_anomaly', 'Crop health (NDVI)'],
  ['mandi_price_index', 'Mandi prices'],
  ['credit_depth', 'Rural credit'],
  ['subsidy_intensity', 'Subsidy intensity'],
  ['diesel_price', 'Diesel price'],
  ['rural_wage_index', 'Rural wages'],
]
const FACTORS: Array<[string, string]> = [
  ['F1', 'Farm economics'], ['F2', 'Land holding'], ['F3', 'Tractor base'],
  ['F4', 'Mechanisation'], ['F5', 'Cropping pattern'], ['F6', 'Policy & subsidy'],
  ['F7', 'Monsoon & water'], ['F8', 'Custom hiring'], ['F9', 'Technology'],
  ['F10', 'Distribution'],
]
const STATES = ['Punjab', 'Madhya Pradesh', 'Maharashtra']
const BUCKETS = ['Grow', 'Defend', 'No product fit']

export default function Forecast() {
  const { productLine } = useStore()
  const [shocks, setShocks] = useState<Record<string, number>>({})
  const [weights, setWeights] = useState<Record<string, number>>({})
  const [metric, setMetric] = useState('demand')
  const [scopeKind, setScopeKind] = useState<'all' | 'state' | 'bucket' | 'archetype'>('all')
  const [stateSel, setStateSel] = useState(STATES[0])
  const [bucketSel, setBucketSel] = useState(BUCKETS[0])
  const buckets = useAsync(() => api.planBuckets({ product: productLine }),
                           [productLine, scopeKind], scopeKind === 'archetype')
  const archRows = buckets.data?.archetypes ?? []
  const [archId, setArchId] = useState<string>()
  const skuBasket = useAsync(() => api.archetypeSkus(archId!, 50), [archId, scopeKind],
                             scopeKind === 'archetype' && !!archId)
  const [skuId, setSkuId] = useState<string>()
  const chosenArch = archRows.find((r: any) => r.archetype_id === archId)

  const state = scopeKind === 'state' ? stateSel : undefined
  const bucket = scopeKind === 'bucket' ? bucketSel : undefined
  const archetypeId = scopeKind === 'archetype' ? archId : undefined
  const skuIdSel = scopeKind === 'archetype' ? skuId : undefined
  const [run, setRun] = useState(0)                 // bumped only by Run scenario / Reset
  // Snapshot of the shocks/weights the chart currently reflects. Sliders only ever write
  // to `shocks`/`weights` above; nothing recomputes until Run copies them in here.
  const [applied, setApplied] = useState<{ shocks: Record<string, number>; weights: Record<string, number> }>(
    { shocks: {}, weights: {} })

  const body = useMemo(() => ({
    shocks: applied.shocks, weights: applied.weights, metric,
    state, bucket, archetype_id: archetypeId, sku_id: skuIdSel, product: productLine,
  }), [run, metric, scopeKind, stateSel, bucketSel, archId, skuId, productLine]) // eslint-disable-line react-hooks/exhaustive-deps

  const f = useAsync(() => api.planForecast(body), [body])

  // useAsync clears its data while refetching, which would blank the whole screen —
  // sliders included — every time Run fires. Hold the last good response and keep
  // rendering it; only the "updating…" hint changes.
  const [last, setLast] = useState<any>()
  useEffect(() => { if (f.data) setLast(f.data) }, [f.data])
  const d = f.data ?? last

  const runScenario = () => { setApplied({ shocks, weights }); setRun(r => r + 1) }
  const reset = () => {
    setShocks({}); setWeights({}); setApplied({ shocks: {}, weights: {} }); setRun(r => r + 1)
  }
  const pending = JSON.stringify(shocks) !== JSON.stringify(applied.shocks)
               || JSON.stringify(weights) !== JSON.stringify(applied.weights)
  const dirty = Object.keys(applied.shocks).length > 0 || Object.keys(applied.weights).length > 0

  return (
    <div className="grid" style={{ gap: 14 }}>
      <div className="stage-note">
        Six months forward from the district UCM — trend, its own estimated seasonal shape,
        and the drivers at a normal year. The sliders move the <b>scenario</b> line only;
        the baseline stays put so you can see what your assumption did.{' '}
        <span className="pill pill-secondary">allocated · UCM</span>
      </div>

      {f.err && <div className="err">{String(f.err)}</div>}
      {!d && <div className="loading">loading…</div>}
      {d && (() => {
        const series = [
          ...d.history.map((h: any) => ({ month: h.month, actual: h.actual })),
          ...d.forecast.map((r: any) => ({
            month: r.month, baseline: r.baseline, scenario: r.scenario,
            band: [r.lo, r.hi] as [number, number],
          })),
        ]
        // Join the last actual to the first forecast so the lines don't float apart.
        const lastActual = d.history[d.history.length - 1]
        const firstFc = series.find((s: any) => s.baseline != null)
        if (lastActual && firstFc) {
          const i = series.findIndex((s: any) => s.month === lastActual.month)
          series[i] = { ...series[i], baseline: lastActual.actual, scenario: lastActual.actual }
        }
        const t = d.total
        return (
          <>
            <div className="grid g4">
              <Kpi k="Next 6 months, baseline" v={fmt.units(t.baseline)} s={d.unit} />
              <Kpi k="Scenario" v={fmt.units(t.scenario)}
                   s={pending ? 'sliders changed — click Run scenario'
                      : dirty ? `${t.delta_pct > 0 ? '+' : ''}${t.delta_pct}% vs baseline` : 'move a slider'} />
              <Kpi k="90% band" v={`${t.ci_low_pct}% … ${t.ci_high_pct}%`}
                   s="from the estimated standard errors" />
              <Kpi k="Scope" v={d.scope.sku_name ?? state ?? bucket ?? chosenArch?.base_name ?? 'All three states'}
                   s={d.scope.sku_name
                      ? `${d.scope.sku_share_pct}% of ${chosenArch?.base_name ?? 'this archetype'}'s basket · ${d.scope.districts} districts`
                      : `${d.scope.districts} districts · history to ${d.history_ends}`} />
            </div>

            <Card
              title={<>Actual, then forecast
                <Info wide text={<>
                  <b>Left of the dashed rule is what happened; right of it is what the model
                  expects.</b> The baseline is a normal year from here — drivers held at their
                  seasonal average. The scenario line is that path with your slider
                  assumptions applied through each district's own elasticity. The shaded band
                  is the 90% interval: the forecast's own uncertainty, widened by the
                  uncertainty in the driver effects you dialled in.
                  {d.scope.sku_name && <> <b>Picking a SKU allocates</b> the archetype's own
                  forecast shape and shock-sensitivity to that product's static demand
                  share — it is not a forecast fitted on that SKU's own history.</>}
                </>} /></>}
              note={`${d.unit} · shaded band is the 90% interval · the rule marks where history ends`}>
              <div className="row" style={{ gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
                <select value={metric} onChange={e => setMetric(e.target.value)}>
                  <option value="demand">Implement demand (units/mo)</option>
                  <option value="registrations">Tractor registrations (TIV added/mo)</option>
                </select>
                <select value={scopeKind} onChange={e => {
                  setScopeKind(e.target.value as any); setArchId(undefined); setSkuId(undefined)
                }}>
                  <option value="all">All three states</option>
                  <option value="state">By state</option>
                  <option value="bucket">By bucket</option>
                  <option value="archetype">By archetype</option>
                </select>
                {scopeKind === 'state' && (
                  <select value={stateSel} onChange={e => setStateSel(e.target.value)}>
                    {STATES.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                )}
                {scopeKind === 'bucket' && (
                  <select value={bucketSel} onChange={e => setBucketSel(e.target.value)}>
                    {BUCKETS.map(b => <option key={b} value={b}>{b}</option>)}
                  </select>
                )}
                {scopeKind === 'archetype' && (
                  <>
                    <Async state={buckets}>{() => (
                      <ArchetypePicker rows={archRows} sel={archId}
                                       setSel={id => { setArchId(id); setSkuId(undefined) }} />
                    )}</Async>
                    {archId && (
                      <select value={skuId ?? ''} onChange={e => setSkuId(e.target.value || undefined)}>
                        <option value="">Whole archetype</option>
                        {(skuBasket.data ?? []).map((s: any) => (
                          <option key={s.sku_id} value={s.sku_id}>{s.name}</option>
                        ))}
                      </select>
                    )}
                  </>
                )}
                {f.loading && <span className="dim" style={{ fontSize: 12 }}>updating…</span>}
              </div>
              <ResponsiveContainer width="100%" height={300}>
                <ComposedChart data={series} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
                  <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" />
                  <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} width={64}
                         tickFormatter={(v: number) => fmt.units(v)} />
                  <Tooltip {...TIP}
                           formatter={(v: any, n: string) => [Array.isArray(v)
                             ? `${fmt.units(v[0])} – ${fmt.units(v[1])}` : fmt.units(v), n]} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Area dataKey="band" name="90% band" stroke="none"
                        fill="var(--c1)" fillOpacity={0.12} />
                  <ReferenceLine x={d.history_ends} stroke="var(--text-3)" strokeDasharray="3 3"
                                 label={{ value: 'forecast →', position: 'insideTopRight', fontSize: 10 }} />
                  <Line dataKey="actual" name="actual" stroke="var(--text-2)" strokeWidth={2}
                        dot={false} connectNulls />
                  <Line dataKey="baseline" name="baseline forecast" stroke="var(--c1)"
                        strokeWidth={2} strokeDasharray="5 4" dot={false} connectNulls />
                  <Line dataKey="scenario" name="scenario" stroke="var(--good)" strokeWidth={2.5}
                        dot={{ r: 2 }} connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
            </Card>

            {!!d.by_category?.length && (
              <Card
                title={<>Forecast by category
                  <Info wide text={<>
                    <b>An allocated split, not a second model.</b> The UCM forecasts one
                    aggregate series per district — it has no product dimension of its own.
                    Each category's slice here is its static share of this scope's demand,
                    shaped by that category's own SKUs' seasonal index, then rescaled so
                    every row sums exactly back to the baseline total above. One caveat
                    worth naming: the seasonal shape is a single national curve, not
                    district-specific, and shares are held static across the six months.
                  </>} /></>}
                note="6-month baseline total, split by category · allocated">
                <table>
                  <thead><tr>
                    <th>Category</th>
                    <th style={{ textAlign: 'right' }}>Units, 6mo</th>
                    <th style={{ textAlign: 'right' }}>Share</th>
                    <th style={{ width: 90 }} />
                    <th>Peaks in</th>
                  </tr></thead>
                  <tbody>
                    {d.by_category.map((c: any) => (
                      <tr key={c.category}>
                        <td>{c.category_label}</td>
                        <td style={{ textAlign: 'right' }}>{fmt.units(c.units_6mo)}</td>
                        <td style={{ textAlign: 'right' }}>{c.share_pct}%</td>
                        <td><Bar value={c.units_6mo} max={d.by_category[0].units_6mo} /></td>
                        <td className="dim">{c.peak_month}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            )}

            <div className="split">
              <Card title="Driver shocks" note="in standard deviations from normal">
                {SHOCKS.map(([k, label]) => (
                  <div className="slider-row" key={k}>
                    <span>{label}</span>
                    <input type="range" min={-2} max={2} step={0.25} value={shocks[k] ?? 0}
                           onChange={e => {
                             const v = Number(e.target.value)
                             setShocks(s => { const n = { ...s }; if (v === 0) delete n[k]; else n[k] = v; return n })
                           }} />
                    <span className="mono n" style={{ textAlign: 'right' }}>
                      {(shocks[k] ?? 0) > 0 ? '+' : ''}{(shocks[k] ?? 0).toFixed(2)} sd
                    </span>
                  </div>
                ))}
                <p className="note" style={{ marginTop: 8 }}>
                  Shocks propagate through each <strong>district's own</strong> estimated
                  elasticity, so a drought hits a rainfed district far harder than an
                  assured-irrigation one — which is why the per-state split below is the
                  interesting part, not the headline number.
                </p>
              </Card>

              <Card title="Factor weight overrides" note="leave at 0 to keep the model's own weight">
                {FACTORS.map(([k, label]) => (
                  <div className="slider-row" key={k}>
                    <span>{label}</span>
                    <input type="range" min={0} max={0.45} step={0.01} value={weights[k] ?? 0}
                           onChange={e => {
                             const v = Number(e.target.value)
                             setWeights(w => { const n = { ...w }; if (v === 0) delete n[k]; else n[k] = v; return n })
                           }} />
                    <span className="mono n" style={{ textAlign: 'right' }}>{(weights[k] ?? 0).toFixed(2)}</span>
                  </div>
                ))}
                <p className="note" style={{ marginTop: 8 }}>
                  Weights re-score demand itself rather than the time path, so they land as
                  a level shift on the scenario line.
                </p>
              </Card>
            </div>

            <div style={{ display: 'flex', gap: 10 }}>
              <button onClick={runScenario}
                      style={{ padding: '8px 18px', borderRadius: 8, border: '1px solid var(--accent)',
                               background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontWeight: 600 }}>
                Run scenario
              </button>
              <button onClick={reset}
                      style={{ padding: '8px 18px', borderRadius: 8, border: '1px solid var(--border-strong)',
                               background: 'var(--panel)', color: 'var(--text)', cursor: 'pointer' }}>
                Reset
              </button>
            </div>

            {!!d.by_state.length && (
              <Card
                title={<>Where the shock lands
                  <Info wide text={<>
                    <b>The same shock, three different answers.</b> Each district responds
                    with the elasticity estimated for it, so a dry monsoon hits rainfed
                    Maharashtra far harder than assured-irrigation Punjab. If this chart were
                    flat, the model would be telling you nothing a national average couldn't.
                  </>} /></>}
                note="% change in the 6-month forecast, by state">
                <ResponsiveContainer width="100%" height={140}>
                  <BarChart data={d.by_state} layout="vertical"
                            margin={{ top: 4, right: 30, bottom: 4, left: 96 }}>
                    <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 11 }} unit="%" />
                    <YAxis type="category" dataKey="state" tick={{ fontSize: 11 }} width={96} interval={0} />
                    <Tooltip {...TIP}
                             formatter={(v: any) => `${Number(v).toFixed(1)}%`} />
                    <ReferenceLine x={0} stroke="var(--text-3)" />
                    <RBar dataKey="delta_pct" radius={[0, 2, 2, 0]}>
                      {d.by_state.map((r: any) => (
                        <Cell key={r.state} fill={r.delta_pct < 0 ? 'var(--bad)' : 'var(--good)'} />
                      ))}
                    </RBar>
                  </BarChart>
                </ResponsiveContainer>
              </Card>
            )}

            {!!d.shocks_applied.length && (
              <Card
                title={<>Shocks applied
                  <Info wide text={<>
                    <b>The elasticity behind each slider.</b> β pooled is the average effect
                    of a 1-sd move in that driver; β range is how far it varies across the
                    114 districts. “Usable” is the share of districts where the estimate is
                    statistically significant <i>and</i> signed the way agronomy says it
                    should be — a low number there means treat that slider as directional.
                  </>} /></>}
                note="each district's own beta, with the spread across districts">
                <table>
                  <thead><tr>
                    <th>Driver</th>
                    <th style={{ textAlign: 'right' }}>Shock</th>
                    <th style={{ textAlign: 'right' }}>β pooled</th>
                    <th style={{ textAlign: 'right' }}>β range</th>
                    <th style={{ textAlign: 'right' }}>Effect</th>
                    <th style={{ textAlign: 'right' }}>Usable</th>
                  </tr></thead>
                  <tbody>
                    {d.shocks_applied.map((s: any) => (
                      <tr key={s.regressor}>
                        <td>{SHOCKS.find(([k]) => k === s.regressor)?.[1] ?? s.regressor}</td>
                        <td style={{ textAlign: 'right' }}>{s.shock_sd > 0 ? '+' : ''}{s.shock_sd} sd</td>
                        <td style={{ textAlign: 'right' }}>{s.beta_pooled}</td>
                        <td style={{ textAlign: 'right' }} className="dim">{s.beta_min} … {s.beta_max}</td>
                        <td style={{ textAlign: 'right' }}>{s.effect_pct_pooled}%</td>
                        <td style={{ textAlign: 'right' }}>{(s.usable_share * 100).toFixed(0)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            )}
          </>
        )
      })()}
    </div>
  )
}
