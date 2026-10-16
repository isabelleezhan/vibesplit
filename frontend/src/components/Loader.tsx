import { motion } from 'motion/react'
import styles from './Loader.module.css'

interface LoaderProps {
  message: string
  sub?: string
  // 0 to 1. Only shown for work that can actually be counted, so the bar
  // never pretends to know more than it does.
  progress?: number
}


function Loader({ message, sub, progress }: LoaderProps) {
  return (
    <div className={styles.wrap}>
      <motion.div
        className={styles.dot}
        animate={{ opacity: [0.35, 1, 0.35], scale: [0.9, 1.05, 0.9] }}
        transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
      />
      <motion.p
        className={styles.message}
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
      >
        {message}
      </motion.p>
      {sub && <p className={styles.sub}>{sub}</p>}
      {progress !== undefined && (
        <div
          className={styles.track}
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress * 100)}
        >
          <motion.div
            className={styles.fill}
            initial={false}
            animate={{ scaleX: progress }}
            transition={{ duration: 0.6, ease: 'easeOut' }}
          />
        </div>
      )}
    </div>
  )
}

export default Loader
