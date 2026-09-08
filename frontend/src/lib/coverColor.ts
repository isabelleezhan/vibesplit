// Samples a cover image client-side to derive a glow color for its
// playlist card

const SAMPLE_SIZE = 16 // downscale target — plenty for an average, cheap to read

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255
  g /= 255
  b /= 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  const d = max - min

  if (d === 0) return [0, 0, l]

  const s = d / (1 - Math.abs(2 * l - 1))
  let h: number
  switch (max) {
    case r:
      h = ((g - b) / d) % 6
      break
    case g:
      h = (b - r) / d + 2
      break
    default:
      h = (r - g) / d + 4
  }
  h *= 60
  if (h < 0) h += 360
  return [h, s, l]
}

function hslToCss(h: number, s: number, l: number): string {
  return `hsl(${Math.round(h)}, ${Math.round(s * 100)}%, ${Math.round(l * 100)}%)`
}

export function extractCoverGlow(imageUrl: string): Promise<string | null> {
  return new Promise((resolve) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'

    img.onload = () => {
      try {
        const canvas = document.createElement('canvas')
        canvas.width = SAMPLE_SIZE
        canvas.height = SAMPLE_SIZE
        const ctx = canvas.getContext('2d')
        if (!ctx) {
          resolve(null)
          return
        }
        ctx.drawImage(img, 0, 0, SAMPLE_SIZE, SAMPLE_SIZE)
        const { data } = ctx.getImageData(0, 0, SAMPLE_SIZE, SAMPLE_SIZE)

        let r = 0
        let g = 0
        let b = 0
        let count = 0
        for (let i = 0; i < data.length; i += 4) {
          if (data[i + 3] < 128) continue // skip mostly-transparent pixels
          r += data[i]
          g += data[i + 1]
          b += data[i + 2]
          count++
        }
        if (count === 0) {
          resolve(null)
          return
        }

        const [h, s, l] = rgbToHsl(r / count, g / count, b / count)

        resolve(hslToCss(h, Math.max(s, 0.5), Math.min(Math.max(l, 0.42), 0.6)))
      } catch {

        resolve(null)
      }
    }

    img.onerror = () => resolve(null)
    img.src = imageUrl
  })
}
