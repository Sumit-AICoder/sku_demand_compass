import React, { useState } from 'react'
import { api, fmt } from '../lib/api'
import { Card, useAsync } from './common'

/**
 * DEFINE · Configure — define a NEW archetype from a rule, then re-cluster.
 *
 * The user picks thresholds (TIV level, HP belt, crop, irrigation); every micro-market
 * that matches is carved into the new archetype and the whole segmentation is
 * re-summarised. Deterministic and transparent — a preview the client can react to.
 */
const HP_BELTS = ['', '<30 HP', '31-40 HP', '41-50 HP', '>50 HP']
const CROPS = ['', 'wheat', 'rice', 'cotton', 'soybean', 'sugarcane']

export default function Configure() {
  const arch = useAsync(() => api.archetypes(), [])
  const subzones = arch.data?.subzones ?? []
  const [name, setName] = useState('High-TIV Wheat >50HP Focus')
  const [tiv, setTiv] = useState('high')
  const [hpBelt, setHpBelt] = useState('>50 HP')
  const [crop, setCrop] = useState('wheat')
  const [irrigation, setIrrigation] = useState('')
  const [subzoneId, setSubzoneId] = useState('')
  const [result, setResult] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string>()

  async function run() {
    setBusy(true); setErr(undefined)
    try {
      const rule: any = { name }
      if (tiv) rule.tiv = tiv
      if (hpBelt) rule.hp_belt = hpBelt
      if (crop) rule.crop = crop
      if (irrigation) rule.irrigation = irrigation
      if (subzoneId) rule.subzone_id = subzoneId
      setResult(await api.configureArchetype(rule))
    } catch (e: any) { setErr(String(e)) } finally { setBusy(false) }
  }

  async function reset() {
    setBusy(true); setErr(undefined)
    try { await api.resetArchetypes(); setResult(null) }
    catch (e: any) { setErr(String(e)) } finally { setBusy(false) }
  }

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Define a new archetype from a rule. Matching micro-markets are carved out of their
        current archetype into the new one, and every summary (micro-markets, TIV, market
        share) is recomputed. This previews the reconfigured segmentation — it is not saved.
      </div>

      <Card title="New archetype rule" note="thresholds over the micro-market population">
        <div className="cfg-form">
          <label>Name<input value={name} onChange={e => setName(e.target.value)} /></label>
          <label>Agro-climatic sub-zone
            <select value={subzoneId} onChange={e => setSubzoneId(e.target.value)}>
              <option value="">Any</option>
              {subzones.map((z: any) => (
                <option key={z.subzone_id} value={z.subzone_id}>
                  {z.subzone_id} · {z.subzone} (LGP {z.lgp})
                </option>
              ))}
            </select>
          </label>
          <label>TIV
            <select value={tiv} onChange={e => setTiv(e.target.value)}>
              <option value="">Any</option><option value="high">High (top third)</option>
              <option value="low">Low (bottom third)</option>
            </select>
          </label>
          <label>HP belt
            <select value={hpBelt} onChange={e => setHpBelt(e.target.value)}>
              {HP_BELTS.map(b => <option key={b} value={b}>{b || 'Any'}</option>)}
            </select>
          </label>
          <label>Dominant crop
            <select value={crop} onChange={e => setCrop(e.target.value)}>
              {CROPS.map(c => <option key={c} value={c}>{c ? c[0].toUpperCase() + c.slice(1) : 'Any'}</option>)}
            </select>
          </label>
          <label>Irrigation
            <select value={irrigation} onChange={e => setIrrigation(e.target.value)}>
              <option value="">Any</option><option value="irrigated">Irrigated</option>
              <option value="rainfed">Rainfed</option>
            </select>
          </label>
          <button className="btn-primary" onClick={run} disabled={busy || !name.trim()}>
            {busy ? 'Re-clustering…' : 'Create & re-cluster'}
          </button>
          <button className="btn-ghost" onClick={reset} disabled={busy}>Reset to base</button>
        </div>
        {err && <p className="bad" style={{ padding: '8px 0 0', fontSize: 12 }}>{err}</p>}
        <p className="dim" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
          Saved server-side — the new archetype is reflected on the <b>Archetypes</b>,{' '}
          <b>Map Explorer</b> and <b>District profile</b> tabs too, until you reset.
        </p>
      </Card>

      {result && result.moved_micromarkets === 0 && (
        <div className="stage-note" style={{ borderColor: 'var(--warn)' }}>
          <b>No micro-markets matched “{result.new_archetype}”.</b> The rule was too narrow —
          loosen a filter (e.g. set HP belt or crop to <i>Any</i>) and try again.
        </div>
      )}

      {result && result.moved_micromarkets > 0 && (
        <Card title={`✓ Created “${result.new_archetype}” — ${result.n_archetypes} archetypes now`}
              note={`${result.moved_micromarkets.toLocaleString('en-IN')} micro-markets moved · ${result.custom_count} custom active · also updated on Archetypes / Map Explorer / District profile`}>
          <table>
            <thead><tr>
              <th>Archetype</th><th>HP belt</th>
              <th style={{ textAlign: 'right' }}>Micro-markets</th>
              <th style={{ textAlign: 'right' }}>Villages</th>
              <th style={{ textAlign: 'right' }}>TIV</th>
              <th style={{ textAlign: 'right' }}>Sonalika %</th>
              <th style={{ textAlign: 'right' }}>Demand /yr</th>
            </tr></thead>
            <tbody>
              {[...result.archetypes].sort((a: any, b: any) => (b.is_custom ? 1 : 0) - (a.is_custom ? 1 : 0)).map((r: any, i: number) => (
                <tr key={i} className={r.is_custom ? 'row-push' : ''}>
                  <td>{r.archetype}{r.is_custom && <span className="pill pill-real">custom</span>}</td>
                  <td className="dim">{r.hp_belt}</td>
                  <td style={{ textAlign: 'right' }}>{fmt.units(r.n_micromarkets)}</td>
                  <td style={{ textAlign: 'right' }} className="dim">{fmt.units(r.n_villages)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt.units(r.tiv)}</td>
                  <td style={{ textAlign: 'right' }}>{(r.avg_sonalika_share * 100).toFixed(1)}%</td>
                  <td style={{ textAlign: 'right' }}>{fmt.units(r.potential_units_yr)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  )
}
