export type RubricPointDraft = {
  label: string
  max_score: string
  sort_order: number
}

export type RubricInputResult = {
  points: RubricPointDraft[]
  error?: string
}

/**
 * 评分说明是给大模型理解的整体规则，不把每一行或每个数字当成可相加的评分点。
 * 例如“答案相同或变式相同10分\n对一半5分”应作为一条总分为10分的分档规则保存。
 */
export function parseRubricInput(raw: string, totalScoreText: string): RubricInputResult {
  const label = raw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).join('\n')
  const totalScore = Number(totalScoreText)

  if (!Number.isFinite(totalScore) || totalScore <= 0) {
    return { points: [], error: '题目总分必须是大于 0 的数字。' }
  }
  if (!label) {
    return { points: [], error: '请填写给大模型使用的评分说明。' }
  }
  return {
    points: [{ label, max_score: totalScoreText.trim(), sort_order: 1 }],
  }
}
