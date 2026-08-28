import React from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi, Info } from './common'

/**
 * DEFINE · Archetypes — the cross-product the whole tool segments on:
 *   agro-climatic ZONE  ×  TIV tier  ×  HP belt.
 *
 * The zone is the published NARP/AESR geography (real); TIV tier and HP belt are modelled
 * until ITL supplies fleet data. Micro-markets are grouped inside the finer sub-zone, so
 * the local unit stays local while the archetype sits where a national plan is made.
 *
 * The categories are not hardcoded here: they come from the taxonomy the Configure tab
 * edits, which is why adding a tier or combining two zones changes this table in a second.
 */
export default function Archetypes() {
  const a = useAsync(() => api.archetypes(), [])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Archetype = <b>agro-climatic zone</b>{' '}
        <span className="pill pill-secondary">real</span> × <b>TIV tier</b> × <b>HP belt</b>{' '}
        <span className="pill pill-client">modelled · ITL pending</span>. Zones follow the
        ICAR AESR scheme; the tiers, belts and zones themselves are editable on{' '}
        <b>Configure</b>.
      </div>

      <Async state={a}>{(d: any) => (
        <>
          <div className="grid g4">
            <Kpi k={<span>Archetypes<Info wide text={<>
                  An archetype is <b>zone × TIV tier × HP belt</b>. Micro-markets are grouped
                  inside the finer sub-zone; the archetype sits at the coarser zone, because
                  that is the level a national plan is made at. The categories themselves are
                  editable on the Configure tab.</>} /></span>}
                 v={d.totals.n_archetypes}
                 s={`${d.zones ?? ''}${d.subzones.length} sub-zones roll up into the zones in force`} />
            <Kpi k="Micro-markets" v={fmt.count(d.totals.n_micromarkets)} s="≈4-5 villages each" />
            <Kpi k="Tractors (TIV)" v={fmt.units(d.totals.tiv)} s="fleet across all archetypes" />
            <Kpi k="Avg Sonalika share" v={`${(d.totals.avg_sonalika_share * 100).toFixed(1)}%`}
                 s="TIV-weighted" />
          </div>

          <Card title={<>{d.archetypes.length} archetypes
                <Info wide text={<>
                  One row per archetype, ranked by fleet. The name carries the crop that is
                  <b> distinctive</b> for that zone; “Most-grown” is the crop actually grown in
                  most of its micro-markets — they differ, and both are worth seeing.
                  “Top branded rival” deliberately excludes the unbranded “Local” segment,
                  which leads every archetype and would read the same on every row.
                  Demand lives on the Plan stage, which is where the choice between
                  archetypes is made; Define describes them.</>} /></>}
                note="zone × TIV tier × HP belt · ranked by fleet">
            <table>
              <thead><tr>
                <th>Archetype</th><th>Zone</th><th>HP belt</th><th>TIV tier</th>
                <th>Most-grown</th>
                <th style={{ textAlign: 'right' }}>Micro-mkts</th>
                <th style={{ textAlign: 'right' }}>Villages</th>
                <th style={{ textAlign: 'right' }}>TIV</th>
                <th style={{ textAlign: 'right' }}>Sonalika %</th>
                <th>Top branded rival</th>
                <th>States</th>
              </tr></thead>
              <tbody>
                {d.archetypes.map((r: any) => (
                  <tr key={r.archetype_id}>
                    <td><b>{r.base_name}</b>
                      <div className="dim" style={{ fontSize: 11 }}>{r.archetype_id}</div></td>
                    <td className="dim" style={{ fontSize: 12 }}>
                      {r.zone} {r.zone_name}
                      <div className="dim" style={{ fontSize: 11 }}>sub-zones {r.subzones || '—'}</div></td>
                    <td><span className="pill pill-real" style={{ marginLeft: 0 }}>{r.hp_belt}</span></td>
                    <td>{r.tiv_tier}</td>
                    <td className="dim">{r.dominant_crop || '—'}</td>
                    <td style={{ textAlign: 'right' }}>{fmt.count(r.n_micromarkets)}</td>
                    <td style={{ textAlign: 'right' }} className="dim">{fmt.count(r.n_villages)}</td>
                    <td style={{ textAlign: 'right' }}>{fmt.units(r.tiv)}</td>
                    <td style={{ textAlign: 'right' }}>{(r.avg_sonalika_share * 100).toFixed(1)}%</td>
                    <td>{r.rival ?? '—'}
                      {r.rival_share != null &&
                        <span className="dim"> {(r.rival_share * 100).toFixed(0)}%</span>}</td>
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
