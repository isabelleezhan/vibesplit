import { useEffect, useRef, useState } from 'react'
import {
  confirmSplit,
  getDemoPlaylists,
  getPlaylists,
  getSplitJob,
  logout,
  splitDemoPlaylist,
  startSplitJob,
  type ClustersResponse,
  type ConfirmSplitResult,
  type Playlist,
  type SplitJob,
} from './api'
import Login from './screens/Login'
import PlaylistPicker from './screens/PlaylistPicker'
import Loader from './components/Loader'
import ClusterPreview from './screens/ClusterPreview'
import SplitSuccess from './screens/SplitSuccess'
import MoltenMetal from './components/MoltenMetal'
import { toEditableClusters, type EditableCluster } from './types'
import {
  SplitCancelled,
  describeJob,
  forgetActiveJob,
  recallActiveJob,
  rememberActiveJob,
  waitForSplitJob,
} from './lib/splitJob'
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
  // The latest poll of the running split job, for the loader's progress.
  const [job, setJob] = useState<SplitJob | null>(null)
  // Browsing the no-login demo: sample playlists, split on the server
  // from saved data, never written to Spotify.
  const [demo, setDemo] = useState(false)

  const requestIdRef = useRef(0)

  useEffect(() => {
    getPlaylists()
      .then((res) => {
        setPlaylists(res.playlists)
        // Refreshed mid-split: the worker kept going, so resume watching.
        const stored = recallActiveJob()
        if (stored) {
          runSplit(stored.playlist, () => getSplitJob(stored.jobId))
        } else {
          setStep({ name: 'picker' })
        }
      })
      .catch(() => setStep({ name: 'login' }))
  }, [])

  async function runSplit(playlist: Playlist, getJob: () => Promise<SplitJob>) {
    const requestId = ++requestIdRef.current
    const isCancelled = () => requestIdRef.current !== requestId
    setError(null)
    setJob(null)
    setStep({ name: 'clustering', playlist })
    try {
      const started = await getJob()
      if (isCancelled()) return
      rememberActiveJob(started.id, playlist)
      const result = await waitForSplitJob(started, setJob, isCancelled)
      forgetActiveJob()
      setClusterMeta(result)
      setEditableClusters(toEditableClusters(result.clusters))
      setStep({ name: 'preview', playlist })
    } catch (err) {
      if (err instanceof SplitCancelled || isCancelled()) return
      forgetActiveJob()
      setError(err instanceof Error ? err.message : 'Something went wrong while clustering.')
      setStep({ name: 'picker' })
    }
  }

  async function runDemoSplit(playlist: Playlist) {
    const requestId = ++requestIdRef.current
    setError(null)
    setJob(null)
    setStep({ name: 'clustering', playlist })
    try {
      const result = await splitDemoPlaylist(playlist.id)
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

  function handleSelectPlaylist(playlist: Playlist) {
    if (demo) runDemoSplit(playlist)
    else runSplit(playlist, () => startSplitJob(playlist.id))
  }

  async function handleTryDemo() {
    const requestId = ++requestIdRef.current
    setDemo(true)
    setError(null)
    setPlaylists([])
    setStep({ name: 'checking' })
    try {
      const res = await getDemoPlaylists()
      if (requestIdRef.current !== requestId) return
      setPlaylists(res.playlists)
    } catch (err) {
      if (requestIdRef.current !== requestId) return
      setError(err instanceof Error ? err.message : 'Couldn\'t load the sample playlists.')
    }
    setStep({ name: 'picker' })
  }

  function resetToLogin() {
    requestIdRef.current++
    forgetActiveJob()
    setDemo(false)
    setJob(null)
    setPlaylists([])
    setClusterMeta(null)
    setEditableClusters([])
    setError(null)
    setStep({ name: 'login' })
  }

  async function handleLogout() {
    // Even if the request fails, the user asked to leave: show the login screen.
    await logout().catch(() => undefined)
    resetToLogin()
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
    // Walking away stops the watching, not the job: it still finishes and
    // gets cached, so picking the same playlist again comes back instantly.
    forgetActiveJob()
    setJob(null)
    setClusterMeta(null)
    setEditableClusters([])
    setError(null)
    setStep({ name: 'picker' })
  }

  const loggedIn = !demo && step.name !== 'checking' && step.name !== 'login'

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
        <div className={styles.headerRight}>
          {demo && (
            <>
              <span className={`mono ${styles.demoPill}`}>DEMO · SAMPLE DATA</span>
              <button className={styles.headerLink} onClick={resetToLogin}>
                Exit demo
              </button>
            </>
          )}
          {loggedIn && (
            <button className={styles.headerLink} onClick={handleLogout}>
              Log out
            </button>
          )}
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
        </div>
      </header>

      <main className={styles.main}>
        {step.name === 'checking' && (
          <Loader message={demo ? 'Loading sample playlists…' : 'Checking your session…'} />
        )}

        {step.name === 'login' && <Login onTryDemo={handleTryDemo} />}

        {step.name === 'picker' && (
          <PlaylistPicker playlists={playlists} error={error} onSelect={handleSelectPlaylist} demo={demo} />
        )}

        {step.name === 'clustering' && (
          <Loader
            message={`Splitting "${step.playlist.name}" into vibes…`}
            sub={demo ? 'Running k-means on the saved sample data…' : describeJob(job)}
          />
        )}

        {step.name === 'preview' && clusterMeta && (
          <ClusterPreview
            playlist={step.playlist}
            meta={clusterMeta}
            clusters={editableClusters}
            onChangeClusters={setEditableClusters}
            onConfirm={() => handleConfirm(step.playlist)}
            onBackToSamples={handleGoHome}
            demo={demo}
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
