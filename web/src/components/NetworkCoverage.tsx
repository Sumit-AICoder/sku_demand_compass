import React, { useState } from 'react'
import { api, fmt } from '../lib/api'
import { useStore } from '../lib/store'
import { Card, Kpi, Async, useAsync, Bar } from './common'

/**
 * REVIEW · Network coverage — Sonalika vs rival OEMs across the three states, mapped to
 * archetypes. Sales coverage is real (dealer locator); service coverage is dummy (ITL
 * service master pending). Low coverage on a "sales issue" archetype is the actionable gap.
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

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Sonalika vs rival OEMs, mapped to archetypes.{' '}
        {type === 'sales'
          ? <>Sales coverage is <span className="pill pill-real">real · dealer locator</span></>
          : <>Service coverage is <span className="pill pill-client">dummy · ITL service master pending</span></>}
        {isTractor && <> · Sonalika’s own tractor network is <span className="pill pill-client">pending ITL</span> (rival tractor network is real).</>}
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
              <Kpi k="Sonalika dealers" v={fmt.units(d.own_dealers)}
                   s={isTractor ? 'tractor network pending ITL' : `own ${type} network`} />
              <Kpi k="Competitor dealers" v={fmt.units(d.competitor_dealers)} s={`${d.oems.length}+ rival OEMs`} />
              <Kpi k={`Avg ${type} coverage`} v={`${(avgCov * 100).toFixed(0)}%`} s="across archetypes" />
              <Kpi k="Coverage gaps" v={gaps} s="sales-issue archetypes < 50% covered" />
            </div>

            <div className="split">
              <Card title="Coverage by archetype" note="worst-covered first — the actionable gaps">
                <div style={{ maxHeight: 460, overflow: 'auto' }}>
                  <table>
                    <thead><tr>
                      <th>Archetype</th><th>Diagnosis</th>
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
                          <td>
                            <Bar value={r.coverage} max={1} color={r.coverage < 0.4 ? 'var(--bad)' : r.coverage < 0.6 ? 'var(--warn)' : 'var(--good)'} />
                            <span className="dim" style={{ fontSize: 11 }}> {(r.coverage * 100).toFixed(0)}%</span>
                          </td>
                          {type === 'sales' && <td style={{ textAlign: 'right' }} className="dim">{(r.pct_covered * 100).toFixed(0)}%</td>}
                          <td style={{ textAlign: 'right' }}>{fmt.units(r.n_micromarkets)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>

              <Card title={`Rival ${productLine} OEMs`} note={`${type === 'sales' ? 'real' : 'dummy'} network reach`}>
                <table>
                  <thead><tr><th>OEM</th><th style={{ textAlign: 'right' }}>Dealers</th><th style={{ textAlign: 'right' }}>Districts</th></tr></thead>
                  <tbody>
                    {d.oems.map((o: any) => (
                      <tr key={o.oem}>
                        <td>{o.oem}</td>
                        <td style={{ textAlign: 'right' }}>{fmt.units(o.dealers)}</td>
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
