import { create } from 'zustand'
import type { Level } from './api'

export interface Crumb { level: Level; id: string; name: string }

export type ProductLine = 'implements' | 'tractors'

interface State {
  view: string
  setView: (v: string) => void

  // which product line the whole tool is scoped to (client wants both)
  productLine: ProductLine
  setProductLine: (p: ProductLine) => void

  // geography drill path
  crumbs: Crumb[]
  push: (c: Crumb) => void
  popTo: (i: number) => void
  reset: () => void

  // filters shared by every panel
  sku?: string
  category?: string
  month?: number
  setSku: (s?: string) => void
  setCategory: (c?: string) => void
  setMonth: (m?: number) => void

  selectedVillage?: string
  setSelectedVillage: (v?: string) => void

  // the archetype the Act stage is scoped to -- shared so the summary and the playbook
  // always describe the same one
  actArchetype?: string
  setActArchetype: (a?: string) => void
}

export const useStore = create<State>((set, get) => ({
  view: 'executive',
  setView: (v) => set({ view: v }),

  productLine: 'implements',
  setProductLine: (p) => set({ productLine: p }),

  crumbs: [],
  push: (c) => set({ crumbs: [...get().crumbs, c] }),
  popTo: (i) => set({ crumbs: get().crumbs.slice(0, i + 1) }),
  reset: () => set({ crumbs: [], selectedVillage: undefined }),

  setSku: (s) => set({ sku: s, category: undefined }),
  setCategory: (c) => set({ category: c, sku: undefined }),
  setMonth: (m) => set({ month: m }),

  setSelectedVillage: (v) => set({ selectedVillage: v }),

  setActArchetype: (a) => set({ actArchetype: a }),
}))

/** The geography level whose CHILDREN should currently be listed. */
export function childLevel(crumbs: Crumb[]): Level {
  const n = crumbs.length
  return n === 0 ? 'state' : n === 1 ? 'district' : n === 2 ? 'block' : 'village'
}

export function currentParent(crumbs: Crumb[]): string | undefined {
  return crumbs.length ? crumbs[crumbs.length - 1].id : undefined
}
