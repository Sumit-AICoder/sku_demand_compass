import React, { useState } from 'react'
import { BarChart, Bar as RBar, XAxis, YAxis, Tooltip, ResponsiveContainer,
         CartesianGrid, ReferenceLine, Cell, ErrorBar } from 'recharts'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi, Badge } from './common'

const FACTORS = [
  ['F1', 'Farm economics'], ['F2', 'Land holding'], ['F3', 'Tractor base'],
  ['F4', 'Mechanisation'], ['F5', 'Cropping pattern'], ['F6', 'Policy & subsidy'],
  ['F7', 'Monsoon & water'], ['F8', 'Custom hiring'], ['F9', 'Technology'],
  ['F10', 'Distribution'],
]
const SHOCKS = [
  ['rainfall_departure', 'Monsoon rainfall'],
  ['reservoir_status', 'Reservoir storage'],
  ['ndvi_anomaly', 'Crop health (NDVI)'],
  ['mandi_price_index', 'Mandi prices'],
  ['credit_depth', 'Rural credit'],
  ['subsidy_intensity', 'Subsidy intensity'],
  ['diesel_price', 'Diesel price'],
  ['rural_wage_index', 'Rural wages'],
]

export default function ScenarioView() {
  const [weights, setWeights] = useState<Record<string, number>>({})
  const [shocks, setShocks] = useState<Record<string, number>>({})
  const [result, setResult] = useState<any>()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string>()

  const run = async () => {
    setBusy(true); setErr(undefined)
    try {
      setResult(await api.scenario({
        weights: Object.keys(weights).length ? weights : undefined,
        shocks: Object.keys(shocks).length ? shocks : undefined,
        level: 'state',
      }))
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  const reset = () => { setWeights({}); setShocks({}); setResult(undefined) }

  return (
    <div className="grid" style={{ gap: 14 }}>
      <div className="split">
        <Card title="Driver shocks" note="in standard deviations from normal">
          {SHOCKS.map(([k, label]) => (
            <div className="slider-row" key={k}>
              <span>{label}</span>
              <input type="range" min={-2} max={2} step={0.25}
                     value={shocks[k] ?? 0}
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
            assured-irrigation one. The confidence band comes from the estimated
            standard errors.
          </p>
        </Card>

        <Card title="Factor weight overrides" note="leave at 0 to keep the model's own weight">
          {FACTORS.map(([k, label]) => (
            <div className="slider-row" key={k}>
              <span>{label}</span>
              <input type="range" min={0} max={0.45} step={0.01}
                     value={weights[k] ?? 0}
                     onChange={e => {
                       const v = Number(e.target.value)
                       setWeights(w => { const n = { ...w }; if (v === 0) delete n[k]; else n[k] = v; return n })
                     }} />
              <span className="mono n" style={{ textAlign: 'right' }}>
                {(weights[k] ?? 0).toFixed(2)}
              </span>
            </div>
          ))}
        </Card>
      </div>

      <div style={{ display: 'flex', gap: 10 }}>
        <button onClick={run} disabled={busy}
                style={{ padding: '8px 18px', borderRadius: 8, border: '1px solid var(--accent)',
                         background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontWeight: 600 }}>
          {busy ? 'Running…' : 'Run scenario'}
        </button>
        <button onClick={reset}
                style={{ padding: '8px 18px', borderRadius: 8, border: '1px solid var(--border-strong)',
                         background: 'var(--panel)', color: 'var(--text)', cursor: 'pointer' }}>
          Reset
        </button>
      </div>

      {err && <div className="err">{err}</div>}

      {result && (
        <>
          <div className="kpis">
            <Kpi k="Baseline" v={fmt.units(result.total.units_base)} s="units / yr" />
            <Kpi k="Scenario" v={fmt.units(result.total.units_scenario)} s="units / yr" />
            <Kpi k="Change"
                 v={<span className={result.total.delta_pct >= 0 ? 'pos' : 'neg'}>
                      {result.total.delta_pct >= 0 ? '+' : ''}{result.total.delta_pct}%
                    </span>}
                 s={`90% CI ${result.total.ci_low_pct}% … ${result.total.ci_high_pct}%`} />
          </div>

          <div className="split">
            <Card title="Impact by state" note="the spread is the point — irrigation buffers a drought">
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={result.by_level} layout="vertical" margin={{ left: 10, right: 24 }}>
                  <CartesianGrid stroke="var(--border)" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 10 }}
                         label={{ value: '% change', position: 'insideBottom', fontSize: 10, dy: 8 }} />
                  <YAxis type="category" dataKey="state" width={120} tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(v: any) => `${Number(v).toFixed(2)}%`} />
                  <ReferenceLine x={0} stroke="var(--border-strong)" />
                  <RBar dataKey="delta_pct" radius={[0, 3, 3, 0]}>
                    {result.by_level.map((r: any, i: number) => (
                      <Cell key={i} fill={r.delta_pct >= 0 ? 'var(--good)' : 'var(--bad)'} />
                    ))}
                    <ErrorBar
                      dataKey={(r: any) => [r.delta_pct - r.delta_pct_lo, r.delta_pct_hi - r.delta_pct] as any}
                      direction="x" width={4} stroke="var(--text-3)" />
                  </RBar>
                </BarChart>
              </ResponsiveContainer>
            </Card>

            <Card title="Shocks applied" tight>
              <div className="tbl-wrap">
                <table>
                  <thead><tr>
                    <th>Driver</th><th className="n">Shock</th><th className="n">β pooled</th>
                    <th className="n">β range across districts</th>
                    <th className="n">Pooled effect</th><th>Usable</th>
                  </tr></thead>
                  <tbody>
                    {result.shocks_applied.map((s: any) => (
                      <tr key={s.regressor}>
                        <td>{s.regressor}</td>
                        <td className="n">{s.shock_sd > 0 ? '+' : ''}{s.shock_sd} sd</td>
                        <td className="n">{s.beta_pooled.toFixed(3)}</td>
                        <td className="n muted">[{s.beta_min.toFixed(3)}, {s.beta_max.toFixed(3)}]</td>
                        <td className={`n ${s.effect_pct_pooled >= 0 ? 'pos' : 'neg'}`}>
                          {s.effect_pct_pooled >= 0 ? '+' : ''}{s.effect_pct_pooled}%
                        </td>
                        <td>{s.usable_share >= 0.5
                          ? <Badge kind="ucm">{Math.round(s.usable_share * 100)}%</Badge>
                          : <Badge kind="prior">low</Badge>}</td>
                      </tr>
                    ))}
                    {!result.shocks_applied.length &&
                      <tr><td colSpan={6} className="muted">no shocks — weight overrides only</td></tr>}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
