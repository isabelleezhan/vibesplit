import { motion } from 'motion/react'
import { goToLogin } from '../api'
import styles from './Login.module.css'

interface LoginProps {
  onTryDemo: () => void
}

function Login({ onTryDemo }: LoginProps) {
  return (
    <div className={styles.wrap}>
      <motion.div
        className={`glassPanel ${styles.card}`}
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: 'easeOut' }}
      >
        <motion.div
          className={styles.eyebrow}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.1, duration: 0.4 }}
        >
          <span className={`mono ${styles.eyebrowText}`}>PLAYLIST · ANALYSIS · SPLIT</span>
        </motion.div>

        <motion.h1
          className={styles.title}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15, duration: 0.45 }}
        >
          One messy playlist,
          <br />
          sorted into its vibes.
        </motion.h1>

        <motion.p
          className={styles.body}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25, duration: 0.45 }}
        >
          VibeSplit clusters your tracks by genre and era and 
          writes the split back to Spotify as new
          playlists. Nothing leaves your account until you confirm.
        </motion.p>

        <motion.div
          className={styles.actions}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.35, duration: 0.45 }}
        >
          <button className="glowButton" onClick={goToLogin}>
            Connect Spotify
          </button>
          <button className="ghostButton" onClick={onTryDemo}>
            Try the demo
          </button>
        </motion.div>

        <motion.p
          className={styles.note}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.45, duration: 0.45 }}
        >
          Spotify only lets a handful of approved accounts log in to apps like this one. The demo splits real
          sample playlists with no login needed.
        </motion.p>
      </motion.div>
    </div>
  )
}

export default Login
