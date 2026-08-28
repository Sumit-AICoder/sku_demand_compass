import React from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi } from './common'

/**
 * DEFINE · Archetypes — the client's three-factor cross-product:
 *   agro-climatic NARP sub-zone  ×  TIV tier  ×  HP belt.
 * The agro-climatic axis is the published NARP/AESR sub-zone (real geography); TIV and
 * HP belt are modelled (ITL pending).
 */
export default function Archetypes() {
  const a = useAsync(() => api.archetypes(), [])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Archetype = <b>NARP agro-climatic sub-zone</b>{' '}
        <span className="pill pill-secondary">real</span> × <b>TIV tier</b> × <b>HP belt</b>{' '}
        <span className="pill pill-client">modelled · ITL pending</span>. Sub-zones follow the
        ICAR AESR scheme (length-of-growing-period in days).
      </div>

      <Async state={a}>{(d: any) => (
        <>
          <div className="grid g3">
            <Kpi k="Archetypes" v={d.totals.n_archetypes}
                 s={`${d.subzones.length} sub-zones × TIV × ${d.hp_belts.length} HP belts`} />
            <Kpi k="Micro-markets" v={fmt.units(d.totals.n_micromarkets)} s="≈4-5 villages each" />
            <Kpi k="TIV in play" v={fmt.units(d.totals.tiv)} s="tractors across all archetypes" />
            <Kpi k="Avg Sonalika share" v={`${(d.totals.avg_sonalika_share * 100).toFixed(1)}%`} s="TIV-weighted" />
          </div>

          <div className="split">
            <Card title="NARP agro-climatic sub-zones" note="the agro-climatic axis (real)">
              <table>
                <thead><tr><th>Zone</th><th>Sub-zone</th><th>LGP</th><th style={{ textAlign: 'right' }}>Micro-mkts</th><th style={{ textAlign: 'right' }}>TIV</th></tr></thead>
                <tbody>
                  {d.subzones.map((z: any) => (
                    <tr key={z.subzone_id}>
                      <td><b>{z.subzone_id}</b></td>
                      <td>{z.subzone}<div className="dim" style={{ fontSize: 11 }}>{z.states}</div></td>
                      <td className="dim">{z.lgp}d</td>
                      <td style={{ textAlign: 'right' }}>{fmt.units(z.micromarkets)}</td>
                      <td style={{ textAlign: 'right' }}>{fmt.units(z.tiv)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            <Card title="HP belts" note="the third axis (mean tractor HP)">
              <table>
                <thead><tr><th>HP belt</th><th style={{ textAlign: 'right' }}>Archetypes</th><th style={{ textAlign: 'right' }}>Micro-mkts</th><th style={{ textAlign: 'right' }}>TIV</th></tr></thead>
                <tbody>
                  {d.hp_belts.map((b: any) => (
                    <tr key={b.hp_belt}>
                      <td><b>{b.hp_belt}</b></td>
                      <td style={{ textAlign: 'right' }}>{b.archetypes}</td>
                      <td style={{ textAlign: 'right' }}>{fmt.units(b.micromarkets)}</td>
                      <td style={{ textAlign: 'right' }}>{fmt.units(b.tiv)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </div>

          <Card title={`${d.archetypes.length} archetypes`}
                note="named by dominant crop + TIV, divided geographically by NARP sub-zone, ranked by demand">
            <table>
              <thead><tr>
                <th>Archetype</th><th>HP belt</th><th>NARP sub-zone</th>
                <th style={{ textAlign: 'right' }}>Micro-mkts</th>
                <th style={{ textAlign: 'right' }}>Villages</th>
                <th style={{ textAlign: 'right' }}>TIV</th>
                <th style={{ textAlign: 'right' }}>Sonalika %</th>
                <th style={{ textAlign: 'right' }}>Demand /yr</th>
                <th>States</th>
              </tr></thead>
              <tbody>
                {d.archetypes.map((r: any) => (
                  <tr key={r.archetype_id} className={r.is_custom ? 'row-push' : ''}>
                    <td><b>{r.base_name}</b>{r.is_custom && <span className="pill pill-real">custom</span>}</td>
                    <td><span className="pill pill-real" style={{ marginLeft: 0 }}>{r.hp_belt}</span></td>
                    <td className="dim" style={{ fontSize: 12 }}>
                      {r.subzone_id ? <>{r.subzone_id} {r.subzone} · LGP {r.lgp}d</> : '—'}</td>
                    <td style={{ textAlign: 'right' }}>{fmt.units(r.n_micromarkets)}</td>
                    <td style={{ textAlign: 'right' }} className="dim">{fmt.units(r.n_villages)}</td>
                    <td style={{ textAlign: 'right' }}>{fmt.units(r.tiv)}</td>
                    <td style={{ textAlign: 'right' }}>{(r.avg_sonalika_share * 100).toFixed(1)}%</td>
                    <td style={{ textAlign: 'right' }}>{fmt.units(r.potential_units_yr)}</td>
                    <td className="dim" style={{ fontSize: 12 }}>{r.states}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}</Async>
    </div>
  )
}
