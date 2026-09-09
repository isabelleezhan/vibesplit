import { useEffect, useRef, useState } from 'react'
import {
  confirmSplit,
  getClusters,
  getPlaylists,
  type ClustersResponse,
  type ConfirmSplitResult,
  type Playlist,
} from './api'
import Login from './screens/Login'
import PlaylistPicker from './screens/PlaylistPicker'
import Loader from './components/Loader'
import ClusterPreview from './screens/ClusterPreview'
import SplitSuccess from './screens/SplitSuccess'
import MoltenMetal from './components/MoltenMetal'
import { toEditableClusters, type EditableCluster } from './types'
import spotifyLogo from './assets/spotify-full-logo-white.svg'
import styles from './App.module.css'

type Step =
  | { name: 'checking' }
  | { name: 'login' }
  | { name: 'picker' }
  | { name: 'clustering'; playlist: Playlist }
  | { name: 'preview'; playlist: Playlist }
  | { name: 'confirming'; playlist: Playlist }
  | { name: 'success'; playlist: Playlist; result: ConfirmSplitResult }

function App() {
  const [step, setStep] = useState<Step>({ name: 'checking' })
  const [playlists, setPlaylists] = useState<Playlist[]>([])
  const [clusterMeta, setClusterMeta] = useState<ClustersResponse | null>(null)
  const [editableClusters, setEditableClusters] = useState<EditableCluster[]>([])
  const [error, setError] = useState<string | null>(null)

  const requestIdRef = useRef(0)

  useEffect(() => {
    getPlaylists()
      .then((res) => {
        setPlaylists(res.playlists)
        setStep({ name: 'picker' })
      })
      .catch(() => setStep({ name: 'login' }))
  }, [])

  async function handleSelectPlaylist(playlist: Playlist) {
    const requestId = ++requestIdRef.current
    setError(null)
    setStep({ name: 'clustering', playlist })
    try {
      const result = await getClusters(playlist.id)
      if (requestIdRef.current !== requestId) return
      setClusterMeta(result)
      setEditableClusters(toEditableClusters(result.clusters))
      setStep({ name: 'preview', playlist })
    } catch (err) {
      if (requestIdRef.current !== requestId) return
      setError(err instanceof Error ? err.message : 'Something went wrong while clustering.')
      setStep({ name: 'picker' })
    }
  }

  async function handleConfirm(playlist: Playlist) {
    const requestId = ++requestIdRef.current
    setError(null)
    setStep({ name: 'confirming', playlist })
    try {
      const toSend = editableClusters.filter((c) => !c.excluded)
      const result = await confirmSplit(playlist.id, toSend)
      if (requestIdRef.current !== requestId) return
      setStep({ name: 'success', playlist, result })
    } catch (err) {
      if (requestIdRef.current !== requestId) return
      setError(err instanceof Error ? err.message : 'Something went wrong while creating playlists.')
      setStep({ name: 'preview', playlist })
    }
  }

  function handleGoHome() {
    if (step.name === 'checking' || step.name === 'login') return
    requestIdRef.current++
    setClusterMeta(null)
    setEditableClusters([])
    setError(null)
    setStep({ name: 'picker' })
  }

  return (
    <div className={styles.app}>
      <div className={styles.backdrop} aria-hidden="true">
        <MoltenMetal
          color1="#150826"
          color2="#b46cff"
          color3="#ffffff"
          speed={0.3}
          scale={4.5}
          glow={1.5}
          opacity={0.45}
          grain={false}
          mouseStrength={0.2}
        />
      </div>

      <header className={styles.header}>
        <button
          className={styles.wordmark}
          onClick={handleGoHome}
          disabled={step.name === 'checking' || step.name === 'login'}
        >
          VibeSplit
        </button>
        <a
          href="https://www.spotify.com"
          target="_blank"
          rel="noreferrer"
          className={styles.attribution}
          aria-label="Data provided by Spotify"
        >
          <span className={styles.attributionText}>Data from</span>
          <img src={spotifyLogo} alt="Spotify" className={styles.spotifyLogo} />
        </a>
      </header>

      <main className={styles.main}>
        {step.name === 'checking' && <Loader message="Checking your session…" />}

        {step.name === 'login' && <Login />}

        {step.name === 'picker' && (
          <PlaylistPicker playlists={playlists} error={error} onSelect={handleSelectPlaylist} />
        )}

        {step.name === 'clustering' && (
          <Loader message={`Splitting "${step.playlist.name}" into vibes…`} sub="Clustering tracks, then asking an LLM to name and refine each group." />
        )}

        {step.name === 'preview' && clusterMeta && (
          <ClusterPreview
            playlist={step.playlist}
            meta={clusterMeta}
            clusters={editableClusters}
            onChangeClusters={setEditableClusters}
            onConfirm={() => handleConfirm(step.playlist)}
            error={error}
          />
        )}

        {step.name === 'confirming' && (
          <Loader message="Creating your new playlists…" sub="Writing each cluster back to Spotify." />
        )}

        {step.name === 'success' && (
          <SplitSuccess result={step.result} clusters={editableClusters} onStartOver={handleGoHome} />
        )}
      </main>
    </div>
  )
}

export default App
