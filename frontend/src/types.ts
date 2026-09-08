import type { Cluster } from './api'

export interface EditableCluster extends Cluster {
  excluded: boolean
}

export function toEditableClusters(clusters: Cluster[]): EditableCluster[] {
  return clusters.map((cluster) => ({ ...cluster, excluded: false }))
}

export interface TrackDragPayload {
  fromClusterId: number
  trackIndex: number
}

export const DRAG_MIME = 'application/x-vibesplit-track'
