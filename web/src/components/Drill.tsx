import React from 'react'
import { RadarChart, PolarGrid, PolarAngleAxis, Radar, ResponsiveContainer, Tooltip,
         BarChart, Bar as RBar, XAxis, YAxis, Cell, ReferenceLine } from 'recharts'
import { api, fmt } from '../lib/api'
import { useStore, childLevel, currentParent } from '../lib/store'
import { Card, Async, useAsync, PointMap, Badge, Bar } from './common'
import Narrative from './Narrative'

const FACTOR_SHORT: Record<string, string> = {
  F1: 'Farm econ', F2: 'Land', F3: 'Tractors', F4: 'Mech', F5: 'Crops',
  F6: 'Policy', F7: 'Water', F8: 'Hiring', F9: 'Tech', F10: 'Dealers',
}

export default function Drill() {
  const { crumbs, push, popTo, reset, sku, category, month,
          selectedVillage, setSelectedVillage } = useStore()
  const level = childLevel(crumbs)
  const parent = currentParent(crumbs)

  const geo = useAsync(() => api.geo(level, { parent, sku, category, month }),
                       [level, parent, sku, category, month])
  const items = geo.data?.items ?? []

  return (
    <div className="grid" style={{ gap: 14 }}>
      <div className="crumbs">
        <button onClick={reset}>All India (pilot)</button>
        {crumbs.map((c, i) => (
          <React.Fragment key={c.id}>
            <span className="sep">›</span>
            {i === crumbs.length - 1
              ? <span className="cur">{c.name}</span>
              : <button onClick={() => popTo(i)}>{c.name}</button>}
          </React.Fragment>
        ))}
        <span className="muted" style={{ marginLeft: 10, fontSize: 12 }}>
          showing {level}s{sku ? ` · ${sku}` : category ? ` · ${category}` : ''}
        </span>
      </div>

      {crumbs.length > 0 && crumbs[crumbs.length - 1].level !== 'state' && (
        <Narrative view="geography"
                   params={{ level: crumbs[crumbs.length - 1].level,
                             id: crumbs[crumbs.length - 1].id,
                             name: crumbs[crumbs.length - 1].name }} />
      )}

      <div className="split">
        <Card title={`${level.charAt(0).toUpperCase() + level.slice(1)}s`}
              note={`${items.length} shown · click to drill`}>
          <PointMap items={items} height={400} selected={selectedVillage}
            onSelect={(id) => {
              const it = items.find(i => i.id === id); if (!it) return
              if (level === 'village') setSelectedVillage(id)
              else push({ level, id: it.id, name: it.name })
            }} />
        </Card>

        <Card title="Ranked" tight>
          <div className="tbl-wrap" style={{ maxHeight: 430 }}>
            <Async state={geo}>{(d: any) => {
              const max = Math.max(...d.items.map((i: any) => i.units), 1)
              return <table>
                <thead><tr>
                  <th>Name</th><th className="n">Units/yr</th><th className="n">Headroom</th>
                  <th>Top SKU</th><th style={{ width: 80 }} />
                </tr></thead>
                <tbody>
                  {d.items.slice(0, 400).map((i: any) => (
                    <tr key={i.id}
                        className={`clickable${selectedVillage === i.id ? ' sel' : ''}`}
                        onClick={() => {
                          if (level === 'village') setSelectedVillage(i.id)
                          else push({ level, id: i.id, name: i.name })
                        }}>
                      <td>{i.name}{i.archetype && <div className="muted" style={{ fontSize: 10.5 }}>{i.archetype}</div>}</td>
                      <td className="n">{fmt.units(i.units)}</td>
                      <td className="n muted">{fmt.units(i.headroom)}</td>
                      <td className="muted mono">{i.top_sku ?? '—'}</td>
                      <td><Bar value={i.units} max={max} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            }}</Async>
          </div>
        </Card>
      </div>

      {selectedVillage && <VillagePanel villageId={selectedVillage} sku={sku} />}
    </div>
  )
}

function VillagePanel({ villageId, sku }: { villageId: string; sku?: string }) {
  const v = useAsync(() => api.village(villageId), [villageId])
  const chosenSku = sku ?? v.data?.top_skus?.[0]?.sku_id
  const drv = useAsync(() => api.drivers(villageId, chosenSku!), [villageId, chosenSku], !!chosenSku)

  return (
    <Async state={v}>{(d: any) => {
      const f = d.factors ?? {}
      const feat = d.features ?? {}
      const radar = Object.keys(FACTOR_SHORT).map(k => ({
        factor: FACTOR_SHORT[k], national: f[k] ?? 0, inState: f[`${k}_state`] ?? 0,
      }))
      return (
        <div className="grid g3">
          <Card title={d.village.village} note={d.village.archetype}>
            <div className="grid" style={{ gap: 5, fontSize: 12.5 }}>
              <Row k="District" v={`${d.village.district}, ${d.village.state}`} />
              <Row k="Net sown" v={`${fmt.num(feat.net_sown_ha, 0)} ha`} />
              <Row k="Avg holding" v={`${fmt.num(feat.avg_holding_ha)} ha`} />
              <Row k="Tractors" v={fmt.num(feat.tractors, 0)} />
              <Row k="Attach rate" v={`${fmt.num(feat.attach_rate)} vs peers ${fmt.num(feat.peer_attach_rate)}`} />
              <Row k="Farm power" v={`${fmt.num(feat.farm_power_kw_ha)} kW/ha`} />
              <Row k="Irrigation" v={fmt.pct((feat.irrigation_ratio ?? 0) * 100, 0)} />
              <Row k="Dominant crop" v={feat.dominant_crop} />
              <Row k="Soil" v={`${feat.soil_texture} · workability ${fmt.num(feat.workability)}`} />
              <Row k="Nearest dealer" v={`${fmt.num(feat.dealer_distance_km, 1)} km`} />
              <Row k="Potential" v={`${fmt.units(d.village.potential_units_yr)} units/yr`} />
            </div>
          </Card>

          <Card title="Factor profile" note="national vs within-state percentile">
            <ResponsiveContainer width="100%" height={250}>
              <RadarChart data={radar} outerRadius="72%">
                <PolarGrid stroke="var(--border)" />
                <PolarAngleAxis dataKey="factor" tick={{ fontSize: 10 }} />
                <Radar name="National" dataKey="national" stroke="var(--c1)"
                       fill="var(--c1)" fillOpacity={0.28} />
                <Radar name="In state" dataKey="inState" stroke="var(--c3)"
                       fill="var(--c3)" fillOpacity={0.12} />
                <Tooltip formatter={(x: any) => Number(x).toFixed(0)} />
              </RadarChart>
            </ResponsiveContainer>
          </Card>

          <Card title="Top SKUs here" tight>
            <div className="tbl-wrap" style={{ maxHeight: 280 }}>
              <table>
                <thead><tr><th>SKU</th><th className="n">Units</th><th className="n">Pen.</th></tr></thead>
                <tbody>
                  {d.top_skus.map((s: any) => (
                    <tr key={s.sku_id} className="clickable"
                        onClick={() => useStore.getState().setSku(s.sku_id)}>
                      <td>{s.name}</td>
                      <td className="n">{fmt.units(s.units)}</td>
                      <td className="n muted">{fmt.pct(s.penetration * 100, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <div style={{ gridColumn: '1 / -1' }}>
            <Card title={`Why this score — ${chosenSku ?? ''}`}
                  note="contribution = factor index × weight; badge shows where the weight came from">
              <Async state={drv}>{(dd: any) => (
                <div className="split" style={{ gap: 14 }}>
                  <ResponsiveContainer width="100%" height={260}>
                    <BarChart data={dd.contributions} layout="vertical" margin={{ left: 12, right: 16 }}>
                      <XAxis type="number" tick={{ fontSize: 10 }} />
                      <YAxis type="category" dataKey="label" width={168} tick={{ fontSize: 10 }} />
                      <Tooltip formatter={(x: any) => Number(x).toFixed(4)} />
                      <ReferenceLine x={0} stroke="var(--border-strong)" />
                      <RBar dataKey="contribution" radius={[0, 3, 3, 0]}>
                        {dd.contributions.map((c: any, i: number) => (
                          <Cell key={i} fill={c.origin === 'ucm' ? 'var(--ucm)' : 'var(--prior)'} />
                        ))}
                      </RBar>
                    </BarChart>
                  </ResponsiveContainer>
                  <div className="tbl-wrap" style={{ maxHeight: 260 }}>
                    <table>
                      <thead><tr><th>Factor</th><th className="n">Index</th><th className="n">Weight</th>
                                 <th>Origin</th><th>Evidence</th></tr></thead>
                      <tbody>
                        {dd.contributions.map((c: any) => (
                          <tr key={c.factor} title={c.excel_impact}>
                            <td>{c.label}</td>
                            <td className="n">{c.index.toFixed(0)}</td>
                            <td className="n">{c.weight.toFixed(3)}</td>
                            <td><Badge kind={c.origin}>{c.origin === 'ucm' ? 'UCM' : 'prior'}</Badge></td>
                            <td><Badge kind={c.evidence}>{c.evidence}</Badge></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}</Async>
            </Card>
          </div>
        </div>
      )
    }}</Async>
  )
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
    <span className="muted">{k}</span><span style={{ textAlign: 'right' }}>{v ?? '—'}</span>
  </div>
}
