import React, { useMemo } from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync } from './common'
import SkuImage from './SkuImage'

/**
 * The archetype's own SKU basket, merged with its competitive contests. One fetch, one
 * merge, shared by the table below and by any narrative view of the same data (see
 * ActPlaybook's product recommendations) so nothing re-fetches or re-merges twice.
 */
export function useArchetypeBasket(archetypeId?: string) {
  const rows = useAsync(() => api.archetypeSkus(archetypeId!, 8), [archetypeId], !!archetypeId)
  // 500, not a small round number: a per-archetype-total cap here would silently drop a
  // SKU's smaller rival contests before we ever filter down to the SKUs actually shown
  // (see the backend route's own note) -- fetch everything, filter client-side instead.
  const rivals = useAsync(() => api.archetypeRivalsBySku(archetypeId!, 500), [archetypeId], !!archetypeId)
  const data = useMemo(() => {
    if (!rows.data) return undefined
    const bySku: Record<string, any[]> = {}
    for (const r of rivals.data ?? []) (bySku[r.sku_id] ??= []).push(r)
    return rows.data.map((x: any) => ({
      ...x,
      rivals: (bySku[x.sku_id] ?? []).sort((a, b) => (b.winnable + b.at_risk) - (a.winnable + a.at_risk)),
    }))
  }, [rows.data, rivals.data])
  return { data, err: rows.err ?? rivals.err, loading: rows.loading || rivals.loading }
}

/**
 * Products for one archetype — the SKU basket it over-indexes on versus the national mix,
 * with each SKU's real competitive contests. Shared by Review's Archetype details and
 * Act's Summary/Playbook, since all three are per-archetype screens that otherwise show
 * zero product-level detail.
 */
export default function ArchetypeSkus({ archetypeId }: { archetypeId?: string }) {
  const rows = useArchetypeBasket(archetypeId)

  return (
    <Card title="Products for this archetype"
          note="ranked by weighted demand · index is share of this archetype's basket vs the national mix">
      {!archetypeId && <p className="dim" style={{ padding: 14 }}>Select an archetype above.</p>}
      {archetypeId && (
        <Async state={rows} empty="no SKU data for this archetype">{(r: any[]) => {
          const maxU = Math.max(1, ...r.map(x => x.units || 0))
          const cannibal = r.filter(x => x.cannibal_pct != null).sort((a, b) => b.cannibal_pct - a.cannibal_pct)[0]
          return (
            <>
            <table>
              <thead><tr>
                <th>SKU</th><th className="dim">Category</th>
                <th style={{ textAlign: 'right' }}>Demand/yr</th>
                <th style={{ textAlign: 'right' }}>Value</th>
                <th style={{ textAlign: 'right' }}>Index vs national</th>
                <th style={{ textAlign: 'right' }}>Subsidy</th>
                <th>Closest rivals
                  <span className="dim" style={{ fontWeight: 400 }}> · winnable / at risk</span>
                </th>
              </tr></thead>
              <tbody>
                {r.map(x => {
                  const push = x.units >= maxU * 0.25 && (x.subsidy_pct ?? 0) >= 40
                  return (
                    <tr key={x.sku_id}>
                      <td>
                        <div className="sku-cell">
                          <SkuImage skuId={x.sku_id} category={x.category} size={36} />
                          <span>{x.name}</span>
                          {push && <span className="pill pill-real">push now</span>}
                        </div>
                      </td>
                      <td className="dim">{x.category_label}</td>
                      <td style={{ textAlign: 'right' }}>{fmt.units(x.units)}</td>
                      <td style={{ textAlign: 'right' }}>{fmt.cr(x.value)}</td>
                      <td style={{ textAlign: 'right' }}>
                        {x.index_vs_national == null ? '—' : `${x.index_vs_national.toFixed(1)}×`}
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        {x.subsidy_pct != null
                          ? <>{x.subsidy_pct}%{' '}
                              <span className={'pill ' + (x.subsidy_provenance === 'real' ? 'pill-secondary' : 'pill-client')}>
                                {x.subsidy_provenance === 'real' ? 'real' : 'proxy'}</span></>
                          : <span className="dim">—</span>}
                      </td>
                      <td style={{ fontSize: 11 }}>
                        {x.rivals.length
                          ? x.rivals.map((rv: any) => (
                              <div key={rv.rival} className="dim">
                                {rv.rival} — {fmt.count(rv.winnable)} / {fmt.count(rv.at_risk)}
                              </div>
                            ))
                          : <span className="dim">—</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            {cannibal && (
              <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
                ~{cannibal.cannibal_pct}% of {cannibal.name}'s demand nationally overlaps a
                sister product — the <b>national</b> rate, not recomputed for this archetype.
              </p>
            )}
            </>
          )
        }}</Async>
      )}
    </Card>
  )
}
