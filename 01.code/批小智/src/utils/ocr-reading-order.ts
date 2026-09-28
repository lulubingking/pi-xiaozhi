export type PositionedOcrTextBlock = {
  block_index?: number | null
  x1?: string | number | null
  y1?: string | number | null
  x2?: string | number | null
  y2?: string | number | null
  text_raw?: string | null
}

type Rect = { x1: number; y1: number; x2: number; y2: number }
type Positioned<T> = { item: T; originalIndex: number; rect: Rect }

function finite(value: unknown): number | null {
  if (value == null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function rectFor(item: PositionedOcrTextBlock): Rect | null {
  const values = [finite(item.x1), finite(item.y1), finite(item.x2), finite(item.y2)]
  if (values.some((value) => value == null)) return null
  const [x1, y1, x2, y2] = values as [number, number, number, number]
  return x2 > x1 && y2 > y1 ? { x1, y1, x2, y2 } : null
}

function fallbackOrder<T extends PositionedOcrTextBlock>(blocks: T[]): T[] {
  return blocks
    .map((item, originalIndex) => ({ item, originalIndex, index: finite(item.block_index) }))
    .sort((left, right) => (left.index ?? left.originalIndex) - (right.index ?? right.originalIndex) || left.originalIndex - right.originalIndex)
    .map(({ item }) => item)
}

function rowOrder<T>(blocks: Positioned<T>[]): Positioned<T>[] {
  if (!blocks.length) return []
  const heights = blocks.map(({ rect }) => Math.max(1, rect.y2 - rect.y1)).sort((left, right) => left - right)
  const middle = Math.floor(heights.length / 2)
  const medianHeight = heights.length % 2 ? heights[middle] : (heights[middle - 1] + heights[middle]) / 2
  const tolerance = Math.max(1, medianHeight * 0.42)
  const byY = [...blocks].sort((left, right) => {
    const y = (left.rect.y1 + left.rect.y2) / 2 - (right.rect.y1 + right.rect.y2) / 2
    return y || left.rect.x1 - right.rect.x1 || left.originalIndex - right.originalIndex
  })
  const rows: Positioned<T>[][] = []
  const centers: number[] = []

  byY.forEach((block) => {
    const centerY = (block.rect.y1 + block.rect.y2) / 2
    const matchingRows = centers
      .map((center, index) => ({ index, distance: Math.abs(centerY - center) }))
      .filter(({ distance }) => distance <= tolerance)
      .sort((left, right) => left.distance - right.distance)
    if (matchingRows.length) {
      const rowIndex = matchingRows[0].index
      rows[rowIndex].push(block)
      const rowCenters = rows[rowIndex].map(({ rect }) => (rect.y1 + rect.y2) / 2).sort((left, right) => left - right)
      const rowMiddle = Math.floor(rowCenters.length / 2)
      centers[rowIndex] = rowCenters.length % 2 ? rowCenters[rowMiddle] : (rowCenters[rowMiddle - 1] + rowCenters[rowMiddle]) / 2
    } else {
      rows.push([block])
      centers.push(centerY)
    }
  })

  return rows
    .map((row, index) => ({ row, center: centers[index] }))
    .sort((left, right) => left.center - right.center)
    .flatMap(({ row }) => row.sort((left, right) => left.rect.x1 - right.rect.x1 || left.rect.y1 - right.rect.y1 || left.originalIndex - right.originalIndex))
}

function columnSplit<T>(blocks: Positioned<T>[]): { split: number; spans: Positioned<T>[] } | null {
  if (blocks.length < 4) return null
  const minX = Math.min(...blocks.map(({ rect }) => rect.x1))
  const maxX = Math.max(...blocks.map(({ rect }) => rect.x2))
  const xSpan = maxX - minX
  if (xSpan <= 1) return null

  const candidates = blocks.filter(({ rect }) => rect.x2 - rect.x1 < xSpan * 0.72)
  if (candidates.length < 4) return null
  const centers = candidates.map(({ rect }) => (rect.x1 + rect.x2) / 2).sort((left, right) => left - right)
  const cuts = Array.from({ length: Math.max(0, centers.length - 3) }, (_, index) => ({ gap: centers[index + 2] - centers[index + 1], index: index + 1 }))
  if (!cuts.length) return null
  const cut = cuts.sort((left, right) => right.gap - left.gap)[0]
  const split = (centers[cut.index] + centers[cut.index + 1]) / 2
  const left = candidates.filter(({ rect }) => (rect.x1 + rect.x2) / 2 < split)
  const right = candidates.filter(({ rect }) => (rect.x1 + rect.x2) / 2 >= split)
  if (left.length < 2 || right.length < 2) return null

  const gutter = Math.min(...right.map(({ rect }) => rect.x1)) - Math.max(...left.map(({ rect }) => rect.x2))
  const widths = candidates.map(({ rect }) => rect.x2 - rect.x1).sort((a, b) => a - b)
  const middle = Math.floor(widths.length / 2)
  const medianWidth = widths.length % 2 ? widths[middle] : (widths[middle - 1] + widths[middle]) / 2
  if (gutter < Math.max(xSpan * 0.07, medianWidth * 0.18)) return null

  const spans = blocks.filter(({ rect }) => rect.x1 < split && rect.x2 > split)
  const spanSet = new Set(spans)
  const columnBlocks = blocks.filter((block) => !spanSet.has(block))
  const actualLeft = columnBlocks.filter(({ rect }) => (rect.x1 + rect.x2) / 2 < split)
  const actualRight = columnBlocks.filter(({ rect }) => (rect.x1 + rect.x2) / 2 >= split)
  return actualLeft.length >= 2 && actualRight.length >= 2 ? { split, spans } : null
}

/** Sort OCR blocks to match visual reading order; clear two-column layouts are read column by column. */
export function sortOcrBlocksReadingOrder<T extends PositionedOcrTextBlock>(blocks: T[]): T[] {
  if (!blocks.length) return []
  const positioned = blocks.map((item, originalIndex) => ({ item, originalIndex, rect: rectFor(item) }))
  if (positioned.some((entry) => entry.rect == null)) return fallbackOrder(blocks)
  const valid = positioned as Positioned<T>[]
  const columns = columnSplit(valid)
  if (!columns) return rowOrder(valid).map(({ item }) => item)

  const { split, spans } = columns
  const spanSet = new Set(spans)
  const left = valid.filter((block) => !spanSet.has(block) && (block.rect.x1 + block.rect.x2) / 2 < split)
  const right = valid.filter((block) => !spanSet.has(block) && (block.rect.x1 + block.rect.x2) / 2 >= split)
  const spanRows = rowOrder(spans)
  const heights = spans.map(({ rect }) => rect.y2 - rect.y1).sort((a, b) => a - b)
  const medianHeight = heights.length ? heights[Math.floor(heights.length / 2)] : 1
  const tolerance = Math.max(1, medianHeight * 0.42)
  const spanCenters = spanRows.reduce<number[]>((result, block) => {
    const center = (block.rect.y1 + block.rect.y2) / 2
    if (!result.length || Math.abs(center - result[result.length - 1]) > tolerance) result.push(center)
    return result
  }, [])

  const ordered: Positioned<T>[] = []
  let previousCenter = Number.NEGATIVE_INFINITY
  spanCenters.forEach((center) => {
    for (const side of [left, right]) {
      ordered.push(...rowOrder(side.filter(({ rect }) => {
        const y = (rect.y1 + rect.y2) / 2
        return y >= previousCenter && y < center
      })))
    }
    ordered.push(...rowOrder(spans.filter(({ rect }) => Math.abs((rect.y1 + rect.y2) / 2 - center) <= tolerance)))
    previousCenter = center
  })
  for (const side of [left, right]) {
    ordered.push(...rowOrder(side.filter(({ rect }) => (rect.y1 + rect.y2) / 2 >= previousCenter)))
  }
  return ordered.map(({ item }) => item)
}
