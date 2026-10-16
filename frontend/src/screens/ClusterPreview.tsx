import { motion } from 'motion/react'
import type { ClustersResponse, Playlist } from '../api'
import type { EditableCluster, TrackDragPayload } from '../types'
import ClusterPanel from '../components/ClusterPanel'
import styles from './ClusterPreview.module.css'

interface ClusterPreviewProps {
  playlist: Playlist
  meta: ClustersResponse
  clusters: EditableCluster[]
  onChangeClusters: (clusters: EditableCluster[]) => void
  onConfirm: () => void
  error: string | null
}

function ClusterPreview({ playlist, meta, clusters, onChangeClusters, onConfirm, error }: ClusterPreviewProps) {
  const includedCount = clusters.filter((c) => !c.excluded).length

  function renameCluster(clusterId: number, label: string) {
    onChangeClusters(clusters.map((c) => (c.cluster_id === clusterId ? { ...c, label } : c)))
  }

  function toggleExclude(clusterId: number) {
    onChangeClusters(clusters.map((c) => (c.cluster_id === clusterId ? { ...c, excluded: !c.excluded } : c)))
  }

  function moveTrack(payload: TrackDragPayload, toClusterId: number) {
    if (payload.fromClusterId === toClusterId) return
    const source = clusters.find((c) => c.cluster_id === payload.fromClusterId)
    const track = source?.tracks[payload.trackIndex]
    if (!track) return

    onChangeClusters(
      clusters.map((c) => {
        if (c.cluster_id === payload.fromClusterId) {
          return { ...c, tracks: c.tracks.filter((_, i) => i !== payload.trackIndex) }
        }
        if (c.cluster_id === toClusterId) {
          return { ...c, tracks: [...c.tracks, track] }
        }
        return c
      }),
    )
  }

  return (
    <div className={styles.wrap}>
      <motion.div
        className={`glassPanel ${styles.summary}`}
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
      >
        <div className={styles.summaryHeading}>
          <h1 className={styles.title}>{playlist.name}</h1>
          <p className={styles.subtitle}>
            Split into {meta.k} clusters · silhouette {meta.silhouette_score?.toFixed(2) ?? '—'}. Drag tracks between
            panels, rename a vibe, or exclude one before confirming.
          </p>
        </div>
      </motion.div>

      {error && <div className={styles.error}>{error}</div>}

      <div className={styles.grid}>
        {clusters.map((cluster, i) => (
          <ClusterPanel
            key={cluster.cluster_id}
            cluster={cluster}
            index={i}
            onRename={renameCluster}
            onToggleExclude={toggleExclude}
            onDropTrack={moveTrack}
          />
        ))}
      </div>

      <div className={styles.footer}>
        <span className={styles.footerNote}>
          {includedCount} of {clusters.length} clusters will be created.
        </span>
        <button className="glowButton" onClick={onConfirm} disabled={includedCount === 0}>
          Create {includedCount} playlist{includedCount === 1 ? '' : 's'} on Spotify
        </button>
      </div>
    </div>
  )
}

export default ClusterPreview
