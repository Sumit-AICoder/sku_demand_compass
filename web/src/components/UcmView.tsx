import React, { useState } from 'react'
import { ComposedChart, Area, Line, Bar as RBar, BarChart, XAxis, YAxis, Tooltip,
         CartesianGrid, ResponsiveContainer, ReferenceLine, Cell, Legend,
         ErrorBar, ScatterChart, Scatter } from 'recharts'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Badge, Kpi } from './common'
import Narrative from './Narrative'

const COMP_COLORS: Record<string, string> = {
  trend: 'var(--c1)', seasonal: 'var(--c2)', cycle: 'var(--c4)',
  regression: 'var(--c3)', irregular: 'var(--text-3)',
}

export default function UcmView() {
  const districts = useAsync(() => api.geo('district', {}), [])
  const [did, setDid] = useState<string>()
  const chosen = did ?? districts.data?.items?.[0]?.id

  const dec = useAsync(() => api.ucmDecomposition(chosen!), [chosen], !!chosen)
  const uplift = useAsync(() => api.ucmUplift(chosen!), [chosen], !!chosen)
  const elas = useAsync(() => api.ucmElasticities(chosen), [chosen], !!chosen)
  const pooled = useAsync(() => api.ucmElasticities(), [])
  const diag = useAsync(() => api.ucmDiagnostics(), [])

  const series = (dec.data?.series ?? []).map((r: any) => ({
    month: r.month, observed: r.observed, fitted: r.fitted,
    trend: r.trend, seasonal: r.seasonal, cycle: r.cycle,
    regression: r.regression, irregular: r.irregular,
  }))
  const d = dec.data?.diagnostics

  return (
    <div className="grid" style={{ gap: 14 }}>
      {chosen && <Narrative view="ucm" params={{ district_id: chosen }} />}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={chosen ?? ''} onChange={e => setDid(e.target.value)}>
          {(districts.data?.items ?? []).map(i =>
            <option key={i.id} value={i.id}>{i.name} — {i.parent}</option>)}
        </select>
        <span className="note" style={{ maxWidth: 760 }}>
          Monthly tractor registrations decomposed into an unobserved local linear trend,
          a stochastic seasonal, a damped cycle, and the contribution of each agri driver.
          Coefficients are elasticities: a 1-sd move in the driver shifts sales by β×100 %.
        </span>
      </div>

      {d && (
        <div className="kpis">
          <Kpi k="Model fit (R²-like)" v={fmt.num(d.r2_like, 3)} s={`${d.n_obs} monthly observations`} />
          <Kpi k="Backtest MAPE" v={fmt.pct(d.backtest_mape)}
               s={<span className={d.beats_snaive ? 'pos' : 'neg'}>
                    vs seasonal-naive {fmt.pct(d.snaive_mape)} — {d.beats_snaive ? 'beats it' : 'does not beat it'}
                  </span>} />
          <Kpi k="Residual autocorrelation" v={d.resid_autocorr_ok ? 'clean' : 'present'}
               s={`Ljung-Box p = ${fmt.num(d.ljung_box_p, 3)}`} />
          <Kpi k="Residual normality" v={d.resid_normal_ok ? 'normal' : 'non-normal'}
               s={`Jarque-Bera p = ${fmt.num(d.jarque_bera_p, 3)}`} />
          <Kpi k="Usable for weighting" v={d.usable_for_weights ? 'yes' : 'no'}
               s="gates whether its βs become score weights" />
        </div>
      )}

      <Card title="Sales decomposition" note="components sum exactly to the observed log series">
        <Async state={dec}>{() => (
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={series} margin={{ left: -12, right: 10, top: 8 }}>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey="month" tick={{ fontSize: 10 }} minTickGap={38} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip formatter={(v: any) => Number(v).toFixed(2)} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Area type="monotone" dataKey="observed" name="Observed units"
                    stroke="var(--c1)" fill="var(--c1)" fillOpacity={0.14} />
              <Line type="monotone" dataKey="fitted" name="Fitted" stroke="var(--c5)"
                    strokeWidth={1.6} dot={false} strokeDasharray="4 3" />
            </ComposedChart>
          </ResponsiveContainer>
        )}</Async>

        <Async state={dec}>{() => (
          <ResponsiveContainer width="100%" height={215}>
            <ComposedChart data={series} margin={{ left: -12, right: 10, top: 14 }} stackOffset="sign">
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey="month" tick={{ fontSize: 10 }} minTickGap={38} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip formatter={(v: any) => Number(v).toFixed(3)} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <ReferenceLine y={0} stroke="var(--border-strong)" />
              {['seasonal', 'cycle', 'regression', 'irregular'].map(k => (
                <RBar key={k} dataKey={k} stackId="c" fill={COMP_COLORS[k]}
                      name={k[0].toUpperCase() + k.slice(1)} />
              ))}
            </ComposedChart>
          </ResponsiveContainer>
        )}</Async>
        <p className="note">
          Top: observed versus fitted. Bottom: the log-space components other than trend,
          stacked around zero. <strong>Seasonal</strong> is the buying rhythm,
          <strong> cycle</strong> the multi-year credit/commodity swing,
          <strong> regression</strong> the combined pull of the agri drivers, and
          <strong> irregular</strong> what the model cannot explain.
        </p>
      </Card>

      <div className="split">
        <Card title="Year-on-year uplift attribution"
              note="what produced this year's change">
          <Async state={uplift}>{(u: any) => (
            <>
              <div style={{ display: 'flex', gap: 22, marginBottom: 10, alignItems: 'baseline' }}>
                <div>
                  <div className="muted" style={{ fontSize: 11 }}>TOTAL GROWTH</div>
                  <div style={{ fontSize: 26, fontWeight: 650 }}
                       className={u.total_growth_pct >= 0 ? 'pos' : 'neg'}>
                    {u.total_growth_pct >= 0 ? '+' : ''}{u.total_growth_pct}%
                  </div>
                </div>
                <div className="note">
                  {fmt.units(u.prior_units)} → {fmt.units(u.current_units)} units
                  over the last 12 months
                </div>
              </div>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={u.components} layout="vertical" margin={{ left: 10, right: 20 }}>
                  <XAxis type="number" tick={{ fontSize: 10 }}
                         label={{ value: 'pp of growth', position: 'insideBottom', fontSize: 10, dy: 8 }} />
                  <YAxis type="category" dataKey="component" width={140} tick={{ fontSize: 10 }} />
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

        <Card title="Elasticities in this district"
              note="90% intervals; β = % change per 1-sd driver move">
          <Async state={elas}>{(rows: any[]) => (
            <div className="tbl-wrap" style={{ maxHeight: 400 }}>
              <table>
                <thead><tr>
                  <th>Driver</th><th>Factor</th><th className="n">β</th>
                  <th className="n">90% CI</th><th>Status</th>
                </tr></thead>
                <tbody>
                  {rows.map((r: any) => (
                    <tr key={r.regressor}>
                      <td>{r.regressor}</td>
                      <td className="muted mono">{r.factor}</td>
                      <td className={`n ${r.beta >= 0 ? 'pos' : 'neg'}`}>{r.beta.toFixed(3)}</td>
                      <td className="n muted">[{r.ci_low.toFixed(2)}, {r.ci_high.toFixed(2)}]</td>
                      <td>{r.usable
                        ? <Badge kind="ucm">used</Badge>
                        : <Badge kind="prior">{!r.sign_ok ? 'wrong sign' : 'not sig.'}</Badge>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}</Async>
        </Card>
      </div>

      <div className="split">
        <Card title="Pooled elasticities across all districts"
              note="mean β with 90% interval">
          <Async state={pooled}>{(rows: any[]) => (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={rows} layout="vertical" margin={{ left: 12, right: 22 }}>
                <CartesianGrid stroke="var(--border)" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 10 }} />
                <YAxis type="category" dataKey="regressor" width={148} tick={{ fontSize: 10 }} />
                <Tooltip formatter={(v: any) => Number(v).toFixed(3)} />
                <ReferenceLine x={0} stroke="var(--border-strong)" />
                <RBar dataKey="beta" radius={[0, 3, 3, 0]}>
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
            Every driver's sign is audited against the impact direction the Excel
            asserts. A β that is insignificant, wrong-signed, or comes from a model that
            fails the backtest is <strong>not</strong> used as a score weight — the
            judgmental prior is used instead and badged as such.
          </p>
        </Card>

        <Card title="Model quality across districts"
              note="each point one district — below the line means the UCM beats seasonal-naive">
          <Async state={diag}>{(dd: any) => (
            <>
              <ResponsiveContainer width="100%" height={250}>
                <ScatterChart margin={{ left: -6, right: 14, top: 10, bottom: 12 }}>
                  <CartesianGrid stroke="var(--border)" />
                  <XAxis type="number" dataKey="snaive_mape" name="seasonal-naive MAPE"
                         tick={{ fontSize: 10 }}
                         label={{ value: 'seasonal-naive MAPE %', position: 'insideBottom', fontSize: 10, dy: 10 }} />
                  <YAxis type="number" dataKey="backtest_mape" name="UCM MAPE" tick={{ fontSize: 10 }}
                         label={{ value: 'UCM MAPE %', angle: -90, position: 'insideLeft', fontSize: 10 }} />
                  <Tooltip formatter={(v: any) => `${Number(v).toFixed(1)}%`} />
                  <Scatter data={dd.districts} fill="var(--c1)" fillOpacity={0.55} />
                </ScatterChart>
              </ResponsiveContainer>
              <div className="tbl-wrap" style={{ maxHeight: 150 }}>
                <table>
                  <thead><tr><th>Driver</th><th className="n">VIF</th><th>Collinearity</th></tr></thead>
                  <tbody>
                    {dd.vif.map((v: any) => (
                      <tr key={v.regressor}>
                        <td>{v.regressor}</td>
                        <td className="n">{v.vif.toFixed(2)}</td>
                        <td>{v.above_threshold
                          ? <Badge kind="allocated">above threshold</Badge>
                          : <Badge kind="real">clear</Badge>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}</Async>
        </Card>
      </div>
    </div>
  )
}
