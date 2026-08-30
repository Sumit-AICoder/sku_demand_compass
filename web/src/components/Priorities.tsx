import React, { useState, useMemo } from 'react'
import { api, fmt } from '../lib/api'
import { useStore } from '../lib/store'
import { Card, Async, useAsync, Bar } from './common'
import { ArchetypePicker } from './ActPicker'

/**
 * PLAN · Prioritise & subsidy.
 *
 * Focus products rank by demand potential, shown against the real subsidy lever
 * (Punjab/Maharashtra real; MP a national-SMAM proxy). District priorities are anchored
 * to REAL cropland (DES) so under-penetration shows relative to actual farmland.
 */
const STATES = ['Punjab', 'Maharashtra', 'Madhya Pradesh']

export default function Priorities() {
  const { productLine } = useStore()
  const [scope, setScope] = useState<'state' | 'archetype'>('state')
  const [state, setState] = useState('Punjab')
  const buckets = useAsync(() => api.planBuckets({ product: productLine }),
                           [productLine, scope], scope === 'archetype')
  const archRows = buckets.data?.archetypes ?? []
  const [archId, setArchId] = useState<string>()

  const pri = useAsync(() => api.planPriorities(state, productLine),
                       [state, productLine, scope], scope === 'state')
  const archSkus = useAsync(() => api.archetypeSkus(archId!, 18), [archId, scope],
                            scope === 'archetype' && !!archId)
  const dist = useAsync(() => api.planDistricts(), [])
  const archMm = useAsync(() => api.planBucketMicromarkets(archId!, 400), [archId, scope],
                          scope === 'archetype' && !!archId)
  const archDistricts = useMemo(
    () => new Set((archMm.data?.micromarkets ?? []).map((m: any) => m.district)),
    [archMm.data])

  const rows: any = scope === 'archetype'
    ? { data: archSkus.data ? { skus: archSkus.data } : undefined, err: archSkus.err, loading: archSkus.loading }
    : pri

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Focus products ranked by demand, shown with the <b>real subsidy lever</b>{' '}
        <span className="pill pill-secondary">EY · real</span> — Punjab &amp; Maharashtra are
        state-specific; Madhya Pradesh uses the national SMAM 40% rate{' '}
        <span className="pill pill-client">SMAM proxy</span>. District priorities are anchored
        to real DES cropland <span className="pill pill-secondary">DES · real</span>.
        {productLine === 'tractors' && ' Tractor demand is modelled pending ITL.'}
      </div>

      <div className="split">
        <Card title="Focus products" note="demand potential × real subsidy lever">
          <div className="filters" style={{ margin: '0 0 10px', marginLeft: 0, gap: 10 }}>
            <select value={scope} onChange={e => setScope(e.target.value as any)}>
              <option value="state">By state</option>
              <option value="archetype">By archetype</option>
            </select>
            {scope === 'state' ? (
              <>
                <label className="dim" style={{ fontSize: 12, alignSelf: 'center' }}>Subsidy state:</label>
                <select value={state} onChange={e => setState(e.target.value)}>
                  {STATES.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </>
            ) : (
              <Async state={buckets}>{() => (
                <ArchetypePicker rows={archRows} sel={archId} setSel={setArchId} />
              )}</Async>
            )}
          </div>
          <Async state={rows} empty={scope === 'archetype' && !archId
            ? 'Select an archetype above.' : undefined}>{(d: any) => {
            const skuRows = d.skus as any[]
            const maxU = Math.max(1, ...skuRows.map(r => r.units || 0))
            return (
              <table>
                <thead><tr>
                  <th>Product</th>
                  <th style={{ textAlign: 'right' }}>Demand /yr</th>
                  <th style={{ width: 90 }}></th>
                  <th style={{ textAlign: 'right' }}>Subsidy</th>
                </tr></thead>
                <tbody>
                  {skuRows.slice(0, 18).map(r => {
                    const push = (r.units >= maxU * 0.25) && (r.subsidy_pct ?? 0) >= 40
                    return (
                      <tr key={r.sku_id} className={push ? 'row-push' : ''}>
                        <td>{r.name}{push && <span className="pill pill-real">push now</span>}</td>
                        <td style={{ textAlign: 'right' }}>{fmt.units(r.units)}</td>
                        <td><Bar value={r.units} max={maxU} color="var(--c1)" /></td>
                        <td style={{ textAlign: 'right' }}>
                          {r.subsidy_pct != null
                            ? <>{r.subsidy_pct}%{' '}
                                <span className={'pill ' + (r.subsidy_provenance === 'real' ? 'pill-secondary' : 'pill-client')}>
                                  {r.subsidy_provenance === 'real' ? 'real' : 'proxy'}</span></>
                            : <span className="dim">—</span>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )
          }}</Async>
        </Card>

        <Card title="District priorities vs real cropland"
              note={scope === 'archetype' && archId
                ? "this archetype's own districts · DES cropland" : "DES cropped area · demand intensity per '000 ha"}>
          <Async state={dist}>{(d: any) => {
            let rows = (d.districts as any[]).filter(r => r.crop_area_lha)
            if (scope === 'archetype' && archId) rows = rows.filter(r => archDistricts.has(r.district))
            return (
              <table>
                <thead><tr>
                  <th>District</th><th>State</th>
                  <th style={{ textAlign: 'right' }}>Demand</th>
                  <th style={{ textAlign: 'right' }}>Cropland</th>
                  <th style={{ textAlign: 'right' }}>/’000 ha</th>
                </tr></thead>
                <tbody>
                  {rows.slice(0, 16).map(r => (
                    <tr key={r.district_id}>
                      <td>{r.district}</td>
                      <td className="dim">{r.state}</td>
                      <td style={{ textAlign: 'right' }}>{fmt.units(r.units)}</td>
                      <td style={{ textAlign: 'right' }} className="dim">
                        {r.crop_area_lha?.toFixed(1)} Lha</td>
                      <td style={{ textAlign: 'right' }}>{r.units_per_kha?.toFixed(1)}</td>
                    </tr>
                  ))}
                  {scope === 'archetype' && archId && !rows.length &&
                    <tr><td colSpan={5} className="dim">no districts with cropland data for this archetype</td></tr>}
                </tbody>
              </table>
            )
          }}</Async>
        </Card>
      </div>
    </div>
  )
}
