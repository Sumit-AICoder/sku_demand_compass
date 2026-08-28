import React, { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { Card, useAsync, Info } from './common'
import { GeoMap, MapPoint } from './GeoMap'

/**
 * The India → state → district drill, and the selection it produces.
 *
 * Three screens need exactly this navigation — Define's profile, Review's market explorer,
 * and the network-coverage map — and "the map should look the same everywhere" is a promise
 * that only holds if there is one map to look at. So the breadcrumb, the layer toggle, the
 * GeoMap and the selection state live here, and each screen supplies what goes beside the
 * map and underneath it.
 *
 * The screen tells it which points to draw and gets back where the user is; it does not have
 * to know that a district file stores its boundary under `outline` or that state shapes are
 * keyed by district_id.
 */
export type DrillLevel = 'india' | 'state' | 'district'
export type Selection = { level: 'district' | 'micromarket'; id: string }
export type DrillState = {
  level: DrillLevel
  state?: string
  district?: { id: string; name: string }
  layer: 'district' | 'micromarket'
  sel?: Selection
  select: (s: Selection | undefined) => void
  setLayer: (l: 'district' | 'micromarket') => void
  goto: (state?: string, district?: { id: string; name: string }) => void
}

export function useDrill(): DrillState {
  const [state, setState] = useState<string>()
  const [district, setDistrict] = useState<{ id: string; name: string }>()
  const [layer, setLayer] = useState<'district' | 'micromarket'>('district')
  const [sel, setSel] = useState<Selection>()
  const level: DrillLevel = district ? 'district' : state ? 'state' : 'india'
  return {
    level, state, district, layer, sel, setLayer,
    select: setSel,
    goto: (s, d) => { setState(s); setDistrict(d); setSel(undefined) },
  }
}

export function MapDrill({
  drill, points, mapNote, mapInfo, legend, height = 480, right, children,
  showLayers = true, layerLabels = ['District', 'Micro-markets'], counter, shapeFill, shapeTitle,
}: {
  drill: DrillState
  points: MapPoint[]
  mapNote?: string
  mapInfo?: React.ReactNode
  legend?: React.ReactNode
  height?: number
  /** The panel beside the map — normally the profile for whatever is selected. */
  right: React.ReactNode
  /** Anything below the split, normally a table that drives the same selection. */
  children?: React.ReactNode
  showLayers?: boolean
  layerLabels?: [string, string] | string[]
  counter?: React.ReactNode
  shapeFill?: (id: string) => string | undefined
  shapeTitle?: (id: string, name: string) => string
}) {
  const d = drill
  const districts = useAsync(() => api.defineDistricts(), [])
  const nDistricts = useMemo(
    () => (districts.data?.districts ?? []).filter((r: any) => !d.state || r.state === d.state).length,
    [districts.data, d.state])

  const onDrill = (id: string, name: string) => {
    if (d.level === 'india') d.goto(name, undefined)
    else if (d.level === 'state') { d.goto(d.state, { id, name }); d.select({ level: 'district', id }) }
    else if (d.district) d.select({ level: 'district', id: d.district.id })
  }

  return (
    <>
      <div className="row row-centre" style={{ gap: 12 }}>
        <div className="switch">
          <button className={!d.state ? 'on' : ''} onClick={() => d.goto(undefined, undefined)}>India</button>
          {d.state && (
            <button className={d.state && !d.district ? 'on' : ''}
                    onClick={() => d.goto(d.state, undefined)}>{d.state}</button>
          )}
          {d.district && <button className="on">{d.district.name}</button>}
        </div>
        {showLayers && d.district && (
          <div className="switch">
            {(['district', 'micromarket'] as const).map((l, i) => (
              <button key={l} className={d.layer === l ? 'on' : ''} onClick={() => d.setLayer(l)}>
                {layerLabels[i]}
              </button>
            ))}
          </div>
        )}
        <span className="dim" style={{ fontSize: 11 }}>
          {counter ?? (d.district ? `${points.length} in ${d.district.name}`
                                  : d.state ? `${nDistricts} districts`
                                            : '3 pilot states · click one to zoom in')}
        </span>
      </div>

      <div className="split">
        <Card title={<>Where you are{mapInfo && <Info wide text={mapInfo} />}</>}
              note={mapNote ?? (d.district ? d.district.name : d.state ?? 'India · pilot states')}>
          <GeoMap level={d.level} parent={d.district?.id ?? d.state} points={points} height={height}
                  selected={d.sel?.level === 'micromarket' ? d.sel.id : undefined}
                  selectedShape={d.sel?.level === 'district' ? d.sel.id : undefined}
                  onDrill={onDrill}
                  onSelect={id => d.select({ level: 'micromarket', id })}
                  shapeFill={shapeFill} shapeTitle={shapeTitle}
                  legend={legend ?? <span className="muted">
                    click a {d.level === 'india' ? 'pilot state' : d.level} to zoom in</span>} />
        </Card>
        {right}
      </div>

      {children}
    </>
  )
}

export default MapDrill
