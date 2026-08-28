import React, { useState } from 'react'
import { api } from '../lib/api'
import { useStore } from '../lib/store'
import { Card, Async, useAsync } from './common'

/**
 * ACT stage, superseded — the original Develop playbook, kept routable behind the
 * hidden flag in App.tsx while ActPlaybook.tsx carries the live version.
 *
 * Per the client brief, playbooks translate primary-survey root causes into
 * interventions. The survey (purchase drivers, brand perception, switching triggers,
 * unmet needs) is a one-time study still pending, so those inputs are modelled
 * placeholders for now and clearly labelled EY-primary · pending. The enablers a
 * playbook recommends (network expansion, subsidy focus) draw on real data where we
 * have it.
 */

// Placeholder survey-derived root causes, keyed loosely by archetype index. These are
// illustrative until the EY primary research lands and are badged as such in the UI.
const ROOT_CAUSES = [
  { driver: 'Service availability', insight: 'Buyers cite slow after-sales service and spares access.', enabler: 'Network coverage', action: 'Expand dealer/service points in white-space blocks; add spares stocking norms.' },
  { driver: 'Awareness / mileage', insight: 'Low awareness of fuel-efficiency and running-cost advantage.', enabler: 'Content strategy', action: 'Mileage-led BTL demos and vernacular digital content on running cost.' },
  { driver: 'Finance access', insight: 'Approval rates and down-payment are the deal-breakers here.', enabler: 'Engagement strategy', action: 'Tie-ups with local financiers; scheme-linked EMI offers at point of sale.' },
  { driver: 'Product fit', insight: 'Current variant under-serves local soil / crop conditions.', enabler: 'Focus products', action: 'Prioritise best-fit SKUs; flag genuine gaps to NPD rather than pushing sales.' },
]

export default function Playbooks() {
  const { productLine } = useStore()
  const clusters = useAsync(() => api.clusters(), [])
  const [sel, setSel] = useState<number>()
  const chosen = sel ?? clusters.data?.[0]?.cluster
  const rc = ROOT_CAUSES[(chosen ?? 0) % ROOT_CAUSES.length]

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        Playbooks marry <b>real enablers</b> (network coverage, subsidy) with{' '}
        <b>primary-survey root causes</b>. The survey is a one-time study still pending,
        so the customer-input rows below are <span className="pill pill-primary">EY primary · modelled</span>
        {' '}and will hot-swap when the study lands.
      </div>

      <Async state={clusters}>{(cl: any[]) => (
        <div className="grid g2" style={{ alignItems: 'start' }}>
          <Card title="Archetypes" note="pick one to build its playbook">
            <table>
              <thead><tr><th>Archetype</th><th>Villages</th><th>Top crops</th></tr></thead>
              <tbody>
                {cl.map(c => (
                  <tr key={c.cluster}
                      className={chosen === c.cluster ? 'row-on' : 'row-click'}
                      onClick={() => setSel(c.cluster)}>
                    <td>{c.archetype ?? `Archetype ${c.cluster}`}</td>
                    <td>{(c.n_villages ?? 0).toLocaleString('en-IN')}</td>
                    <td>{c.top_crops ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <Card title={`Playbook — ${cl.find(c => c.cluster === chosen)?.archetype ?? 'archetype'}`}
                note={`${productLine} · archetype-specific commercial plan`}>
            <div className="pb-row">
              <span className="pb-k">Root cause</span>
              <span>{rc.driver} <span className="pill pill-primary">survey · modelled</span></span>
            </div>
            <div className="pb-row"><span className="pb-k">What customers say</span><span>{rc.insight}</span></div>
            <div className="pb-row"><span className="pb-k">Primary enabler</span><span><b>{rc.enabler}</b></span></div>
            <div className="pb-row"><span className="pb-k">Recommended action</span><span>{rc.action}</span></div>
            <div className="pb-grid">
              <div className="pb-cell"><span className="pb-k">Network strategy</span><span className="pill pill-real">dealers · real</span></div>
              <div className="pb-cell"><span className="pb-k">Subsidy focus</span><span className="pill pill-secondary">policy · real (PB/MH)</span></div>
              <div className="pb-cell"><span className="pb-k">Engagement / beat plan</span><span className="pill pill-client">activities · pending ITL</span></div>
              <div className="pb-cell"><span className="pb-k">Content strategy</span><span className="pill pill-primary">survey · modelled</span></div>
            </div>
          </Card>
        </div>
      )}</Async>
    </div>
  )
}
