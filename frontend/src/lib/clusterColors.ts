import type { CSSProperties } from 'react'

const PALETTE = [
  '#2dd4bf', // teal
  '#fb7185', // coral
  '#38bdf8', // sky
  '#a3e635', // lime
  '#f472b6', // pink
  '#facc15', // gold
  '#f97316', // orange
] as const

export function clusterColor(clusterId: number): string {
  const index = ((clusterId % PALETTE.length) + PALETTE.length) % PALETTE.length
  return PALETTE[index]
}


export function clusterColorVars(clusterId: number): CSSProperties {
  const color = clusterColor(clusterId)
  return {
    '--cluster-color': color,
    '--cluster-glow': `${color}59`, // ~35% alpha, for box-shadow glow
  } as CSSProperties
}
