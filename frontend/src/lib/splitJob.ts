import { getSplitJob, type ClustersResponse, type Playlist, type SplitJob } from '../api'

const POLL_INTERVAL_MS = 1500
// A single failed poll (say, the API restarting) shouldn't kill a split
// that's still running fine in the worker.
const MAX_CONSECUTIVE_POLL_FAILURES = 3
const STORAGE_KEY = 'vibesplit.activeJob'

export class SplitCancelled extends Error {}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** Polls a job until it finishes. Calls onUpdate with every poll so the
 *  loader can show progress, and stops early (throwing SplitCancelled)
 *  once isCancelled() says the user has moved on. */
export async function waitForSplitJob(
  job: SplitJob,
  onUpdate: (job: SplitJob) => void,
  isCancelled: () => boolean,
): Promise<ClustersResponse> {
  let current = job
  let failures = 0

  while (current.status === 'queued' || current.status === 'running') {
    onUpdate(current)
    await sleep(POLL_INTERVAL_MS)
    if (isCancelled()) throw new SplitCancelled()
    try {
      current = await getSplitJob(current.id)
      failures = 0
    } catch (err) {
      if (++failures >= MAX_CONSECUTIVE_POLL_FAILURES) throw err
    }
  }

  if (isCancelled()) throw new SplitCancelled()
  if (current.status === 'failed' || !current.result) {
    throw new Error(current.error ?? 'Something went wrong while splitting this playlist.')
  }
  return current.result
}

// --- Surviving a page refresh ---
// The job keeps running in the worker whether or not anyone is watching,
// so a refresh mid-split should pick the same job back up rather than
// dropping the user back at the picker.

interface StoredJob {
  jobId: string
  playlist: Playlist
}

export function rememberActiveJob(jobId: string, playlist: Playlist): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ jobId, playlist } satisfies StoredJob))
  } catch {
    // Private mode or storage disabled: the split still works, it just
    // won't survive a refresh.
  }
}

export function recallActiveJob(): StoredJob | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as StoredJob) : null
  } catch {
    return null
  }
}

export function forgetActiveJob(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // See rememberActiveJob.
  }
}

/** The loader's headline for where a job is up to. */
export function describeJob(job: SplitJob | null): string {
  if (!job || job.status === 'queued') return 'Waiting for a free worker…'
  switch (job.stage) {
    case 'clustering':
      return 'Grouping tracks by vibe…'
    case 'labeling':
      return 'Naming each group…'
    default:
      return 'Pulling tracks, genres and audio features…'
  }
}
