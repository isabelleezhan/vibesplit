import { useEffect, useState, type CSSProperties } from 'react'
import { motion } from 'motion/react'
import type { Playlist } from '../api'
import { extractCoverGlow } from '../lib/coverColor'
import styles from './PlaylistPicker.module.css'

interface PlaylistPickerProps {
  playlists: Playlist[]
  error: string | null
  onSelect: (playlist: Playlist) => void
}

function PlaylistPicker({ playlists, error, onSelect }: PlaylistPickerProps) {
  const [coverGlows, setCoverGlows] = useState<Record<string, string>>({})

  useEffect(() => {
    let cancelled = false
    for (const playlist of playlists) {
      if (!playlist.image_url) continue
      extractCoverGlow(playlist.image_url).then((color) => {
        if (cancelled || !color) return
        setCoverGlows((prev) => ({ ...prev, [playlist.id]: color }))
      })
    }
    return () => {
      cancelled = true
    }
  }, [playlists])

  return (
    <div className={styles.page}>
      <div className={styles.wrap}>
        <div className={styles.heading}>
          <h1 className={styles.title}>Pick a playlist to split.</h1>
        </div>

        {error && <div className={styles.error}>{error}</div>}

        {playlists.length === 0 ? (
          <p className={styles.empty}>No playlists found on this account.</p>
        ) : (
          <div className={styles.grid}>
            {playlists.map((playlist, i) => (
              <motion.button
                key={playlist.id}
                className={`glassPanel ${styles.card}`}
                onClick={() => onSelect(playlist)}
                style={{ '--cover-glow': coverGlows[playlist.id] ?? 'var(--accent)' } as CSSProperties}
                initial={{ opacity: 0, y: 14 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.35, delay: Math.min(i * 0.045, 0.5), ease: 'easeOut' }}
                whileHover={{ y: -3 }}
              >
                <div className={styles.cover}>
                  {playlist.image_url ? (
                    <img src={playlist.image_url} alt="" className={styles.coverImg} loading="lazy" />
                  ) : (
                    <div className={styles.coverPlaceholder} aria-hidden="true">
                      ♫
                    </div>
                  )}
                </div>

                <div className={styles.cardBody}>
                  <span className={styles.cardName}>{playlist.name}</span>
                  <span className={`mono ${styles.cardMeta}`}>
                    {playlist.track_count ?? '?'} tracks
                    {playlist.owner ? ` · ${playlist.owner}` : ''}
                  </span>
                </div>
              </motion.button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default PlaylistPicker
