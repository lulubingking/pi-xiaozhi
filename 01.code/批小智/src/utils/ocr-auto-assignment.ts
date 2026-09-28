import type { OcrBlock } from '../api/client'
import { extractQuestionMarkers, isChoiceSet, leadingChoiceLabel, normalizeQuestionNo, selectPrimaryQuestionMarkers } from './ocr-question-markers'
import { sortOcrBlocksReadingOrder } from './ocr-reading-order'

export type AutoAssignmentQuestion = {
  id: string
  question_no: string
  question_prompt?: string | null
  reference_answer?: string | null
}

export type OcrAssignmentOptions = { mode?: 'default' | 'question_paper' }

function extractNumberTokens(text: string): string[] {
  return (text.match(/\d+(?:\.\d+)?/g) ?? []).map((token) => {
    const normalized = token.replace(/^0+(?=\d)/, '')
    return normalized || '0'
  })
}

const COMMON_TEXT_TOKENS = new Set([
  '一个', '一种', '这个', '其中', '如果', '因为', '所以', '然后', '根据', '请你', '求出', '求解', '答案', '解答', '说明', '写出',
  'the', 'and', 'for', 'with', 'from', 'that', 'this', 'find', 'write', 'answer', 'show', 'what',
])

function extractTextTokens(text: string): string[] {
  const normalized = text
    .toLowerCase()
    .replace(/\\[a-z]+/g, ' ')
    .replace(/[{}()[\]_=+\-*/^:：，。！？、；;,.'"`]/g, ' ')
  const tokens = new Set<string>()
  for (const word of normalized.match(/[a-z][a-z0-9]{2,}/g) ?? []) {
    if (!COMMON_TEXT_TOKENS.has(word)) tokens.add(word)
  }
  for (const run of normalized.match(/[\u4e00-\u9fff]{2,}/g) ?? []) {
    if (!COMMON_TEXT_TOKENS.has(run)) tokens.add(run)
    for (let index = 0; index < run.length - 1; index += 1) {
      const bigram = run.slice(index, index + 2)
      if (!COMMON_TEXT_TOKENS.has(bigram)) tokens.add(bigram)
    }
    for (let index = 0; index < run.length - 2; index += 1) {
      tokens.add(run.slice(index, index + 3))
    }
  }
  return [...tokens]
}

function numberWeight(token: string): number {
  if (token.length >= 3) return 4
  if (token.length === 2) return 3
  return 1
}

function questionEvidence(question: AutoAssignmentQuestion): string[] {
  const source = `${question.question_prompt ?? ''}\n${question.reference_answer ?? ''}`
  const tokens = extractNumberTokens(source)
  const questionNo = normalizeQuestionNo(question.question_no)
  const firstQuestionNoIndex = tokens.findIndex((token) => token === questionNo)
  if (firstQuestionNoIndex >= 0 && question.question_prompt?.trim().match(/^\s*(?:第\s*)?\d+/)) {
    tokens.splice(firstQuestionNoIndex, 1)
  }
  return [...new Set(tokens)]
}

function longestCommonNumberRun(left: string[], right: string[]): number {
  let longest = 0
  for (let leftIndex = 0; leftIndex < left.length; leftIndex += 1) {
    for (let rightIndex = 0; rightIndex < right.length; rightIndex += 1) {
      let length = 0
      while (left[leftIndex + length] && left[leftIndex + length] === right[rightIndex + length]) length += 1
      longest = Math.max(longest, length)
    }
  }
  return longest
}

function scoreTextEvidence(block: OcrBlock, question: AutoAssignmentQuestion): number {
  const blockTokens = new Set(extractTextTokens(block.text_raw))
  const questionTokens = extractTextTokens(`${question.question_prompt ?? ''}\n${question.reference_answer ?? ''}`)
  const matched = questionTokens.filter((token) => blockTokens.has(token))
  if (!matched.length) return 0
  const strongMatches = matched.filter((token) => token.length >= 3 || /^[a-z]/.test(token))
  if (!strongMatches.length && matched.length < 2) return 0
  return (strongMatches.length ? 4 : 2) + Math.min(6, matched.length)
}

function scoreQuestionEvidence(block: OcrBlock, question: AutoAssignmentQuestion): number {
  const blockNumberTokens = extractNumberTokens(block.text_raw)
  const blockTokens = new Set(blockNumberTokens)
  const evidence = questionEvidence(question)
  const hasLongEvidence = evidence.some((token) => token.length >= 2)
  const weightedEvidence = hasLongEvidence
    ? evidence.filter((token) => token.length >= 2)
    : evidence
  const matched = weightedEvidence.filter((token) => blockTokens.has(token))
  const referenceTokens = extractNumberTokens(question.reference_answer ?? question.question_prompt ?? '')
  const sequenceRun = longestCommonNumberRun(blockNumberTokens, referenceTokens)
  let score = scoreTextEvidence(block, question)
  // 单个一位数字在任何学科中都非常常见，只有多个数字、长数字或连续公式证据才参与归属。
  if (matched.length && (hasLongEvidence || matched.length >= 2 || sequenceRun >= 3)) {
    score += matched.reduce((total, token) => total + numberWeight(token), 0) + (matched.length > 1 ? 2 : 0)
  }
  const sequenceScore = sequenceRun >= 3 ? 6 + sequenceRun * 4 : 0
  return score + sequenceScore
}

function blockCenter(block: OcrBlock): { x: number; y: number } {
  const x1 = Number(block.x1 ?? 0)
  const y1 = Number(block.y1 ?? 0)
  const x2 = Number(block.x2 ?? x1)
  const y2 = Number(block.y2 ?? y1)
  return { x: (x1 + x2) / 2, y: (y1 + y2) / 2 }
}

function clusterAxis(values: number[], clusterCount: number): number[] {
  if (!values.length) return []
  const min = Math.min(...values)
  const max = Math.max(...values)
  if (clusterCount <= 1 || max - min < 1) return [values.reduce((sum, value) => sum + value, 0) / values.length]
  let centers = Array.from({ length: clusterCount }, (_, index) => min + ((max - min) * index) / (clusterCount - 1))
  for (let iteration = 0; iteration < 8; iteration += 1) {
    const groups = centers.map(() => [] as number[])
    values.forEach((value) => {
      const nearest = centers.reduce((best, center, index) => Math.abs(value - center) < Math.abs(value - centers[best]) ? index : best, 0)
      groups[nearest].push(value)
    })
    centers = centers.map((center, index) => groups[index].length ? groups[index].reduce((sum, value) => sum + value, 0) / groups[index].length : center)
  }
  return centers.sort((left, right) => left - right)
}

function nearestIndex(value: number, centers: number[]): number {
  return centers.reduce((best, center, index) => Math.abs(value - center) < Math.abs(value - centers[best]) ? index : best, 0)
}

function normalizedQuestionNos(questions: AutoAssignmentQuestion[]) {
  return questions.map((question) => ({ ...question, normalizedNo: normalizeQuestionNo(question.question_no) }))
}

/**
 * 自动把 OCR 文本块归属到批次题目。
 * 题号明确时按题号归属；题号缺失时沿用版面顺序，不把老师变成候选区域选择器。
 * 这是可复现的前端兜底，教师只负责核对合并后的文本。
 */
export function inferOcrBlockAssignments(blocks: OcrBlock[], questions: AutoAssignmentQuestion[], options: OcrAssignmentOptions = {}): Record<string, string[]> {
  const orderedBlocks = [...new Map(blocks.map((block) => [block.id, block])).values()]
    .filter((block) => Boolean(block.text_raw?.trim()))
    .sort((left, right) => Number(left.block_index) - Number(right.block_index))
  const orderedQuestions = normalizedQuestionNos(questions)
  const assignments: Record<string, string[]> = {}
  let currentQuestionIndex = -1

  // OCR 可能漏掉一题的题号，却先识别到它的 A/B/C 选项。识别到至少三个不同选项时，
  // 把选项块归给紧邻的前一题；这也能将印刷题号映射到已有批次的连续内部题号。
  const allMarkerOccurrences = orderedBlocks
    .flatMap((block) => extractQuestionMarkers(block.text_raw).map((marker) => ({ block, marker })))
  const selectedMarkers = new Set(selectPrimaryQuestionMarkers(allMarkerOccurrences.map((item) => item.marker)))
  const primaryOccurrences = allMarkerOccurrences
    .filter((item) => selectedMarkers.has(item.marker))
    .filter((item) => Number(item.marker.questionNo) > 0 && Number(item.marker.questionNo) <= 200)
    .sort((left, right) => Number(left.block.block_index) - Number(right.block.block_index) || left.marker.index - right.marker.index)
  const primaryQuestionNos = new Set(primaryOccurrences.map((item) => item.marker.questionNo))
  const primaryMarkerKeys = new Set(primaryOccurrences.map((item) => `${item.block.id}:${item.marker.index}:${item.marker.questionNo}`))
  const markerOccurrences = primaryOccurrences
  const markersByNo = new Map<string, typeof markerOccurrences[number]>()
  markerOccurrences.forEach((item) => { if (!markersByNo.has(item.marker.questionNo)) markersByNo.set(item.marker.questionNo, item) })
  const markerNumbers = [...markersByNo.keys()].map(Number).sort((left, right) => left - right)
  const markerSpan = markerNumbers.length ? markerNumbers[markerNumbers.length - 1] - markerNumbers[0] + 1 : 0
  const firstMarker = markerOccurrences[0]
  const leadingChoiceBlocks = firstMarker
    ? orderedBlocks
      .filter((block) => Number(block.block_index) < Number(firstMarker.block.block_index))
      .map((block) => ({ block, label: leadingChoiceLabel(block.text_raw) }))
      .filter((item): item is { block: OcrBlock; label: string } => Boolean(item.label))
    : []
  const hasLeadingChoiceGroup = isChoiceSet(leadingChoiceBlocks.map((item) => item.label))
  const canAlignByOrder = markerSpan > 0
    && markerSpan <= orderedQuestions.length
    && orderedQuestions.length === markerSpan + (hasLeadingChoiceGroup ? 1 : 0)
  const markerQuestionIndexes = new Map<string, number>()
  if (canAlignByOrder) {
    markerNumbers.forEach((questionNo) => {
      const questionNoText = String(questionNo)
      const exactIndex = orderedQuestions.findIndex((question) => question.normalizedNo === questionNoText)
      markerQuestionIndexes.set(questionNoText, exactIndex >= 0 ? exactIndex : questionNo - markerNumbers[0] + (hasLeadingChoiceGroup ? 1 : 0))
    })
  }
  const previousQuestionNo = firstMarker ? String(Number(firstMarker.marker.questionNo) - 1) : ''
  const previousQuestionIndex = orderedQuestions.findIndex((question) => question.normalizedNo === previousQuestionNo)
  const leadingChoiceQuestionIndex = hasLeadingChoiceGroup
    ? previousQuestionIndex >= 0 ? previousQuestionIndex : canAlignByOrder ? 0 : -1
    : -1
  const leadingChoiceBlockTargets = new Map<string, string>()
  if (leadingChoiceQuestionIndex >= 0) {
    const targetId = orderedQuestions[leadingChoiceQuestionIndex].id
    leadingChoiceBlocks.forEach(({ block }) => leadingChoiceBlockTargets.set(block.id, targetId))
  }

  const assign = (questionId: string, blockId: string) => {
    if (!assignments[questionId]) assignments[questionId] = []
    if (!assignments[questionId].includes(blockId)) assignments[questionId].push(blockId)
  }

  if (options.mode === 'question_paper') {
    // 题目纸可能包含双页扫描或多栏材料，block_index 会在栏间交错。
    // 题干抽取按同页的题号锚点、横向栏位和纵向区间归属；未落在题目栏位的材料块保持未归属，避免把阅读材料或别题内容写成题干。
    const pages = new Map<number, OcrBlock[]>()
    const assignedPaperBlockIds = new Set(Object.values(assignments).flat())
    const assignPaper = (questionId: string, blockId: string) => {
      assign(questionId, blockId)
      assignedPaperBlockIds.add(blockId)
    }
    orderedBlocks.forEach((block) => {
      const pageOrder = Math.floor(Number(block.block_index) / 100000)
      pages.set(pageOrder, [...(pages.get(pageOrder) ?? []), block])
    })
    pages.forEach((pageBlocks) => {
      const pageBlockIds = new Set(pageBlocks.map((block) => block.id))
      const pageMarkers = markerOccurrences
        .filter((item) => pageBlockIds.has(item.block.id))
        .sort((left, right) => Number(left.block.block_index) - Number(right.block.block_index) || left.marker.index - right.marker.index)
      const pageFirstMarker = pageMarkers[0] ?? null
      const anchors = markerOccurrences
        .filter((item) => pageBlockIds.has(item.block.id))
        .flatMap(({ block, marker }) => {
          const questionIndex = markerQuestionIndexes.get(marker.questionNo)
            ?? orderedQuestions.findIndex((question) => question.normalizedNo === marker.questionNo)
          return questionIndex >= 0 ? [{ block, questionId: orderedQuestions[questionIndex].id, markerNo: marker.questionNo, ...blockCenter(block) }] : []
        })
      const anchorByMarkerKey = new Map(anchors.map((anchor) => [`${anchor.block.id}:${anchor.markerNo}`, anchor]))
      const allX = pageBlocks.map((block) => blockCenter(block).x)
      const minX = Math.min(...allX)
      const maxX = Math.max(...allX)
      const horizontalRange = maxX - minX
      const anchorXs = anchors.map((anchor) => anchor.x)
      const anchorRange = anchorXs.length ? Math.max(...anchorXs) - Math.min(...anchorXs) : 0
      const columnCenters = anchors.length > 1 && anchorRange >= horizontalRange * 0.35
        ? clusterAxis(anchorXs, 2)
        : anchors.length ? [anchorXs.reduce((sum, value) => sum + value, 0) / anchorXs.length] : []
      const horizontalTolerance = horizontalRange <= 2 ? 0.22 : Math.max(80, horizontalRange * 0.18)

      const pageLeadingChoices = pageFirstMarker
        ? pageBlocks
          .filter((block) => Number(block.block_index) < Number(pageFirstMarker.block.block_index))
          .map((block) => ({ block, label: leadingChoiceLabel(block.text_raw) }))
          .filter((item): item is { block: OcrBlock; label: string } => Boolean(item.label))
        : []
      let leadingTarget = pageLeadingChoices[0] ? leadingChoiceBlockTargets.get(pageLeadingChoices[0].block.id) : null
      if (pageFirstMarker && isChoiceSet(pageLeadingChoices.map((item) => item.label))) {
        const firstNo = Number(pageFirstMarker.marker.questionNo)
        const previousQuestionIndex = orderedQuestions.findIndex((question) => question.normalizedNo === String(firstNo - 1))
        const firstMarkerQuestionIndex = markerQuestionIndexes.get(pageFirstMarker.marker.questionNo)
          ?? orderedQuestions.findIndex((question) => question.normalizedNo === pageFirstMarker.marker.questionNo)
        const inferredPreviousIndex = previousQuestionIndex >= 0 ? previousQuestionIndex : firstMarkerQuestionIndex - 1
        if (!leadingTarget && inferredPreviousIndex >= 0 && inferredPreviousIndex < orderedQuestions.length) {
          leadingTarget = orderedQuestions[inferredPreviousIndex].id
        }
        if (leadingTarget) pageLeadingChoices.forEach(({ block }) => leadingChoiceBlockTargets.set(block.id, leadingTarget!))
      }
      if (pageFirstMarker && leadingTarget && isChoiceSet(pageLeadingChoices.map((item) => item.label))) {
        const choiceCenters = pageLeadingChoices.map((item) => blockCenter(item.block))
        const choiceX = choiceCenters.reduce((sum, point) => sum + point.x, 0) / choiceCenters.length
        const choiceY = Math.min(...choiceCenters.map((point) => point.y))
        const firstMarkerCenter = blockCenter(pageFirstMarker.block)
        const verticalTolerance = horizontalRange <= 2 ? 0.04 : Math.max(40, horizontalRange * 0.025)
        if (Math.abs(choiceX - firstMarkerCenter.x) <= horizontalTolerance) {
          pageBlocks.forEach((block) => {
            const center = blockCenter(block)
            if (Number(block.block_index) >= Number(pageFirstMarker.block.block_index)
              || center.y < choiceY - verticalTolerance
              || center.y >= firstMarkerCenter.y
              || Math.abs(center.x - choiceX) > horizontalTolerance) return
            assignPaper(leadingTarget, block.id)
          })
        }
      }

      pageBlocks.forEach((block) => {
        const leadingChoiceTarget = leadingChoiceBlockTargets.get(block.id)
        if (leadingChoiceTarget) {
          assignPaper(leadingChoiceTarget, block.id)
          return
        }
        const explicitId = block.question_id && questions.some((question) => question.id === block.question_id) ? block.question_id : null
        if (explicitId) {
          assignPaper(explicitId, block.id)
          return
        }
        const marker = extractQuestionMarkers(block.text_raw).find((item) => primaryMarkerKeys.has(`${block.id}:${item.index}:${item.questionNo}`))
        if (marker) {
          const anchor = anchorByMarkerKey.get(`${block.id}:${marker.questionNo}`)
          if (anchor) assignPaper(anchor.questionId, block.id)
        }
      })

      if (!anchors.length || !columnCenters.length) return
      pageBlocks.forEach((block) => {
        if (assignedPaperBlockIds.has(block.id)) return
        const center = blockCenter(block)
        const columnIndex = nearestIndex(center.x, columnCenters)
        const columnCenter = columnCenters[columnIndex]
        if (Math.abs(center.x - columnCenter) > horizontalTolerance) return
        const columnAnchors = anchors
          .filter((anchor) => nearestIndex(anchor.x, columnCenters) === columnIndex)
          .sort((left, right) => left.y - right.y)
        const preceding = columnAnchors.filter((anchor) => anchor.y <= center.y)
        if (!preceding.length) return
        const questionId = preceding[preceding.length - 1].questionId
        if (/^(?:语文试卷|数学试卷|英语试卷|第\s*\d+\s*页|共\s*\d+\s*页)/.test(block.text_raw.trim())) return
        assignPaper(questionId, block.id)
      })
    })
    return assignments
  }

  orderedBlocks.forEach((block) => {
    const leadingChoiceTarget = leadingChoiceBlockTargets.get(block.id)
    if (leadingChoiceTarget) {
      assign(leadingChoiceTarget, block.id)
      return
    }
    const explicitId = block.question_id && questions.some((question) => question.id === block.question_id) ? block.question_id : null
    if (explicitId) {
      currentQuestionIndex = Math.max(currentQuestionIndex, orderedQuestions.findIndex((question) => question.id === explicitId))
      assign(explicitId, block.id)
      return
    }

    const marker = extractQuestionMarkers(block.text_raw).find((item) => primaryQuestionNos.has(item.questionNo) && primaryMarkerKeys.has(`${block.id}:${item.index}:${item.questionNo}`))
    if (marker) {
      const markerIndex = markerQuestionIndexes.get(marker.questionNo)
        ?? orderedQuestions.findIndex((question) => question.normalizedNo === marker.questionNo)
      // 题目内部的“1.”、“2.”等步骤编号不能把当前题目跳回前面的题。
      if (markerIndex >= currentQuestionIndex && markerIndex >= 0) currentQuestionIndex = markerIndex
    }
    if (currentQuestionIndex >= 0) assign(orderedQuestions[currentQuestionIndex].id, block.id)
  })

  // 手写、印刷体以及数学 OCR 都可能只返回内容块，不返回“第几题”的题号。
  // 这时使用题干/参考答案的文本、数字和公式证据做二次归属，避免识别成功但文本框为空。
  const assignedBlockIds = new Set(Object.values(assignments).flat())
  orderedBlocks.forEach((block) => {
    if (assignedBlockIds.has(block.id)) return
    const scored = orderedQuestions
      .map((question) => ({ question, score: scoreQuestionEvidence(block, question) }))
      .filter((item) => item.score > 0)
      .sort((left, right) => right.score - left.score)
    const best = scored[0]
    const second = scored[1]
    if (best && (!second || best.score > second.score)) {
      assign(best.question.id, block.id)
      assignedBlockIds.add(block.id)
    }
  })

  // 内容证据不足的块按页面空间布局归属。常见的“两列多题”排版，
  // 先按列聚类，再按每列的纵向顺序匹配题号，避免把 q1 的“2”误归到 q5。
  const assignedCenters = orderedQuestions.flatMap((question) => {
    const questionBlocks = orderedBlocks.filter((block) => assignments[question.id]?.includes(block.id))
    if (!questionBlocks.length) return []
    const centers = questionBlocks.map(blockCenter)
    return [{ questionId: question.id, x: centers.reduce((sum, point) => sum + point.x, 0) / centers.length, y: centers.reduce((sum, point) => sum + point.y, 0) / centers.length }]
  })
  const allCenters = orderedBlocks.map(blockCenter)
  const columnCenters = clusterAxis(allCenters.map((center) => center.x), Math.min(2, Math.max(1, orderedQuestions.length)))
  const rowCentersByColumn = columnCenters.map((columnCenter) => {
    const columnYs = allCenters.filter((center) => nearestIndex(center.x, columnCenters) === columnCenters.indexOf(columnCenter)).map((center) => center.y)
    return clusterAxis(columnYs, Math.min(3, Math.max(1, orderedQuestions.length)))
  })
  const cellForCenter = (center: { x: number; y: number }) => {
    const column = nearestIndex(center.x, columnCenters)
    const rows = rowCentersByColumn[column] ?? []
    return { column, row: nearestIndex(center.y, rows) }
  }
  const templates = [
    (index: number) => ({ column: Math.floor(index / 3), row: index % 3 }),
    (index: number) => ({ column: index % 2, row: Math.floor(index / 2) }),
  ]
  const matchedTemplate = templates
    .map((template) => ({ template, score: assignedCenters.reduce((score, center) => {
      const questionIndex = orderedQuestions.findIndex((question) => question.id === center.questionId)
      if (questionIndex < 0) return score
      const expected = template(questionIndex)
      const actual = cellForCenter(center)
      return score + (expected.column === actual.column && expected.row === actual.row ? 1 : 0)
    }, 0) }))
    .sort((left, right) => right.score - left.score)[0]?.template
  if (matchedTemplate && columnCenters.length > 1 && assignedCenters.length) {
    orderedBlocks.forEach((block) => {
      if (assignedBlockIds.has(block.id)) return
      const cell = cellForCenter(blockCenter(block))
      const candidates = orderedQuestions.filter((_question, index) => {
        const expected = matchedTemplate(index)
        return expected.column === cell.column && expected.row === cell.row
      })
      if (candidates.length === 1) {
        assign(candidates[0].id, block.id)
        assignedBlockIds.add(block.id)
      }
    })
  }

  // 网格无法判断时，使用已经归属的空间邻近关系作为最后兜底。
  // 这是候选归属，不会覆盖原始 OCR，教师仍可在文本框中查缺补漏。
  if (assignedCenters.length) {
    orderedBlocks.forEach((block) => {
      if (assignedBlockIds.has(block.id)) return
      const center = blockCenter(block)
      const nearest = assignedCenters.reduce((best, candidate) => {
        const distance = Math.hypot(center.x - candidate.x, center.y - candidate.y)
        return !best || distance < best.distance ? { ...candidate, distance } : best
      }, null as ({ questionId: string; distance: number } | null))
      if (nearest) {
        assign(nearest.questionId, block.id)
        assignedBlockIds.add(block.id)
      }
    })
  }

  if (!Object.keys(assignments).length && orderedQuestions.length === 1) {
    orderedBlocks.forEach((block) => assign(orderedQuestions[0].id, block.id))
  }
  return assignments
}

export function textForAssignedBlocks(blocks: OcrBlock[], blockIds: string[]): string {
  const selected = new Set(blockIds)
  return sortOcrBlocksReadingOrder(blocks.filter((block) => selected.has(block.id)))
    .map((block) => block.text_raw.trim())
    .filter(Boolean)
    .join('\n')
}

export function answerTextFromOcr(text: string): string {
  const match = text.match(/(?:参考答案|标准答案|答案|解答|答)\s*[:：]\s*([\s\S]+)/i)
  return match?.[1]?.trim() ?? ''
}
