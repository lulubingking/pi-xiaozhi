import type { BatchQuestion, OcrBlock, QuestionLayout, QuestionLayoutPage, QuestionLayoutRegion, SourcePage } from '../api/client'
import { inferOcrBlockAssignments } from './ocr-auto-assignment'

export type QuestionLayoutDraft = {
  source_page_id: string
  source_pages: QuestionLayoutPage[]
  regions: QuestionLayoutRegion[]
  covered_question_ids: string[]
  missing_question_nos: string[]
  warnings: string[]
}

export type QuestionLayoutIssue = { code: 'invalid_bounds' | 'overlap'; message: string; region_indexes: number[] }

/**
 * A region is copied to every student page and each OCR result is assigned to
 * its question. Different questions therefore must not share a substantial
 * part of the same crop; otherwise printed text and answers leak across rows.
 */
export function questionLayoutIssues(regions: QuestionLayoutRegion[]): QuestionLayoutIssue[] {
  const issues: QuestionLayoutIssue[] = []
  regions.forEach((region, index) => {
    if (![region.x1, region.y1, region.x2, region.y2].every(Number.isFinite)
      || region.x1 < 0 || region.y1 < 0 || region.x2 > 1 || region.y2 > 1
      || region.x2 <= region.x1 || region.y2 <= region.y1) {
      issues.push({ code: 'invalid_bounds', message: `第 ${region.question_no} 题区域坐标无效。`, region_indexes: [index] })
    }
  })
  for (let leftIndex = 0; leftIndex < regions.length; leftIndex += 1) {
    const left = regions[leftIndex]
    for (let rightIndex = leftIndex + 1; rightIndex < regions.length; rightIndex += 1) {
      const right = regions[rightIndex]
      if (left.source_page_id !== right.source_page_id) continue
      const overlapWidth = Math.max(0, Math.min(left.x2, right.x2) - Math.max(left.x1, right.x1))
      const overlapHeight = Math.max(0, Math.min(left.y2, right.y2) - Math.max(left.y1, right.y1))
      const overlapArea = overlapWidth * overlapHeight
      const smallerArea = Math.min((left.x2 - left.x1) * (left.y2 - left.y1), (right.x2 - right.x1) * (right.y2 - right.y1))
      if (smallerArea > 0 && overlapArea / smallerArea > 0.15) {
        issues.push({
          code: 'overlap',
          message: left.question_id === right.question_id
            ? `第 ${left.question_no} 题的两个区域重叠过多，识别会重复答案，请重新框选。`
            : `第 ${left.question_no} 题与第 ${right.question_no} 题的区域重叠过多，请重新框选。`,
          region_indexes: [leftIndex, rightIndex],
        })
      }
    }
  }
  return issues
}

export function questionLayoutDraftFromSaved(layout: QuestionLayout, questions: BatchQuestion[]): QuestionLayoutDraft {
  const coveredQuestionIds = [...new Set(layout.regions.map((region) => region.question_id))]
  const missingQuestions = questions.filter((question) => !coveredQuestionIds.includes(question.id))
  const issues = questionLayoutIssues(layout.regions)
  return {
    source_page_id: layout.source_page_id,
    source_pages: layout.source_pages,
    regions: layout.regions.map((region) => ({ ...region, ocr_block_ids: [...region.ocr_block_ids] })),
    covered_question_ids: coveredQuestionIds,
    missing_question_nos: missingQuestions.map((question) => question.question_no),
    warnings: issues.map((issue) => issue.message),
  }
}

export function pageUsesQuestionLayout(page: SourcePage, layout: QuestionLayout | null): boolean {
  if (!layout || layout.status !== 'confirmed' || questionLayoutIssues(layout.regions).length) return false
  const layoutRun = page.transform?.layout_first
  if (!layoutRun || typeof layoutRun !== 'object') return false
  const metadata = layoutRun as Record<string, unknown>
  return metadata.layout_id === layout.id && Number(metadata.layout_version) === layout.version
}

function blocksFor(page: SourcePage): OcrBlock[] {
  return page.latest_ocr_run?.blocks ?? []
}

function number(value: string | number | null | undefined, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function pageDimensions(page: SourcePage, blocks: OcrBlock[]) {
  const sourceRender = page.transform && typeof page.transform.source_render === 'object' && page.transform.source_render
    ? page.transform.source_render as Record<string, unknown>
    : null
  const blockWidth = Math.max(...blocks.map((block) => number(block.x2)), 0)
  const blockHeight = Math.max(...blocks.map((block) => number(block.y2)), 0)
  return {
    width: Math.max(1, Math.round(number(sourceRender?.width as number | undefined, blockWidth || 1000))),
    height: Math.max(1, Math.round(number(sourceRender?.height as number | undefined, blockHeight || 1400))),
  }
}

function validBox(block: OcrBlock) {
  const x1 = number(block.x1, NaN)
  const y1 = number(block.y1, NaN)
  const x2 = number(block.x2, NaN)
  const y2 = number(block.y2, NaN)
  return Number.isFinite(x1) && Number.isFinite(y1) && Number.isFinite(x2) && Number.isFinite(y2) && x2 > x1 && y2 > y1
}

function clamp(value: number) {
  return Math.max(0, Math.min(1, value))
}

function makeRegion(question: BatchQuestion, page: SourcePage, blocks: OcrBlock[], width: number, height: number): QuestionLayoutRegion | null {
  const boxed = blocks.filter(validBox)
  if (!boxed.length) return null
  const minX = Math.min(...boxed.map((block) => number(block.x1)))
  const minY = Math.min(...boxed.map((block) => number(block.y1)))
  const maxX = Math.max(...boxed.map((block) => number(block.x2)))
  const maxY = Math.max(...boxed.map((block) => number(block.y2)))
  const padX = Math.max(12, width * 0.025)
  const padY = Math.max(18, height * 0.035)
  const confidenceValues = boxed.map((block) => number(block.confidence, 0.7)).filter((value) => value > 0)
  return {
    question_id: question.id,
    question_no: question.question_no,
    source_page_id: page.id,
    page_index: page.page_index,
    x1: clamp((minX - padX) / width),
    y1: clamp((minY - padY) / height),
    x2: clamp((maxX + padX) / width),
    y2: clamp((maxY + padY) / height),
    confidence: confidenceValues.length ? Math.min(...confidenceValues) : 0.7,
    ocr_block_ids: boxed.map((block) => block.id),
  }
}

function makeSingleQuestionFallbackRegion(question: BatchQuestion, page: SourcePage): QuestionLayoutRegion {
  // 单题作文、整页简答题和手动录入题没有可依赖的题号文本框时，
  // 用整页作为候选区域。它仍然由系统自动生成，不要求教师拖框；置信度降低并在界面中明确提示核对。
  return {
    question_id: question.id,
    question_no: question.question_no,
    source_page_id: page.id,
    page_index: page.page_index,
    x1: 0.03,
    y1: 0.03,
    x2: 0.97,
    y2: 0.97,
    confidence: 0.35,
    ocr_block_ids: [],
  }
}

/**
 * 根据题目页 OCR 的文本块和坐标生成可编辑的题目区域候选。
 * 学生答题区域必须由教师在原图上核对或调整后，才能复用于整批学生页。
 */
export function buildQuestionLayoutDraft(pages: SourcePage[], questions: BatchQuestion[]): QuestionLayoutDraft | null {
  if (!pages.length || !questions.length) return null
  const regions: QuestionLayoutRegion[] = []
  const sourcePages: QuestionLayoutPage[] = []
  // 单题无论是作文、计算、选择还是手动录入题，都可以安全使用整页候选区域；
  // 多题页面没有可靠题号时则不擅自平均切分，避免学生答案错题归属。
  const singleQuestionFallback = questions.length === 1

  pages.forEach((page) => {
    const blocks = blocksFor(page)
    const dimensions = pageDimensions(page, blocks)
    sourcePages.push({ source_page_id: page.id, page_index: page.page_index, ...dimensions })
    if (!blocks.length) {
      if (singleQuestionFallback) regions.push(makeSingleQuestionFallbackRegion(questions[0], page))
      return
    }
    // Layouts become OCR crops reused on student pages. Use the page-aware
    // assignment mode so text in another column is not included in a question's
    // crop extent before the teacher reviews it.
    const assignments = inferOcrBlockAssignments(blocks, questions, { mode: 'question_paper' })
    questions.forEach((question) => {
      const assignedIds = new Set(assignments[question.id] ?? [])
      const assignedBlocks = blocks.filter((block) => assignedIds.has(block.id))
      const region = makeRegion(question, page, assignedBlocks, dimensions.width, dimensions.height)
      if (region) regions.push(region)
    })
    if (singleQuestionFallback && !regions.some((region) => region.source_page_id === page.id)) {
      regions.push(makeSingleQuestionFallbackRegion(questions[0], page))
    }
  })

  // 同一页同一列的题目之间共享空白区，向下扩展到下一题之前，
  // 使学生手写答案也落在模板区域内，而不仅是题干印刷文字。
  sourcePages.forEach((sourcePage) => {
    const pageRegions = regions.filter((region) => region.source_page_id === sourcePage.source_page_id)
    const centers = pageRegions.map((region) => (region.x1 + region.x2) / 2).sort((left, right) => left - right)
    let splitAt = -1
    let largestGap = 0
    for (let index = 1; index < centers.length; index += 1) {
      const gap = centers[index] - centers[index - 1]
      if (gap > largestGap) {
        largestGap = gap
        splitAt = index
      }
    }
    const hasTwoColumns = splitAt > 0 && largestGap >= 0.16
    pageRegions.forEach((region) => {
      const centerX = (region.x1 + region.x2) / 2
      if (hasTwoColumns) {
        const split = (centers[splitAt - 1] + centers[splitAt]) / 2
        if (centerX < split) {
          region.x1 = Math.min(region.x1, 0.04)
          region.x2 = Math.max(region.x2, clamp(split - 0.015))
        } else {
          region.x1 = Math.min(region.x1, clamp(split + 0.015))
          region.x2 = Math.max(region.x2, 0.96)
        }
      }
      const below = pageRegions
        .filter((candidate) => candidate !== region && candidate.y1 > region.y1 && Math.abs((candidate.x1 + candidate.x2) / 2 - centerX) < 0.28)
        .sort((left, right) => left.y1 - right.y1)[0]
      // The next question is a hard boundary: when OCR grouped a neighboring
      // text block into this candidate, do not let the crop extend through it.
      const nextQuestionBoundary = below ? below.y1 - 0.012 : 0.98
      region.y2 = clamp(Math.min(region.y2, nextQuestionBoundary))
    })
  })

  const coveredQuestionIds = [...new Set(regions.map((region) => region.question_id))]
  const missingQuestions = questions.filter((question) => !coveredQuestionIds.includes(question.id))
  const warnings: string[] = []
  if (missingQuestions.length) warnings.push(`第 ${missingQuestions.map((question) => question.question_no).join('、')} 题没有形成可靠 OCR 区域，暂不能确认模板。`)
  if (!regions.length) warnings.push('没有可用的题目 OCR 坐标，请先完成题目页 OCR，或为多题页面补充清晰的题号文本。')
  if (singleQuestionFallback && regions.some((region) => region.confidence < 0.5)) warnings.push('当前题目没有可用 OCR 坐标，系统已按单题整页自动生成候选区域；请先核对题干，再确认模板。')
  warnings.push(...questionLayoutIssues(regions).map((issue) => issue.message))
  warnings.push('系统区域只是候选框。请在原图上核对实际作答位置；题干、印刷选项和相邻题区域不得归入学生答案框。')
  return {
    source_page_id: regions[0]?.source_page_id ?? sourcePages[0]?.source_page_id ?? '',
    source_pages: sourcePages,
    regions,
    covered_question_ids: coveredQuestionIds,
    missing_question_nos: missingQuestions.map((question) => question.question_no),
    warnings,
  }
}
