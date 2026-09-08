// Every function here corresponds to one route in app/*.py. 

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL as string

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: 'include',
    ...options,
  })

  if (!response.ok) {

    const body = await response.json().catch(() => null)
    throw new Error(body?.detail ?? `Request failed: ${response.status}`)
  }

  return response.json() as Promise<T>
}

// --- Types, matching each endpoint's actual response shape ---

export interface Playlist {
  id: string
  name: string
  track_count: number | null
  owner: string | null
  image_url: string | null
}

export interface PlaylistsResponse {
  total: number
  playlists: Playlist[]
}

export interface Artist {
  id: string | null
  name: string | null
}

export interface Track {
  id: string | null
  name: string | null
  is_local: boolean
  popularity: number | null
  release_date: string | null
  artists: Artist[]
  genres: string[]
  moods: string[]
}

export interface Cluster {
  cluster_id: number
  label: string
  track_count: number
  coherence: number | null
  tracks: Track[]
}

export interface ClustersResponse {
  k: number
  silhouette_score: number | null
  clusters: Cluster[]
  original_coherence: number | null
  avg_new_coherence: number | null
}

export interface ConfirmSplitResult {
  created: { playlist_id: string; name: string; track_count: number }[]
}

// --- Endpoints ---

export function goToLogin(): void {
  window.location.href = `${API_BASE_URL}/auth/login`
}

export function getPlaylists(): Promise<PlaylistsResponse> {
  return request<PlaylistsResponse>('/playlists')
}

export function getClusters(playlistId: string): Promise<ClustersResponse> {
  return request<ClustersResponse>(`/playlists/${playlistId}/clusters`, { method: 'POST' })
}

export function confirmSplit(playlistId: string, clusters: Cluster[]): Promise<ConfirmSplitResult> {
  return request<ConfirmSplitResult>(`/playlists/${playlistId}/confirm-split`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clusters }),
  })
}
