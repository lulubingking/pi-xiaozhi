export type QuestionMarker = {
  questionNo: string
  index: number
  end: number
  parenthesized: boolean
  explicit: boolean
}

const chineseNumberDigits: Record<string, number> = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 零: 0 }
const chineseNumberUnits: Record<string, number> = { 十: 10, 百: 100 }
const chineseNumberPattern = '[一二三四五六七八九十百零]+'
const questionNoPattern = `(?:[0-9]{1,3}|${chineseNumberPattern})`
const markerPunctuation = '[-—–./、．:：]'

export function normalizeQuestionNo(value: string): string {
  const compact = value.replace(/\s/g, '')
  if (/^\d+$/.test(compact)) return String(Number(compact))
  let total = 0
  let current = 0
  for (const char of compact) {
    if (char in chineseNumberDigits) current = chineseNumberDigits[char]
    else if (char in chineseNumberUnits) {
      total += (current || 1) * chineseNumberUnits[char]
      current = 0
    }
  }
  const normalized = String(total + current)
  return normalized === '0' ? value.trim() : normalized
}

function addMarker(markers: QuestionMarker[], text: string, raw: string, start: number, end: number, parenthesized: boolean, explicit = false) {
  if (!raw || /\d/.test(text.slice(end, end + 1))) return
  const suffix = text.slice(end).trimStart()
  // 公式、变量和小数编号不作为题号，例如“2) x=3”或“1.25”。
  if (/^(?:[A-Za-z]|[=+\-*/^_{}\\])/.test(suffix)) return
  const questionNo = normalizeQuestionNo(raw)
  if (markers.some((marker) => marker.index === start)) return
  markers.push({ questionNo, index: start, end, parenthesized, explicit })
}

/**
 * 识别题号开头。题号只允许出现在行首，或使用明确的“第 N 题”格式，
 * 以减少把正文和公式里的数字误认为新题。
 */
export function extractQuestionMarkers(text: string): QuestionMarker[] {
  const markers: QuestionMarker[] = []
  const explicit = new RegExp(`(?:^|\\n)[\\t\\u3000 ]*第\\s*(${questionNoPattern})\\s*题`, 'gm')
  for (const match of text.matchAll(explicit)) {
    const matchStart = match.index ?? 0
    const start = matchStart + match[0].lastIndexOf('第')
    addMarker(markers, text, match[1], start, matchStart + match[0].length, false, true)
  }

  const lineStart = new RegExp(`(?:^|\\n)[\\t\\u3000 ]*(?:([（(])\\s*(${questionNoPattern})\\s*([）)])\\s*(?:${markerPunctuation})?|(${questionNoPattern})\\s*${markerPunctuation})`, 'gm')
  for (const match of text.matchAll(lineStart)) {
    const raw = match[2] ?? match[4]
    if (!raw) continue
    const start = (match.index ?? 0) + match[0].search(/[0-9一二三四五六七八九十百零]/)
    addMarker(markers, text, raw, start, start + match[0].length - match[0].search(/[0-9一二三四五六七八九十百零]/), Boolean(match[1] && match[3]))
  }

  return markers.sort((left, right) => left.index - right.index || left.end - right.end)
}

/**
 * Parenthesized 1/2/3 markers are commonly subquestions. When ordinary top-level
 * question markers exist, prefer those and keep parenthesized numbers out of the
 * structure/assignment sequence. All-parenthesized exam numbering remains valid.
 */
export function selectPrimaryQuestionMarkers(markers: QuestionMarker[]): QuestionMarker[] {
  const topLevel = markers.filter((marker) => !marker.parenthesized)
  const candidates = topLevel.length ? topLevel : markers
  const numericNos = [...new Set(candidates
    .map((marker) => Number(marker.questionNo))
    .filter((value) => Number.isInteger(value) && value > 0 && value <= 200))]
    .sort((left, right) => left - right)
  const sequenceNos = new Set<number>()
  let group: number[] = []
  const groups: number[][] = []
  numericNos.forEach((value, index) => {
    if (!group.length || value - group[group.length - 1] <= 2) group.push(value)
    else {
      groups.push(group)
      group = [value]
    }
    if (index === numericNos.length - 1) groups.push(group)
  })
  groups.filter((items) => items.length >= 2).flat().forEach((value) => sequenceNos.add(value))
  if (!sequenceNos.size) return candidates
  return candidates.filter((marker) => marker.explicit || sequenceNos.has(Number(marker.questionNo)))
}

function normalizeOptionLabel(value: string): string {
  const circled = '①②③④⑤⑥'.indexOf(value)
  if (circled >= 0) return String.fromCharCode(65 + circled)
  const normalized = value.toUpperCase().replace(/[Ａ-Ｆ]/g, (label) => String.fromCharCode(label.charCodeAt(0) - 0xfee0))
  return normalized.replace(/[甲乙丙丁戊己]/g, (label) => String.fromCharCode(65 + '甲乙丙丁戊己'.indexOf(label)))
}

export function leadingChoiceLabel(text: string): string | null {
  const trimmed = text.trimStart()
  const circled = trimmed.match(/^[①②③④⑤⑥]/)?.[0]
  if (circled) return normalizeOptionLabel(circled)
  const match = trimmed.match(/^(?:[（(]\s*([A-FＡ-Ｆ甲乙丙丁戊己])\s*[）)]|([A-FＡ-Ｆ甲乙丙丁戊己])\s*[.．、:：)）])/i)
  const label = match?.[1] ?? match?.[2]
  return label ? normalizeOptionLabel(label) : null
}

export function choiceLabelsInText(text: string): string[] {
  const labels = new Set<string>()
  for (const line of text.split(/\r?\n/)) {
    const label = leadingChoiceLabel(line)
    if (label) labels.add(label)
  }
  // Some OCR engines return several short choices in a single block instead of
  // one block per line. Require punctuation and at least three distinct labels
  // at the call site to avoid treating ordinary A/B references as options.
  const inline = /(?:^|[\s（(])([A-FＡ-Ｆ甲乙丙丁戊己①②③④⑤⑥])\s*[.．、:：)）]/gi
  for (const match of text.matchAll(inline)) labels.add(normalizeOptionLabel(match[1]))
  for (const match of text.matchAll(/[①②③④⑤⑥]/g)) labels.add(normalizeOptionLabel(match[0]))
  return [...labels].sort()
}

export function isChoiceSet(labels: string[]): boolean {
  const unique = [...new Set(labels.map(normalizeOptionLabel))].sort()
  if (unique.length < 3 || unique.some((label) => label < 'A' || label > 'F')) return false
  const values = unique.map((label) => label.charCodeAt(0))
  return values[values.length - 1] - values[0] <= unique.length + 1
}
