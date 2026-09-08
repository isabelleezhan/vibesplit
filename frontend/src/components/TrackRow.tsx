import type { DragEvent } from 'react'
import type { Track } from '../api'
import { DRAG_MIME, type TrackDragPayload } from '../types'
import styles from './TrackRow.module.css'

interface TrackRowProps {
  track: Track
  clusterId: number
  index: number
}

function TrackRow({ track, clusterId, index }: TrackRowProps) {
  function handleDragStart(e: DragEvent<HTMLDivElement>) {
    const payload: TrackDragPayload = { fromClusterId: clusterId, trackIndex: index }
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify(payload))
    e.dataTransfer.effectAllowed = 'move'
  }

  const artistNames = track.artists.map((a) => a.name).filter(Boolean).join(', ')

  return (
    <div className={styles.row} draggable onDragStart={handleDragStart}>
      <span className={styles.grip} aria-hidden="true">
        ⠿
      </span>
      <div className={styles.info}>
        <span className={styles.name}>{track.name ?? 'Untitled track'}</span>
        {artistNames && <span className={styles.artist}>{artistNames}</span>}
      </div>
      {track.is_local && <span className={`mono ${styles.badge}`}>local</span>}
    </div>
  )
}

export default TrackRow
