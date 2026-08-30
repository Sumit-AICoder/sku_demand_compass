import React, { useState, useMemo } from 'react'
import { ComposedChart, Area, Line, Bar as RBar, BarChart, XAxis, YAxis, Tooltip,
         CartesianGrid, ResponsiveContainer, ReferenceLine, Cell, Legend,
         ErrorBar, ScatterChart, Scatter } from 'recharts'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi } from './common'

/**
 * REVIEW · What drives sales — per-archetype Unobserved Components Model.
 *
 * Sonalika has no real daily sales feed (only the annual estimates used elsewhere in
 * Review), so this whole panel is SIMULATED illustrative daily history built so the UCM
 * can cleanly recover known effects — badged throughout. The model is fit in LEVELS
 * (sales units, not log), so every uplift is additive: Baseline (trend + weekly + annual
 * seasonal, stripped of weather/holiday/promo/price/competition) + the five factor
 * uplifts + residual = Actual, exactly, by construction.
 */
const UPLIFT_COLORS: Record<string, string> = {
  temperature: 'var(--c3)', is_holiday: 'var(--c2)', is_promotion: 'var(--good)',
  price_drop_pct: 'var(--c6)', competitor: 'var(--bad)',
}
const UPLIFT_LABEL: Record<string, string> = {
  temperature: 'Weather', is_holiday: 'Holiday', is_promotion: 'Promotion',
  price_drop_pct: 'Price drop', competitor: 'Competition',
}
const WINDOWS = [90, 180, 365]

export default function WhatDrivesSales() {
  const archList = useAsync(() => api.reviewArchetypes(), [])
  const [aid, setAid] = useState<string>()
  const [windowDays, setWindowDays] = useState(365)

  const archetypes = archList.data?.archetypes ?? []
  const chosen = aid ?? archetypes[0]?.archetype_id
  const chosenRow = archetypes.find((a: any) => a.archetype_id === chosen)

  const dec = useAsync(() => api.archetypeUcmDecomposition(chosen!), [chosen], !!chosen)
  const uplift = useAsync(() => api.archetypeUcmUplift(chosen!, Math.min(windowDays, 180)),
                          [chosen, windowDays], !!chosen)
  const elas = useAsync(() => api.archetypeUcmElasticities(chosen), [chosen], !!chosen)
  const pooled = useAsync(() => api.archetypeUcmElasticities(), [])
  const diagAll = useAsync(() => api.archetypeUcmDiagnostics(), [])

  // Archetype × SKU: the UCM has no product dimension of its own (one series per
  // archetype, everything it sells combined) -- picking a SKU allocates the archetype's
  // curve by that SKU's static demand share. Every numeric field scales by the same
  // constant, so the additive identity (baseline + uplifts = predicted) holds exactly
  // regardless of which SKU is picked, same as at the archetype level.
  const skus = useAsync(() => api.archetypeSkus(chosen!, 50), [chosen], !!chosen)
  const basket = skus.data ?? []
  const [skuId, setSkuId] = useState<string>()
  const skuShare = useMemo(() => {
    if (!skuId) return 1
    const tot = basket.reduce((s: number, x: any) => s + x.units, 0)
    const mine = basket.find((x: any) => x.sku_id === skuId)?.units ?? 0
    return tot ? mine / tot : 0
  }, [skuId, basket])
  const skuName = basket.find((x: any) => x.sku_id === skuId)?.name

  const full = dec.data?.series ?? []
  const scaledFull = useMemo(() => {
    if (skuShare === 1) return full
    const fields = ['actual_sales', 'predicted', 'baseline', ...Object.keys(UPLIFT_LABEL).map(k => `uplift_${k}`)]
    return full.map((r: any) => {
      const out: any = { date: r.date }
      for (const f of fields) out[f] = r[f] != null ? r[f] * skuShare : r[f]
      return out
    })
  }, [full, skuShare])
  const windowed = useMemo(() => scaledFull.slice(-windowDays), [scaledFull, windowDays])
  const d = dec.data?.diagnostics

  const upliftKeys = Object.keys(UPLIFT_LABEL)

  return (
    <div className="grid" style={{ gap: 14 }}>
      <div className="stage-note">
        Daily sales history is <span className="pill pill-client">simulated · illustrative</span> —
        Sonalika has no real daily/weekly feed, only the annual estimates used on the other
        Review tabs (this panel's annual totals tie back to those). The model is fit in{' '}
        <b>sales units</b> (not log), so Baseline + every factor's uplift add up to Predicted
        exactly, and <b>Competitor</b> pressure is built to carry a negative effect — which is
        exactly why Predicted can dip <i>below</i> Baseline: whenever competitor pressure and/or
        hot weather outweigh the positive effects (holiday, promotion, price cuts) on a given
        day, that's the model naming a real headwind, not an error in the chart.
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <Async state={archList}>{() => (
          <select value={chosen ?? ''} onChange={e => { setAid(e.target.value); setSkuId(undefined) }}
                  style={{ minWidth: 260 }}>
            {archetypes.map((a: any) => (
              <option key={a.archetype_id} value={a.archetype_id}>
                {a.base_name} · {a.hp_belt} — {a.diagnosis}
              </option>
            ))}
          </select>
        )}</Async>
        <select value={skuId ?? ''} onChange={e => setSkuId(e.target.value || undefined)}
                style={{ minWidth: 200 }}>
          <option value="">Whole archetype</option>
          {basket.map((s: any) => <option key={s.sku_id} value={s.sku_id}>{s.name}</option>)}
        </select>
        <div className="switch">
          {WINDOWS.map(w => (
            <button key={w} className={windowDays === w ? 'on' : ''} onClick={() => setWindowDays(w)}>
              {w}d
            </button>
          ))}
        </div>
        {chosenRow && <span className="dim" style={{ fontSize: 12 }}>
          {fmt.units(chosenRow.n_micromarkets)} micro-markets · {chosenRow.subzone_id} {chosenRow.subzone}
        </span>}
      </div>

      {d && (
        <div className="grid g3">
          <Kpi k="Model fit (R²-like)" v={fmt.num(d.r2_like, 3)} s={`${d.n_obs} daily observations`} />
          <Kpi k="Backtest WAPE" v={fmt.pct(d.backtest_wape)}
               s={<span className={d.beats_snaive ? 'pos' : 'neg'}>
                    vs seasonal-naive {fmt.pct(d.snaive_wape)} — {d.beats_snaive ? 'beats it' : 'does not beat it'}
                  </span>} />
          <Kpi k="Additive identity" v={d.identity_ok ? 'holds exactly' : 'error'}
               s={`max error ${d.identity_max_abs_error?.toExponential?.(1) ?? d.identity_max_abs_error}`} />
        </div>
      )}
      {skuName && (
        <p className="note">
          <span className="pill pill-secondary">allocated</span> Showing{' '}
          <b>{skuName}</b>'s static share ({(skuShare * 100).toFixed(1)}%) of this
          archetype's demand, scaled onto the archetype's own curve — the SKU gets the
          archetype's shape and factor-sensitivity, not its own fitted seasonality. Model
          fit / WAPE / identity above describe the archetype-level fit and don't change
          with the SKU picked (scaling every term by the same constant keeps the additive
          identity exact either way).
        </p>
      )}

      <Card title="Panel 1 — Actual vs Predicted vs Baseline"
            note="Baseline = trend + weekly + annual seasonal, stripped of weather/holiday/promo/price/competition">
        <Async state={dec}>{() => (
          <ResponsiveContainer width="100%" height={280}>
            <ComposedChart data={windowed} margin={{ left: -12, right: 10, top: 8 }}>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={50}
                     tickFormatter={(v: string) => v?.slice(0, 10)} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip labelFormatter={(v: any) => String(v).slice(0, 10)}
                       formatter={(v: any) => Number(v).toFixed(1)} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Area type="monotone" dataKey="actual_sales" name="Actual sales"
                    stroke="var(--c1)" fill="var(--c1)" fillOpacity={0.14} />
              <Line type="monotone" dataKey="predicted" name="Predicted (fitted)" stroke="var(--c5)"
                    strokeWidth={1.6} dot={false} strokeDasharray="4 3" />
              <Line type="monotone" dataKey="baseline" name="Baseline (trend+seasonal)" stroke="var(--good)"
                    strokeWidth={1.8} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        )}</Async>
      </Card>

      <Card title="Panel 2 — Additive contribution: Baseline + factor uplifts = Predicted"
            note="stacked so the net height of the stack traces the Predicted line">
        <Async state={dec}>{() => (
          <ResponsiveContainer width="100%" height={280}>
            <ComposedChart data={windowed} margin={{ left: -12, right: 10, top: 8 }} stackOffset="sign">
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={50}
                     tickFormatter={(v: string) => v?.slice(0, 10)} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip labelFormatter={(v: any) => String(v).slice(0, 10)}
                       formatter={(v: any) => Number(v).toFixed(1)} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <ReferenceLine y={0} stroke="var(--border-strong)" />
              <Area type="monotone" dataKey="baseline" name="Baseline" stackId="u"
                    stroke="var(--c4)" fill="var(--c4)" fillOpacity={0.5} />
              {upliftKeys.map(k => (
                <Area key={k} type="monotone" dataKey={`uplift_${k}`} name={UPLIFT_LABEL[k]}
                      stackId="u" stroke={UPLIFT_COLORS[k]} fill={UPLIFT_COLORS[k]} fillOpacity={0.65} />
              ))}
              <Line type="monotone" dataKey="predicted" name="Predicted" stroke="var(--text)"
                    strokeWidth={1.4} dot={false} strokeDasharray="2 2" />
            </ComposedChart>
          </ResponsiveContainer>
        )}</Async>
        <p className="note">
          <b>Competition</b> stacks downward (negative by construction — more aggressive
          rival pricing lowers our sales). Everything else stacks upward from the baseline.
          The dashed line is Predicted, which the stack's net height reproduces exactly.
        </p>
      </Card>

      <div className="split">
        <Card title="Uplift attribution — trailing vs prior period"
              note="what produced the recent change, in sales units">
          <Async state={uplift}>{(u: any) => (
            <>
              <div style={{ display: 'flex', gap: 22, marginBottom: 10, alignItems: 'baseline' }}>
                <div>
                  <div className="muted" style={{ fontSize: 11 }}>TOTAL CHANGE</div>
                  <div style={{ fontSize: 26, fontWeight: 650 }}
                       className={u.total_growth_pct >= 0 ? 'pos' : 'neg'}>
                    {u.total_growth_pct >= 0 ? '+' : ''}{u.total_growth_pct}%
                  </div>
                </div>
                <div className="note">
                  {fmt.units(u.prior_units)} → {fmt.units(u.current_units)} units
                  over the last {u.days} days vs the {u.days} before
                </div>
              </div>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={u.components} layout="vertical" margin={{ left: 10, right: 20 }}>
                  <XAxis type="number" tick={{ fontSize: 10 }}
                         label={{ value: 'pp of prior-period volume', position: 'insideBottom', fontSize: 10, dy: 8 }} />
                  <YAxis type="category" dataKey="component" width={150} tick={{ fontSize: 10 }} />
                  <Tooltip formatter={(v: any) => `${Number(v).toFixed(2)} pp`} />
                  <ReferenceLine x={0} stroke="var(--border-strong)" />
                  <RBar dataKey="pp_of_growth" radius={[0, 3, 3, 0]}>
                    {u.components.map((c: any, i: number) => (
                      <Cell key={i} fill={c.pp_of_growth >= 0
                        ? (c.kind === 'structural' ? 'var(--c1)' : 'var(--good)')
                        : 'var(--bad)'} />
                    ))}
                  </RBar>
                </BarChart>
              </ResponsiveContainer>
            </>
          )}</Async>
        </Card>

        <Card title="Elasticities in this archetype"
              note="90% intervals; β = sales units per unit of the driver">
          <Async state={elas}>{(rows: any[]) => (
            <div className="tbl-wrap" style={{ maxHeight: 340 }}>
              <table>
                <thead><tr>
                  <th>Driver</th><th className="n">β (est.)</th><th className="n">true β</th>
                  <th className="n">90% CI</th><th>Sig.</th>
                </tr></thead>
                <tbody>
                  {rows.map((r: any) => (
                    <tr key={r.regressor}>
                      <td>{r.label}</td>
                      <td className={`n ${r.beta >= 0 ? 'pos' : 'neg'}`}>{r.beta.toFixed(3)}</td>
                      <td className="n muted">{r.true_beta?.toFixed(3)}</td>
                      <td className="n muted">[{r.ci_low.toFixed(2)}, {r.ci_high.toFixed(2)}]</td>
                      <td>{r.significant
                        ? <span className="pill pill-real">sig.</span>
                        : <span className="pill dim">n.s.</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}</Async>
        </Card>
      </div>

      <div className="split">
        <Card title="Pooled elasticities across all archetypes" note="mean β with 90% interval, vs the known true β">
          <Async state={pooled}>{(rows: any[]) => (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={rows} layout="vertical" margin={{ left: 12, right: 22 }}>
                <CartesianGrid stroke="var(--border)" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 10 }} />
                <YAxis type="category" dataKey="label" width={110} tick={{ fontSize: 10 }} />
                <Tooltip formatter={(v: any) => Number(v).toFixed(3)} />
                <ReferenceLine x={0} stroke="var(--border-strong)" />
                <RBar dataKey="beta" name="Estimated β" radius={[0, 3, 3, 0]}>
                  {rows.map((r: any, i: number) => (
                    <Cell key={i} fill={r.beta >= 0 ? 'var(--c2)' : 'var(--c5)'} />
                  ))}
                  <ErrorBar dataKey={(r: any) => [r.beta - r.ci_low, r.ci_high - r.beta] as any}
                            direction="x" width={4} stroke="var(--text-3)" />
                </RBar>
              </BarChart>
            </ResponsiveContainer>
          )}</Async>
          <p className="note">
            Every archetype's model recovers the driver's known sign — competitor pressure
            is negative in all 53, by construction and by estimation.
          </p>
        </Card>

        <Card title="Model quality across archetypes"
              note="each point one archetype — below the line means the UCM beats seasonal-naive">
          <Async state={diagAll}>{(dd: any) => (
            <ResponsiveContainer width="100%" height={280}>
              <ScatterChart margin={{ left: -6, right: 14, top: 10, bottom: 12 }}>
                <CartesianGrid stroke="var(--border)" />
                <XAxis type="number" dataKey="snaive_wape" name="seasonal-naive WAPE"
                       tick={{ fontSize: 10 }}
                       label={{ value: 'seasonal-naive WAPE %', position: 'insideBottom', fontSize: 10, dy: 10 }} />
                <YAxis type="number" dataKey="backtest_wape" name="UCM WAPE" tick={{ fontSize: 10 }}
                       label={{ value: 'UCM WAPE %', angle: -90, position: 'insideLeft', fontSize: 10 }} />
                <Tooltip formatter={(v: any) => `${Number(v).toFixed(1)}%`}
                         content={({ payload }: any) => payload?.[0] ? (
                           <div className="maptip" style={{ position: 'static' }}>
                             <b>{payload[0].payload.base_name} · {payload[0].payload.hp_belt}</b>
                             <div className="muted">UCM {payload[0].payload.backtest_wape?.toFixed(1)}% vs
                               naive {payload[0].payload.snaive_wape?.toFixed(1)}%</div>
                           </div>) : null} />
                <Scatter data={dd.archetypes} fill="var(--c1)" fillOpacity={0.6} />
              </ScatterChart>
            </ResponsiveContainer>
          )}</Async>
        </Card>
      </div>
    </div>
  )
}
