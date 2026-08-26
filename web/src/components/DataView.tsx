import React from 'react'
import { api } from '../lib/api'
import { Card, Async, useAsync, Badge } from './common'

export default function DataView() {
  const meta = useAsync(() => api.meta(), [])
  return (
    <div className="grid" style={{ gap: 14 }}>
      <Card title="What is real, what is estimated, what is simulated">
        <p className="note" style={{ maxWidth: 940 }}>
          This dashboard mixes observed data, statistically estimated quantities, and
          calibrated simulation. Mixing them without labelling them is how a tool like this
          loses trust, so every number carries its origin. The short version:
        </p>
        <ul className="note" style={{ maxWidth: 940, lineHeight: 1.75 }}>
          <li><Badge kind="real">real</Badge> — district boundaries and names, district and
            village counts (Census 2011 anchors), the SKU taxonomy and HP bands
            (Excel + Sonalika range).</li>
          <li><Badge kind="allocated">allocated</Badge> — district statistics downscaled to
            villages, and everything derived from the UCM. Most open Indian agri data
            publishes at district level; village figures are district signal apportioned by
            real village-level modifiers.</li>
          <li><Badge kind="simulated">simulated</Badge> — the layers with no public source
            at all: OEM implement sales, dealer network, competitor share, CHC density and
            finance penetration. These are generated from documented parameters in
            <span className="mono"> sim_params.yaml</span>, not invented ad hoc.</li>
        </ul>
      </Card>

      <Async state={meta}>{(m: any) => (
        <>
          <Card title="Model quality">
            <div className="grid g3">
              <div>
                <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>UCM</div>
                <ul className="note" style={{ lineHeight: 1.7 }}>
                  <li>{m.ucm.districts_fitted} districts fitted, {m.ucm.beats_seasonal_naive} beat seasonal-naive</li>
                  <li>median backtest MAPE {m.ucm.median_backtest_mape}% vs naive {m.ucm.median_snaive_mape}%</li>
                  <li>median R²-like {m.ucm.median_r2}</li>
                  <li>{m.ucm.residual_autocorr_ok}/{m.ucm.districts_fitted} with clean residual autocorrelation</li>
                </ul>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>Weights</div>
                <ul className="note" style={{ lineHeight: 1.7 }}>
                  <li><Badge kind="ucm">UCM</Badge> {m.weights.ucm ?? 0} SKU-factor pairs empirical</li>
                  <li><Badge kind="prior">prior</Badge> {m.weights.prior ?? 0} judgmental</li>
                  <li className="muted">Only four factor groups have a time-varying driver the
                    UCM can identify. The six structural ones — land holding, tractor base,
                    cropping pattern, custom hiring, technology, distribution — barely move
                    month to month, so they stay judgmental by necessity.</li>
                </ul>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase' }}>Clustering</div>
                <ul className="note" style={{ lineHeight: 1.7 }}>
                  <li>{m.clustering.k} archetypes</li>
                  <li>bootstrap ARI {m.clustering.bootstrap_ari.toFixed(3)}</li>
                  <li>spatial coherence {m.clustering.spatial_coherence.toFixed(2)}</li>
                </ul>
              </div>
            </div>
          </Card>

          <Card title="Source fetch manifest" tight
                note="what each connector actually did on the last run">
            <div className="tbl-wrap">
              <table>
                <thead><tr>
                  <th>Source</th><th>Mode</th><th>Provenance</th><th className="n">Rows</th>
                  <th className="n">Coverage</th><th>Why not live</th>
                </tr></thead>
                <tbody>
                  {m.sources.map((s: any) => (
                    <tr key={s.source}>
                      <td className="mono">{s.source}</td>
                      <td>{s.mode === 'real'
                        ? <Badge kind="real">live</Badge>
                        : <Badge kind="simulated">synthetic</Badge>}</td>
                      <td><Badge kind={s.provenance}>{s.provenance}</Badge></td>
                      <td className="n">{s.rows?.toLocaleString('en-IN')}</td>
                      <td className="n muted">{s.coverage_pct != null ? `${s.coverage_pct}%` : '—'}</td>
                      <td className="note" style={{ whiteSpace: 'normal', maxWidth: 420 }}>{s.error ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="AI narratives and chat">
            <p className="note" style={{ maxWidth: 940 }}>
              {m.ai?.available
                ? <>Narratives and the Ask-the-data chat are written by{' '}
                    <strong>{m.ai.provider === 'azure'
                      ? `Azure OpenAI ${m.ai.model}` : `Anthropic ${m.ai.model}`}</strong>.{' '}</>
                : <>No AI provider is configured, so narratives use deterministic templates and
                    chat uses a keyword router.{' '}</>}
              In both cases the model is never the source of a number. Deterministic queries
              compute a fact pack from the data; the model only writes prose over it, and for
              chat it may only see data by calling one of eight whitelisted query tools — so
              every answer is backed by a query that actually ran, shown under
              &ldquo;how I got this&rdquo;. No free-form SQL ever reaches the database.
            </p>
          </Card>

          <Card title="The limit worth knowing">
            <p className="note" style={{ maxWidth: 940 }}>
              No public series of implement sales exists, so implement elasticities are
              <strong> derived</strong> from tractor elasticities via HP band and crop fit —
              not directly estimated. The UCM is fitted on tractor registrations, whose
              structure and seasonality are realistic, and the estimator is validated by a
              parameter-recovery test against coefficients known by construction. If
              Sonalika can supply real dealer secondary-sales history, the same machinery
              applies to it directly, and that is the single largest accuracy upgrade
              available to this model.
            </p>
          </Card>
        </>
      )}</Async>
    </div>
  )
}
