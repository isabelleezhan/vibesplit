import { motion } from 'motion/react'
import styles from './Loader.module.css'

interface LoaderProps {
  message: string
  sub?: string
}


function Loader({ message, sub }: LoaderProps) {
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
    </div>
  )
}

export default Loader
