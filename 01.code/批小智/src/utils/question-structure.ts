import type { OcrBlock } from '../api/client'
import { choiceLabelsInText, extractQuestionMarkers, isChoiceSet, leadingChoiceLabel, normalizeQuestionNo, selectPrimaryQuestionMarkers } from './ocr-question-markers'

export type DetectedQuestionType = 'objective' | 'subjective' | 'needs_review'
export type DetectedQuestionSubtype =
  | 'single_choice'
  | 'multiple_choice'
  | 'true_false'
  | 'fill_blank'
  | 'matching'
  | 'cloze'
  | 'reading'
  | 'calculation'
  | 'proof'
  | 'short_answer'
  | 'essay'
  | 'translation'
  | 'experiment'
  | 'diagram'
  | 'comprehensive'
  | 'unknown'

export const questionSubtypeLabels: Record<DetectedQuestionSubtype, string> = {
  single_choice: '单选题',
  multiple_choice: '多选题',
  true_false: '判断题',
  fill_blank: '填空/补全题',
  matching: '连线/匹配题',
  cloze: '完形/语篇填空',
  reading: '阅读/材料题',
  calculation: '计算/应用题',
  proof: '证明/推理题',
  short_answer: '简答/解答题',
  essay: '作文/写作题',
  translation: '翻译题',
  experiment: '实验/探究题',
  diagram: '作图/读图题',
  comprehensive: '综合题',
  unknown: '待确认题型',
}

export type DetectedQuestion = {
  questionNo: string
  questionType: DetectedQuestionType
  questionSubtype: DetectedQuestionSubtype
  confidence: number
  evidence: string
  requiresPromptReview?: boolean
}

export type QuestionStructureResult = {
  questionCount: number
  objectiveCount: number
  subjectiveCount: number
  needsReviewCount: number
  confidence: number
  status: 'confirmed' | 'needs_review'
  sourceBlockCount: number
  questions: DetectedQuestion[]
  notes: string[]
  ocrTextByQuestionNo?: Record<string, string>
}

export type QuestionStructureSubject = 'chinese' | 'math' | 'english'

export function isQuestionStructureNeedsReview(question: Pick<DetectedQuestion, 'questionType' | 'questionSubtype' | 'confidence' | 'evidence' | 'requiresPromptReview'>): boolean {
  return question.questionType === 'needs_review'
    || question.questionSubtype === 'unknown'
    || question.confidence < 0.7
    || Boolean(question.requiresPromptReview)
    || question.evidence.includes('请核对原卷')
    || question.evidence.includes('需人工确认')
}

export function questionPromptFromOcr(question: DetectedQuestion, recognizedText: string, existingPrompt: string | null | undefined): string | null {
  if (question.requiresPromptReview) return existingPrompt ?? null
  return recognizedText.trim() || existingPrompt || null
}

type ClassificationRule = {
  subtype: DetectedQuestionSubtype
  questionType: 'objective' | 'subjective'
  signals: string[]
  pattern?: RegExp
  evidence: string
}

// 题型细分类只用于解释和候选定位；后续评分仍兼容 objective/subjective 两条现有业务路径。
// 规则覆盖三类首版学科以及常见理化生、历史地理题型，未知题型会明确降级为待确认。
const classificationRules: ClassificationRule[] = [
  { subtype: 'multiple_choice', questionType: 'objective', signals: ['多选题', '多项选择', '不定项选择', '选择所有正确', 'multiple choice', 'choose all'], evidence: '检测到多选或不定项选择要求' },
  { subtype: 'single_choice', questionType: 'objective', signals: ['单选题', '单项选择', '选择题', '选择正确答案', '选择最佳答案', 'single choice', 'choose the best'], evidence: '检测到单选或选择题要求' },
  { subtype: 'true_false', questionType: 'objective', signals: ['判断题', '判断正误', '正确的打', '错误的打', '对的打', '错的打', '是否正确', 'true or false', 'true/false'], evidence: '检测到判断正误要求' },
  { subtype: 'matching', questionType: 'objective', signals: ['连线题', '匹配题', '配对题', '连一连', '搭配', 'matching'], evidence: '检测到连线、匹配或配对要求' },
  { subtype: 'cloze', questionType: 'objective', signals: ['完形填空', '语篇填空', '短文填空', '选词填空', '补全短文', 'cloze'], evidence: '检测到完形或语篇填空要求' },
  { subtype: 'fill_blank', questionType: 'objective', signals: ['填空题', '填空', '补全句子', '补全对话', '横线上', '括号内填', '填写', 'fill in the blanks', 'complete the sentence', 'complete the dialogue'], pattern: /_{2,}|（\s*）|\(\s*\)/, evidence: '检测到填空、补全或空格格式' },
  { subtype: 'reading', questionType: 'subjective', signals: ['阅读理解', '现代文阅读', '文言文阅读', '古诗阅读', '诗歌鉴赏', '材料分析', '材料题', '史料分析', '任务型阅读', '综合性学习', '名著阅读', 'reading comprehension', 'reading passage'], evidence: '检测到阅读、材料、名著或综合性学习要求' },
  { subtype: 'essay', questionType: 'subjective', signals: ['作文', '写作', '书面表达', '写一篇', '写段话', '写话', '习作', '日记', '周记', 'writing', 'composition'], evidence: '检测到作文、写作或书面表达要求' },
  { subtype: 'translation', questionType: 'subjective', signals: ['翻译', '英译汉', '汉译英', '翻译下列句子', 'translation', 'translate'], evidence: '检测到翻译要求' },
  { subtype: 'proof', questionType: 'subjective', signals: ['证明题', '证明', '推理', '论证', '说明理由', '为什么'], evidence: '检测到证明、推理或说明理由要求' },
  { subtype: 'calculation', questionType: 'subjective', signals: ['计算题', '计算', '求值', '解方程', '解不等式', '化简', '因式分解', '应用题', '列式计算', '求出', '算一算'], evidence: '检测到计算、方程、化简或应用题要求' },
  { subtype: 'experiment', questionType: 'subjective', signals: ['实验题', '实验探究', '探究题', '实验报告', '设计实验', '实验方案', '观察并记录', '科学探究'], evidence: '检测到实验、探究或方案设计要求' },
  { subtype: 'diagram', questionType: 'subjective', signals: ['作图题', '画图', '作出图形', '补全图形', '读图', '识图', '看图回答', '绘图'], evidence: '检测到作图、读图或识图要求' },
  { subtype: 'comprehensive', questionType: 'subjective', signals: ['综合题', '综合实践', '综合应用', '开放性问题', '开放题', '探究与实践'], evidence: '检测到综合、开放或实践要求' },
  { subtype: 'short_answer', questionType: 'subjective', signals: ['解答题', '简答题', '论述题', '问答题', '简述', '概括', '分析', '解释', '说明', '有何作用', '有什么作用', '什么作用', '结合全文', '结合文章', '结合上下文', '联系实际', '谈谈', '如何理解', '写出过程', '解答过程', '步骤', '病句', '仿写', '口语交际', 'short answer', 'answer the question'], evidence: '检测到简答、解答、语言运用或过程说明要求' },
]

const sectionHeading = /(单选题|多选题|不定项选择|选择题|判断题|填空题|连线题|匹配题|完形填空|语篇填空|阅读理解|材料分析|作文|写作|书面表达|翻译|证明题|计算题|应用题|实验题|探究题|作图题|综合题|解答题|简答题|论述题)/
const mathExpression = /(?:[a-zA-Z]\s*[+\-*/^=<>≤≥]|[+\-*/^=<>≤≥]\s*[a-zA-Z]|√|∑|∫|∠|△|≌|∵|∴|x²|平方根|分式|函数|方程|不等式)/

function signalScore(text: string, signals: string[]): number {
  return signals.reduce((score, signal) => score + (text.includes(signal) ? 1 : 0), 0)
}

function classify(text: string, section: string, subject?: QuestionStructureSubject): { questionType: DetectedQuestionType; questionSubtype: DetectedQuestionSubtype; confidence: number; evidence: string } {
  const content = `${section}\n${text}`
  const candidates = classificationRules
    .map((rule) => ({ rule, score: signalScore(content, rule.signals) + (rule.pattern?.test(text) ? 2 : 0) }))
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score)
  const best = candidates[0]
  const second = candidates[1]
  const optionSet = isChoiceSet(choiceLabelsInText(text))
  if (optionSet) {
    const objectiveCandidate = candidates.find((candidate) => candidate.rule.questionType === 'objective')
    return {
      questionType: 'objective',
      questionSubtype: objectiveCandidate?.rule.subtype ?? 'single_choice',
      confidence: 0.82,
      evidence: `${objectiveCandidate?.rule.evidence ?? '检测到选择题选项'}；同一题目下检测到至少三个不同的选项标记`,
    }
  }
  if (subject === 'math' && mathExpression.test(text) && (!best || best.rule.questionType === 'subjective')) {
    return { questionType: 'subjective', questionSubtype: 'calculation', confidence: Math.min(0.9, 0.68 + (mathExpression.test(text) ? 0.12 : 0)), evidence: '检测到数学公式、方程或几何表达式' }
  }
  if (!best) {
    return { questionType: 'needs_review', questionSubtype: 'unknown', confidence: 0.45, evidence: '未发现明确题号、题型标题或作答要求' }
  }
  if (second && second.score === best.score && best.rule.questionType !== second.rule.questionType) {
    return { questionType: 'needs_review', questionSubtype: 'unknown', confidence: 0.58, evidence: '检测到客观与主观题型特征同时出现，需要教师确认' }
  }
  const ambiguityNote = second && second.score === best.score ? '；同时存在相近题型特征，建议核对' : ''
  return {
    questionType: best.rule.questionType,
    questionSubtype: best.rule.subtype,
    confidence: Math.min(0.98, (second && second.score === best.score ? 0.64 : 0.62) + Math.min(0.32, best.score * 0.08)),
    evidence: `${best.rule.evidence}${ambiguityNote}`,
  }
}

export type AnalyzeQuestionStructureOptions = { fallbackQuestionNo?: string; subject?: QuestionStructureSubject }

export function analyzeQuestionStructure(blocks: OcrBlock[], options: AnalyzeQuestionStructureOptions = {}): QuestionStructureResult {
  const usableBlocks = blocks
    .filter((block) => Boolean(block.text_raw?.trim()))
    .sort((left, right) => Number(left.block_index) - Number(right.block_index))
  const markerOccurrences = usableBlocks.flatMap((block) => extractQuestionMarkers(block.text_raw).map((marker) => ({ block, marker })))
  const primaryMarkers = new Set(selectPrimaryQuestionMarkers(markerOccurrences.map((item) => item.marker)))
  const markersByBlock = new Map<OcrBlock, typeof markerOccurrences>()
  markerOccurrences.filter((item) => primaryMarkers.has(item.marker)).forEach((item) => {
    markersByBlock.set(item.block, [...(markersByBlock.get(item.block) ?? []), item])
  })
  const primaryOccurrences = markerOccurrences.filter((item) => primaryMarkers.has(item.marker))
  const blocksByPage = new Map<number, OcrBlock[]>()
  const markersByPage = new Map<number, typeof markerOccurrences>()
  usableBlocks.forEach((block) => {
    const pageOrder = Math.floor(Number(block.block_index) / 100000)
    blocksByPage.set(pageOrder, [...(blocksByPage.get(pageOrder) ?? []), block])
  })
  primaryOccurrences.forEach((item) => {
    const pageOrder = Math.floor(Number(item.block.block_index) / 100000)
    markersByPage.set(pageOrder, [...(markersByPage.get(pageOrder) ?? []), item])
  })
  const pageStartsWithOrphanChoices = new Set<number>()
  markersByPage.forEach((occurrences, pageOrder) => {
    const firstMarker = [...occurrences].sort((left, right) => Number(left.block.block_index) - Number(right.block.block_index) || left.marker.index - right.marker.index)[0]
    if (!firstMarker) return
    const labels = (blocksByPage.get(pageOrder) ?? [])
      .filter((block) => Number(block.block_index) < Number(firstMarker.block.block_index))
      .map((block) => leadingChoiceLabel(block.text_raw))
      .filter((label): label is string => Boolean(label))
    if (isChoiceSet(labels)) pageStartsWithOrphanChoices.add(pageOrder)
  })
  const detected = new Map<string, { text: string; section: string }>()
  let currentSection = ''
  let activeQuestionNo: string | null = null
  let activePageOrder: number | null = null

  usableBlocks.forEach((block) => {
    const pageOrder = Math.floor(Number(block.block_index) / 100000)
    if (activePageOrder !== null && activePageOrder !== pageOrder && pageStartsWithOrphanChoices.has(pageOrder)) activeQuestionNo = null
    activePageOrder = pageOrder
    const text = block.text_raw.trim()
    const heading = text.match(sectionHeading)?.[1]
    if (heading) currentSection = heading
    const markers = markersByBlock.get(block) ?? []
    if (!markers.length) {
      if (activeQuestionNo) {
        const current = detected.get(activeQuestionNo)
        detected.set(activeQuestionNo, { text: `${current?.text ?? ''}\n${text}`.trim(), section: current?.section || currentSection })
      }
      return
    }
    markers.forEach(({ marker }, markerIndex) => {
      const nextMarker = markers[markerIndex + 1]?.marker
      const fragment = text.slice(marker.index, nextMarker?.index ?? text.length).trim()
      activeQuestionNo = marker.questionNo
      const current = detected.get(marker.questionNo)
      detected.set(marker.questionNo, { text: `${current?.text ?? ''}\n${fragment}`.trim(), section: current?.section || currentSection })
    })
  })

  // 每页都检查首个题号前的选项组，覆盖跨页续题和整张双页扫描。
  // 只有至少三个不同的选项标记才推断前一道客观题，并将题干缺失留给教师复核。
  const inferredQuestionNos = new Set<string>()
  markersByPage.forEach((occurrences, pageOrder) => {
    const firstMarker = [...occurrences].sort((left, right) => Number(left.block.block_index) - Number(right.block.block_index) || left.marker.index - right.marker.index)[0]
    if (!firstMarker) return
    const firstNo = Number(firstMarker.marker.questionNo)
    const leadingChoices = (blocksByPage.get(pageOrder) ?? [])
      .filter((block) => Number(block.block_index) < Number(firstMarker.block.block_index))
      .map((block) => ({ block, label: leadingChoiceLabel(block.text_raw) }))
      .filter((item): item is { block: OcrBlock; label: string } => Boolean(item.label))
    const candidateNo = String(firstNo - 1)
    if (!Number.isInteger(firstNo) || firstNo <= 1 || !isChoiceSet(leadingChoices.map((item) => item.label)) || detected.has(candidateNo)) return
    const optionsText = leadingChoices
      .sort((left, right) => Number(left.block.block_index) - Number(right.block.block_index))
      .map((item) => item.block.text_raw.trim())
      .join('\n')
    detected.set(candidateNo, { text: optionsText, section: '' })
    inferredQuestionNos.add(candidateNo)
  })

  const detectedEntries = Array.from(detected.entries())

  let questions = detectedEntries
    .sort(([left], [right]) => Number(left) - Number(right))
    .map(([questionNo, value]) => {
      if (inferredQuestionNos.has(questionNo)) {
        return {
          questionNo,
          questionType: 'objective' as const,
          questionSubtype: 'single_choice' as const,
          confidence: 0.66,
          evidence: '根据页内首个题号前至少三个选项标记推断为客观题；题号或题干未完整识别，请核对原卷',
          requiresPromptReview: true,
        }
      }
      return { questionNo, ...classify(value.text, value.section, options.subject) }
    })

  // 作文、整页简答题和手动录入的单题经常没有可解析的题号。
  // 只有调用方明确知道当前批次是单题时才启用，避免把多题试卷误合并为一道题。
  if (!questions.length && options.fallbackQuestionNo && usableBlocks.length) {
    const fallbackText = usableBlocks.map((block) => block.text_raw.trim()).join('\n')
    const result = classify(fallbackText, '', options.subject)
    questions = [{
      questionNo: normalizeQuestionNo(options.fallbackQuestionNo),
      questionType: result.questionType === 'objective' ? 'objective' : 'subjective',
      questionSubtype: result.questionSubtype === 'unknown' ? 'short_answer' : result.questionSubtype,
      confidence: Math.min(result.confidence, 0.72),
      evidence: `未识别到明确题号，已按当前批次单题整页兜底；${result.evidence}`,
    }]
  }

  // OCR 漏掉少量题号时只补紧凑范围，并标记为待确认。
  const numericQuestionNos = questions
    .map((item) => Number(item.questionNo))
    .filter((value) => Number.isInteger(value) && value > 0 && value <= 200)
  if (numericQuestionNos.length >= 2) {
    const min = Math.min(...numericQuestionNos)
    const max = Math.max(...numericQuestionNos)
    const completedRange = Array.from({ length: max - min + 1 }, (_, index) => String(min + index))
      .filter((questionNo) => !questions.some((item) => item.questionNo === questionNo))
    if (max - min <= 20 && completedRange.length > 0 && completedRange.length <= 3) {
      questions = [...questions, ...completedRange.map((questionNo) => ({
        questionNo,
        questionType: 'needs_review' as const,
        questionSubtype: 'unknown' as const,
        confidence: 0.42,
        evidence: '题号可能被 OCR 漏识，已按相邻题号补出草稿，需人工确认',
      }))].sort((left, right) => Number(left.questionNo) - Number(right.questionNo))
    }
  }

  const objectiveCount = questions.filter((item) => item.questionType === 'objective').length
  const subjectiveCount = questions.filter((item) => item.questionType === 'subjective').length
  const needsReviewCount = questions.filter(isQuestionStructureNeedsReview).length
  const confidence = questions.length ? questions.reduce((sum, item) => sum + item.confidence, 0) / questions.length : 0
  const notes: string[] = []
  if (!questions.length) notes.push('未识别到明确的题号格式，请确认图片包含完整题目编号，或在题干编辑区手动录入单题。')
  inferredQuestionNos.forEach((questionNo) => notes.push(`第 ${questionNo} 题根据页内首个题号前的选项组推断；OCR 可能未提取完整题号或题干，请对照原卷补全。`))
  if (questions.some((item) => item.evidence.includes('单题整页兜底'))) notes.push('当前按单题整页建立候选区域；待教师核对题干后即可确认位置模板。')
  if (needsReviewCount) notes.push(`${needsReviewCount} 道题的题号、题干或题型置信度较低，请教师核对后再确认。`)
  notes.push('题型识别覆盖选择、判断、填空、匹配、阅读、写作、计算、证明、实验、作图和综合等常见题型；低置信度结果仍需教师核对。')

  return {
    questionCount: questions.length,
    objectiveCount,
    subjectiveCount,
    needsReviewCount,
    confidence,
    status: needsReviewCount || !questions.length || inferredQuestionNos.size > 0 ? 'needs_review' : 'confirmed',
    sourceBlockCount: usableBlocks.length,
    questions,
    notes,
  }
}
