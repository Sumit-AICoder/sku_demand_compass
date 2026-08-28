import React, { useState } from 'react'
import { api, fmt } from '../lib/api'
import { useStore } from '../lib/store'
import { Card, Async, useAsync, Bar } from './common'
import IndiaMap, { MapNode } from './IndiaMap'
import Narrative from './Narrative'

const ACTION_COLOR: Record<string, string> = {
  'Convert now': 'var(--good)', 'Build access': 'var(--c1)',
  'Defend': 'var(--c4)', 'Monitor': 'var(--text-3)',
}

/**
 * Map-first exploration: India → state → district → block → village.
 *
 * The map is the navigation, not a decoration beside it — clicking a shape drills, and
 * the table beside it always lists exactly what the map is showing, so the two can
 * never disagree.
 */
export default function Explore() {
  const { sku, category, month, setSelectedVillage, selectedVillage } = useStore()
  const [path, setPath] = useState<MapNode[]>([])

  const level = path.length === 0 ? 'state'
    : path.length === 1 ? 'district'
    : path.length === 2 ? 'block' : 'village'
  const parent = path.length ? path[path.length - 1].id : undefined

  const rows = useAsync(
    () => api.geo(level as any, { parent, sku, category, month }),
    [level, parent, sku, category, month])

  const drill = (n: MapNode) => setPath(p => [...p, n])
  const up = (i: number) => setPath(p => (i < 0 ? [] : p.slice(0, i + 1)))

  const narrLevel = path.length === 1 ? 'state' : path.length === 2 ? 'district'
    : path.length === 3 ? 'block' : null

  return (
    <div className="grid" style={{ gap: 14 }}>
      {narrLevel && narrLevel !== 'state' && (
        <Narrative view="geography"
                   params={{ level: narrLevel, id: parent, name: path[path.length - 1].name }} />
      )}

      <div className="split">
        <Card title="Map" note={sku ?? category ?? 'all products'}>
          <IndiaMap path={path} onDrill={drill} onUp={up}
                    sku={sku} category={category} month={month}
                    onSelectVillage={setSelectedVillage} height={540} />
        </Card>

        <Card title={`${level.charAt(0).toUpperCase()}${level.slice(1)}s`} tight
              note={`${rows.data?.items.length ?? 0} shown · click to drill`}>
          <div className="tbl-wrap" style={{ maxHeight: 560 }}>
            <Async state={rows}>{(d: any) => {
              const max = Math.max(...d.items.map((i: any) => i.units), 1)
              return (
                <table>
                  <thead><tr>
                    <th>Name</th><th className="n">Units/yr</th>
                    <th className="n">Unserved</th><th>Best product</th><th style={{ width: 80 }} />
                  </tr></thead>
                  <tbody>
                    {d.items.slice(0, 500).map((i: any) => (
                      <tr key={i.id} className="clickable"
                          onClick={() => {
                            if (level === 'village') setSelectedVillage(i.id)
                            else drill({ level: level as any, id: i.id, name: i.name })
                          }}>
                        <td>{i.name}
                          {i.archetype &&
                            <div className="muted" style={{ fontSize: 10 }}>{i.archetype}</div>}
                        </td>
                        <td className="n">{fmt.units(i.units)}</td>
                        <td className="n muted">{fmt.units(i.headroom)}</td>
                        <td className="muted mono">{i.top_sku ?? '—'}</td>
                        <td><Bar value={i.units} max={max} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            }}</Async>
          </div>
        </Card>
      </div>

      {selectedVillage && <VillagePanel villageId={selectedVillage} />}
    </div>
  )
}

function VillagePanel({ villageId }: { villageId: string }) {
  const d = useAsync<any>(
    () => fetch(`/api/village/${villageId}/insight`).then(r => r.json()), [villageId])
  return (
    <Async state={d}>{(x: any) => {
      const v = x.insight
      return (
        <Card title={`${v.village} — ${v.district}, ${v.state}`} note={v.archetype}>
          <div className="village-head">
            <span className="pill lg" style={{ color: ACTION_COLOR[v.action_segment],
                                               borderColor: ACTION_COLOR[v.action_segment] }}>
              {v.action_segment}
            </span>
            <span className="muted">{v.action_rationale}</span>
          </div>
          <p className="headline">{v.headline}</p>
          <div className="grid g3" style={{ marginTop: 12 }}>
            <div>
              <h4 className="sub">The farm here</h4>
              <Row k="Tractors" v={Math.round(v.tractors)} />
              <Row k="Average farm size" v={`${v.avg_holding_ha.toFixed(1)} ha`} />
              <Row k="Main crop" v={v.dominant_crop} />
              <Row k="Irrigated" v={`${Math.round(v.irrigation_ratio * 100)}%`} />
              <Row k="Soil" v={v.soil_texture} />
            </div>
            <div>
              <h4 className="sub">The opportunity</h4>
              <Row k="Opportunity score" v={`${Math.round(v.opportunity_score)} / 100`} />
              <Row k="Rank in district" v={`#${v.rank_in_district} of ${v.villages_in_district}`} />
              <Row k="Demand" v={`${fmt.units(v.potential_units_yr)} units/yr`} />
              <Row k="Unserved" v={fmt.units(v.headroom)} />
              <Row k="Nearest dealer" v={`${v.dealer_distance_km.toFixed(1)} km`} />
            </div>
            <div>
              <h4 className="sub">Best products here</h4>
              {x.top_skus.slice(0, 5).map((s: any) => (
                <Row key={s.sku_id} k={s.name} v={`${fmt.units(s.units)} u`} />
              ))}
            </div>
          </div>
        </Card>
      )
    }}</Async>
  )
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="kv"><span className="muted">{k}</span><span>{v ?? '—'}</span></div>
}
