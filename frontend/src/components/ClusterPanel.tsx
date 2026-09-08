import { useState, type DragEvent } from 'react'
import { motion } from 'motion/react'
import { DRAG_MIME, type EditableCluster, type TrackDragPayload } from '../types'
import { clusterColorVars } from '../lib/clusterColors'
import TrackRow from './TrackRow'
import styles from './ClusterPanel.module.css'

interface ClusterPanelProps {
  cluster: EditableCluster
  index: number
  onRename: (clusterId: number, label: string) => void
  onToggleExclude: (clusterId: number) => void
  onDropTrack: (payload: TrackDragPayload, toClusterId: number) => void
}

function ClusterPanel({ cluster, index, onRename, onToggleExclude, onDropTrack }: ClusterPanelProps) {
  const [dragOver, setDragOver] = useState(false)

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes(DRAG_MIME)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    setDragOver(true)
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragOver(false)
    const raw = e.dataTransfer.getData(DRAG_MIME)
    if (!raw) return
    const payload: TrackDragPayload = JSON.parse(raw)
    onDropTrack(payload, cluster.cluster_id)
  }

  return (
    <motion.div
      className={`glassPanel ${styles.panel} ${cluster.excluded ? styles.excluded : ''} ${dragOver ? styles.dragOver : ''}`}
      style={clusterColorVars(cluster.cluster_id)}
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: Math.min(index * 0.05, 0.4), ease: 'easeOut' }}
      onDragOver={handleDragOver}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      <div className={styles.headRow}>
        <input
          className={styles.labelInput}
          value={cluster.label}
          onChange={(e) => onRename(cluster.cluster_id, e.target.value)}
          aria-label="Cluster name"
        />
        <button
          className={styles.excludeToggle}
          onClick={() => onToggleExclude(cluster.cluster_id)}
          title={cluster.excluded ? 'Include this cluster' : 'Exclude this cluster'}
        >
          {cluster.excluded ? 'excluded' : '✕'}
        </button>
      </div>

      <div className={`mono ${styles.metaRow}`}>
        <span>{cluster.tracks.length} tracks</span>
        {cluster.coherence !== null && <span>coherence {Math.round(cluster.coherence * 100)}%</span>}
      </div>

      <div className={styles.trackList}>
        {cluster.tracks.length === 0 ? (
          <p className={styles.emptyTracks}>Drop tracks here.</p>
        ) : (
          cluster.tracks.map((track, i) => (
            <TrackRow key={`${track.id ?? 'local'}-${i}`} track={track} clusterId={cluster.cluster_id} index={i} />
          ))
        )}
      </div>
    </motion.div>
  )
}

export default ClusterPanel
