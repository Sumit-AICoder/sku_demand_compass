const BASE = '/api'

async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const qs = new URLSearchParams()
  Object.entries(params ?? {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  })
  const url = `${BASE}${path}${qs.toString() ? `?${qs}` : ''}`
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

export type Level = 'state' | 'district' | 'block' | 'village'

export interface GeoItem {
  id: string; name: string; parent: string | null
  lon: number; lat: number
  units: number; value: number; headroom: number; addressable: number
  top_sku?: string; top_category?: string; attach_gap?: number
  zone?: string; crop_system?: string; mech_tier?: string; archetype?: string
}

export interface Sku {
  sku_id: string; name: string; category: string; category_label: string
  hp_min: number; hp_max: number; price_inr: number; maturity: string
  life_years: number; season: string; rental_substitutable: number
}

export interface Meta {
  pilot_states: string[]
  counts: { districts: number; blocks: number; villages: number; skus: number }
  ucm: {
    districts_fitted: number; beats_seasonal_naive: number
    median_backtest_mape: number; median_snaive_mape: number
    median_r2: number; residual_autocorr_ok: number
  }
  weights: Record<string, number>
  clustering: { k: number; bootstrap_ari: number; spatial_coherence: number }
  sources: Array<Record<string, unknown>>
}

export const api = {
  meta: () => get<Meta>('/meta'),
  skus: () => get<Sku[]>('/skus'),
  factors: () => get<any[]>('/factors'),
  geo: (level: Level, p: { parent?: string; sku?: string; category?: string; month?: number }) =>
    get<{ level: Level; parent: string | null; season_factor: number; items: GeoItem[] }>(`/geo/${level}`, p),
  scores: (p: { level?: string; id?: string; category?: string; limit?: number }) =>
    get<any[]>('/scores', p),
  drivers: (village_id: string, sku_id: string) => get<any>('/drivers', { village_id, sku_id }),
  village: (id: string) => get<any>(`/village/${id}`),
  ucmDecomposition: (district_id: string) => get<any>('/ucm/decomposition', { district_id }),
  ucmUplift: (district_id: string) => get<any>('/ucm/uplift', { district_id }),
  ucmElasticities: (district_id?: string) => get<any[]>('/ucm/elasticities', { district_id }),
  ucmDiagnostics: () => get<any>('/ucm/diagnostics'),
  clusters: () => get<any[]>('/clusters'),
  clusterSkus: (id: number) => get<any[]>(`/clusters/${id}/skus`),
  whitespace: (p: { cluster_id?: number; state?: string; limit?: number }) =>
    get<any[]>('/whitespace', p),
  lookalike: (village_id: string, n = 20) => get<any[]>('/lookalike', { village_id, n }),
  scenario: (body: unknown) => post<any>('/scenario', body),
  compete: (p: { district_id?: string; category?: string }) => get<any[]>('/compete', p),
  network: (product: string) => get<{ product_line: string; provenance: string; districts: any[] }>('/network', { product }),
  networkSummary: (product: string) => get<any>('/network/summary', { product }),
  reviewMicromarkets: (p: { district?: string; archetype_id?: string; metric?: string; limit?: number }) =>
    get<{ metric: string; micromarkets: any[] }>('/review/micromarkets', p),
  reviewMicromarket: (id: string) => get<{ micromarket: any }>(`/review/micromarket/${id}`),
  reviewArchetypes: () => get<{ archetypes: any[]; diagnosis: any[]; totals: any }>('/review/archetypes'),
  reviewCoverage: (product: string, type: string) => get<{ product_line: string; type: string; provenance: string; own_dealers: number; competitor_dealers: number; archetypes: any[]; oems: any[] }>('/review/coverage', { product, type }),
  archetypeUcmDecomposition: (archetype_id: string) => get<{ archetype_id: string; provenance: string; series: any[]; diagnostics: any }>('/archetype-ucm/decomposition', { archetype_id }),
  archetypeUcmElasticities: (archetype_id?: string) => get<any[]>('/archetype-ucm/elasticities', { archetype_id }),
  archetypeUcmDiagnostics: () => get<{ archetypes: any[] }>('/archetype-ucm/diagnostics'),
  archetypeUcmUplift: (archetype_id: string, days: number) => get<any>('/archetype-ucm/uplift', { archetype_id, days }),
  agroclimate: () => get<{ provenance: string; temp_note: string; districts: any[] }>('/agroclimate'),
  defineDistricts: () => get<{ districts: any[] }>('/define/districts'),
  archetypes: () => get<{ archetypes: any[]; totals: any; hp_belts: any[]; subzones: any[] }>('/archetypes'),
  micromarkets: (p: { district?: string; archetype?: string; hp_belt?: string; metric?: string; limit?: number }) =>
    get<{ metric: string; micromarkets: any[] }>('/micromarkets', p),
  micromarketDetail: (id: string) => get<{ micromarket: any; villages: any[] }>(`/micromarket/${id}`),
  configureArchetype: (rule: unknown) => post<{ new_archetype: string; moved_micromarkets: number; n_archetypes: number; custom_count: number; archetypes: any[] }>('/archetypes/configure', rule),
  resetArchetypes: () => post<{ n_archetypes: number; custom_count: number }>('/archetypes/reset', {}),
  subsidy: (state?: string) => get<{ rows: any[] }>('/subsidy', { state }),
  planPriorities: (state: string, product: string) => get<{ state: string; skus: any[] }>('/plan/priorities', { state, product }),
  planDistricts: () => get<{ provenance: string; districts: any[] }>('/plan/districts'),
}

export const fmt = {
  units: (n: number | null | undefined) =>
    n == null ? '—' : n >= 1000 ? Math.round(n).toLocaleString('en-IN') : n.toFixed(1),
  cr: (n: number | null | undefined) =>
    n == null ? '—' : `₹${(n / 1e7).toFixed(n / 1e7 >= 100 ? 0 : 1)} cr`,
  pct: (n: number | null | undefined, d = 1) => (n == null ? '—' : `${n.toFixed(d)}%`),
  num: (n: number | null | undefined, d = 2) => (n == null ? '—' : n.toFixed(d)),
}

export const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
