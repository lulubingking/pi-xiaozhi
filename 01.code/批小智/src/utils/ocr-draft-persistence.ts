export type SavedAnswerDraft = {
  answer_text: string
  is_blank_confirmed: boolean
  source_type: 'ocr' | 'manual' | 'teacher_corrected'
  coverage_status?: string
}

/** Use persisted teacher text after save; OCR is only the initial value for empty placeholders. */
export function questionPromptDraftFromSavedOrOcr(
  savedPrompt: string | null | undefined,
  ocrText: string,
  promptNeedsReview = false,
): string {
  const saved = savedPrompt?.trim() ?? ''
  if (saved && !/^\d+$/.test(saved)) return saved
  if (promptNeedsReview) return saved
  return ocrText.trim() || saved
}

/** Never let a later OCR refresh replace a teacher-edited or manually entered answer. */
export function shouldSyncAutoOcrDraft(saved: SavedAnswerDraft | null | undefined, ocrText: string): boolean {
  const recognized = ocrText.trim()
  if (!recognized) return false
  if (saved && (saved.source_type !== 'ocr' || saved.is_blank_confirmed || saved.coverage_status === 'reviewed')) return false
  return saved?.answer_text?.trim() !== recognized
}

/**
 * Identify untouched OCR text saved in the old model order. This lets the UI
 * show the coordinate-corrected order without silently changing saved history.
 */
export function savedAnswerMatchesLegacyOcrOrder(
  saved: SavedAnswerDraft | null | undefined,
  legacyOcrText: string,
): boolean {
  if (!saved || saved.source_type !== 'ocr' || saved.is_blank_confirmed) return false
  const normalize = (value: string) => value.replace(/\s+/g, ' ').trim()
  const previousText = normalize(saved.answer_text ?? '')
  return Boolean(previousText) && previousText === normalize(legacyOcrText)
}
