import React, { useState } from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync } from './common'

/**
 * DEFINE · District profile — real agro-climate (temperature, rainfall, crop-mix) plus
 * the modelled market signals (TIV, Sonalika share) at district level.
 */
const CROPS: Array<[string, string, string]> = [
  ['crop_wheat_share', 'Wheat', 'var(--c1)'],
  ['crop_rice_share', 'Rice', 'var(--c2)'],
  ['crop_cotton_share', 'Cotton', 'var(--c3)'],
  ['crop_soybean_share', 'Soybean', 'var(--c4)'],
  ['crop_sugarcane_share', 'Sugarcane', 'var(--c6)'],
]

type Sort = 'rain_normal_mm' | 'mean_temp' | 'tiv' | 'sonalika_share'

export default function DistrictProfile() {
  const ac = useAsync(() => api.defineDistricts(), [])
  const [sortBy, setSortBy] = useState<Sort>('tiv')
  const th = (key: Sort, label: string) => (
    <th className="srt" onClick={() => setSortBy(key)}
        style={{ textAlign: 'right', cursor: 'pointer' }}>
      {label}{sortBy === key ? ' ▾' : ''}
    </th>
  )

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Agro-climate — temperature, rainfall, crop-mix — is{' '}
        <span className="pill pill-secondary">IMD / DES · real</span>. TIV and Sonalika share
        are <span className="pill pill-client">modelled · ITL pending</span>.
      </div>

      <Async state={ac}>{(d: any) => {
        const rows = [...d.districts].sort((a, b) => (Number(b[sortBy]) || 0) - (Number(a[sortBy]) || 0))
        return (
          <Card title="District profile"
                note="real agro-climate + modelled TIV / market share">
            <table>
              <thead><tr>
                <th>District</th><th>State</th><th>NARP sub-zone</th>
                {th('tiv', 'TIV')}
                {th('sonalika_share', 'Sonalika %')}
                {th('mean_temp', 'Temp °C')}
                {th('rain_normal_mm', 'Rain mm')}
                <th style={{ width: 140 }}>Crop mix</th>
              </tr></thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.district_id}>
                    <td>{r.district}</td>
                    <td className="dim">{r.state}</td>
                    <td>{r.subzone_id ? <><b>{r.subzone_id}</b> {r.subzone}
                      <div className="dim" style={{ fontSize: 11 }}>LGP {r.lgp}d</div></> : '—'}</td>
                    <td style={{ textAlign: 'right' }}>{fmt.units(r.tiv)}</td>
                    <td style={{ textAlign: 'right' }}>
                      {r.sonalika_share != null ? `${(r.sonalika_share * 100).toFixed(1)}%` : '—'}</td>
                    <td style={{ textAlign: 'right' }}>
                      {r.mean_temp?.toFixed(1)}{r.temp_is_allocated && <span className="dim"> ~</span>}</td>
                    <td style={{ textAlign: 'right' }}>{r.rain_normal_mm ? Math.round(r.rain_normal_mm) : '—'}</td>
                    <td>
                      <div className="cropbar" title={r.top_crops ?? ''}>
                        {CROPS.map(([k, , color]) => {
                          const v = r[k] ?? 0
                          return v > 0.02 ? <span key={k} style={{ width: `${v * 100}%`, background: color }} /> : null
                        })}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )
      }}</Async>

      <div className="legend">
        {CROPS.map(([k, label, color]) => <span key={k}><i style={{ background: color }} />{label}</span>)}
        <span className="dim">· ~ = temperature filled from nearest station</span>
      </div>
    </div>
  )
}
