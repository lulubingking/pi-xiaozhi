import { sortOcrBlocksReadingOrder, type PositionedOcrTextBlock } from './ocr-reading-order'

/**
 * Objective OCR must never return the printed option list as a student's
 * response. Only accept a short, standalone option mark from the answer crop;
 * ambiguous or prose OCR stays empty for teacher review.
 */
export function studentAnswerText(questionType: string, text: string): string {
  const value = text.trim()
  if (questionType !== 'objective') return value

  const compact = value
    .toUpperCase()
    .replace(/[\s\u3000]/g, '')
    .replace(/^(?:学生答案|选择|选项|答案)[:：]?/, '')
  const match = compact.match(/^[（(\[【]?([A-H])[）)\]】]?$/)
  return match?.[1] ?? ''
}

export type StudentAnswerOcrBlock = PositionedOcrTextBlock & {
  text_raw: string
  confidence?: string | number | null
}

export function studentAnswerFromOcrBlocks(questionType: string, blocks: StudentAnswerOcrBlock[], preserveLegacyBlockOrder = false): string {
  const ordered = preserveLegacyBlockOrder
    ? [...blocks].sort((left, right) => Number(left.block_index ?? 0) - Number(right.block_index ?? 0))
    : sortOcrBlocksReadingOrder(blocks)
  if (questionType !== 'objective') return ordered.map((block) => block.text_raw.trim()).filter(Boolean).join('\n')

  const choices = new Set<string>()
  ordered.forEach((block) => {
    const confidence = block.confidence == null ? null : Number(block.confidence)
    if (confidence != null && Number.isFinite(confidence) && confidence < 0.7) return
    const choice = studentAnswerText(questionType, block.text_raw)
    if (choice) choices.add(choice)
  })
  return choices.size === 1 ? [...choices][0] : ''
}
