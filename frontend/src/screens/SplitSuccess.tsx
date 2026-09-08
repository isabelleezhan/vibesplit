import { motion } from 'motion/react'
import type { ConfirmSplitResult } from '../api'
import type { EditableCluster } from '../types'
import { clusterColorVars } from '../lib/clusterColors'
import styles from './SplitSuccess.module.css'

interface SplitSuccessProps {
  result: ConfirmSplitResult
  clusters: EditableCluster[]
  onStartOver: () => void
}

function SplitSuccess({ result, clusters, onStartOver }: SplitSuccessProps) {
  function colorFor(name: string) {
    const match = clusters.find((c) => !c.excluded && c.label === name)
    return clusterColorVars(match?.cluster_id ?? 0)
  }

  return (
    <div className={styles.wrap}>
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className={styles.heading}
      >
        <h1 className={styles.title}>Split complete.</h1>
        <p className={styles.subtitle}>
          {result.created.length} new playlist{result.created.length === 1 ? '' : 's'} added to your Spotify account.
        </p>
      </motion.div>

      <div className={styles.grid}>
        {result.created.map((item, i) => (
          <motion.a
            key={item.playlist_id}
            href={`https://open.spotify.com/playlist/${item.playlist_id}`}
            target="_blank"
            rel="noreferrer"
            className={`glassPanel ${styles.card}`}
            style={colorFor(item.name)}
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, delay: Math.min(i * 0.06, 0.5), ease: 'easeOut' }}
            whileHover={{ y: -3 }}
          >
            <span className={styles.cardName}>{item.name}</span>
            <span className={`mono ${styles.cardMeta}`}>{item.track_count} tracks · open on Spotify ↗</span>
          </motion.a>
        ))}
      </div>

      <button className="ghostButton" onClick={onStartOver}>
        Split another playlist
      </button>
    </div>
  )
}

export default SplitSuccess
