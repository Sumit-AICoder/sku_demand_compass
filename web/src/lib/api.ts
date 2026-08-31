import { useStore } from './store'

const BASE = '/api'

/**
 * Every GET carries the product line. Injected here rather than at each of the ~40 call
 * sites: an endpoint that is not line-aware ignores the extra query param, and one that is
 * cannot be called without it by mistake. Explicit `product` in params still wins, for the
 * two screens that show both lines side by side.
 */
async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const qs = new URLSearchParams()
  qs.set('product', useStore.getState().productLine)
  Object.entries(params ?? {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  })
  const url = `${BASE}${path}${qs.toString() ? `?${qs}` : ''}`
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

/**
 * POST carries the product line the same way GET does. It did not, which is why switching
 * to tractors left the playbook and the Act forecasts silently answering for implements --
 * the toggle changed, the body did not, and the backend fell through to its own default.
 * A body that sets `product` itself still wins.
 */
async function post<T>(path: string, body: unknown): Promise<T> {
  const qs = new URLSearchParams({ product: useStore.getState().productLine })
  const r = await fetch(`${BASE}${path}?${qs}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => path)}`)
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
  archetypeSkus: (archetype_id: string, limit?: number) =>
    get<any[]>(`/archetypes/${archetype_id}/skus`, { limit }),
  bucketSkus: (bucket: string, limit?: number) =>
    get<any[]>(`/plan/buckets/${bucket}/skus`, { limit }),
  archetypeRivalsBySku: (archetype_id: string, limit?: number) =>
    get<any[]>(`/archetypes/${archetype_id}/rivals-by-sku`, { limit }),
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
  reviewProfile: (level: string, id: string) => get<any>('/review/profile', { level, id }),
  reviewArchetypes: () => get<{ archetypes: any[]; diagnosis: any[]; totals: any }>('/review/archetypes'),
  reviewCoverage: (product: string, type: string) => get<{ product_line: string; type: string; provenance: Record<string, string>; own_dealers: number; competitor_dealers: number; covered_states: string[]; districts: any[]; archetypes: any[]; oems: any[] }>('/review/coverage', { product, type }),
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
  defineProfile: (level: string, id: string) => get<any>('/define/profile', { level, id }),
  taxonomy: () => get<any>('/taxonomy'),
  saveTaxonomy: (tax: unknown) => put<any>('/taxonomy', tax),
  resetTaxonomy: () => post<any>('/taxonomy/reset', {}),
  subsidy: (state?: string) => get<{ rows: any[] }>('/subsidy', { state }),
  planPriorities: (state: string, product: string) => get<{ state: string; skus: any[] }>('/plan/priorities', { state, product }),
  planDistricts: () => get<{ provenance: string; districts: any[] }>('/plan/districts'),
  planBuckets: (p: { product?: string; fit_min?: number; mode?: string; defend_pct?: number }) =>
    get<{ rule: any; totals: any[]; archetypes: any[] }>('/plan/buckets', p),
  planBucketMicromarkets: (archetype_id: string, limit = 400) =>
    get<{ archetype_id: string; micromarkets: any[] }>(`/plan/bucket/${archetype_id}/micromarkets`, { limit }),
  planTargets: (p: { archetype_id: string; target_units?: number }) => get<any>('/plan/targets', p),
  planForecast: (body: unknown) => post<any>('/plan/forecast', body),
  actSummary: (archetype_id: string) => get<any>('/act/summary', { archetype_id }),
  actPlaybook: (body: unknown) => post<any>('/act/playbook', body),
}

export const fmt = {
  units: (n: number | null | undefined) =>
    n == null ? '—' : n >= 1000 ? Math.round(n).toLocaleString('en-IN') : n.toFixed(1),
  count: (n: number | null | undefined) =>
    n == null ? '—' : Math.round(n).toLocaleString('en-IN'),
  cr: (n: number | null | undefined) =>
    n == null ? '—' : `₹${(n / 1e7).toFixed(n / 1e7 >= 100 ? 0 : 1)} cr`,
  pct: (n: number | null | undefined, d = 1) => (n == null ? '—' : `${n.toFixed(d)}%`),
  num: (n: number | null | undefined, d = 2) => (n == null ? '—' : n.toFixed(d)),
}

export const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
