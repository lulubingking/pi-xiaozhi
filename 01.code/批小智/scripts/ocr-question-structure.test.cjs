const assert = require('node:assert/strict')
const fs = require('node:fs')
const test = require('node:test')
const ts = require('typescript')

require.extensions['.ts'] = (module, filename) => {
  const source = fs.readFileSync(filename, 'utf8')
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
    fileName: filename,
  }).outputText
  module._compile(output, filename)
}

const { analyzeQuestionStructure, questionPromptFromOcr } = require('../src/utils/question-structure.ts')
const { extractQuestionMarkers } = require('../src/utils/ocr-question-markers.ts')
const { inferOcrBlockAssignments, textForAssignedBlocks } = require('../src/utils/ocr-auto-assignment.ts')
const { questionPromptDraftFromSavedOrOcr, savedAnswerMatchesLegacyOcrOrder, shouldSyncAutoOcrDraft } = require('../src/utils/ocr-draft-persistence.ts')
const { buildQuestionLayoutDraft, questionLayoutIssues } = require('../src/utils/question-layout.ts')
const { studentAnswerFromOcrBlocks, studentAnswerText } = require('../src/utils/student-answer.ts')

function block(id, index, text, x1 = 100, y1 = index * 40) {
  return { id, block_index: index, text_raw: text, x1, y1, x2: x1 + 600, y2: y1 + 28, confidence: 0.9 }
}

function sameExamBlocks() {
  return [
    block('a', 1, 'A. 甲选项', 3177, 273),
    block('a-cont', 2, 'A 选项续文', 3188, 394),
    block('b', 3, 'B. 乙选项', 3182, 502),
    block('left-story', 4, '这段是前文阅读材料，不属于题干。', 272, 530),
    block('right-choice-cont', 5, '选项组续文。', 3188, 615),
    block('c', 10, 'C. 丙选项', 3188, 841),
    block('q17', 21, '17. 文中两次出现冷风，各有什么作用？', 3212, 1446),
    block('sub1', 23, '(1) 纽约的一条大街上，警察沿街走着。', 3237, 1536),
    block('answer17', 25, '运用了环境描写，写出天气寒冷。', 3219, 1590),
    block('sub2', 31, '(2) 又是一阵冷风穿街而过。', 3252, 2015),
    block('q18', 40, '18. 吉米是一个什么样的人？请结合全文简要分析。', 3244, 2515),
    block('q19', 51, '19. 故事结尾便条上的内容体现了作者讲故事的匠心，请分析巧妙之处。', 3261, 3057),
  ]
}

test('cross-column scan recovers a partial objective question and filters subquestion numbers', () => {
  const result = analyzeQuestionStructure(sameExamBlocks(), { subject: 'chinese' })
  assert.deepEqual(result.questions.map((item) => item.questionNo), ['16', '17', '18', '19'])
  assert.equal(result.questions[0].questionType, 'objective')
  assert.equal(result.questions[1].questionType, 'subjective')
  assert.equal(result.questions[2].questionType, 'subjective')
  assert.equal(result.questions[3].questionType, 'subjective')
  assert.equal(result.needsReviewCount, 1)
  assert.equal(result.questions[0].requiresPromptReview, true)
  assert.match(result.questions[0].evidence, /题干未完整识别/)
  assert.equal(questionPromptFromOcr(result.questions[0], 'A. 甲选项\nB. 乙选项\nC. 丙选项', '原有题干'), '原有题干')
  assert.equal(questionPromptFromOcr(result.questions[1], '17. 文中两次出现冷风，各有什么作用？', null), '17. 文中两次出现冷风，各有什么作用？')
})

test('objective choices override a broad reading section classification', () => {
  const result = analyzeQuestionStructure([
    block('heading', 1, '阅读理解'),
    block('q1', 2, '1. 阅读下文，选择正确的一项。'),
    block('a', 3, 'A. 第一项'),
    block('b', 4, 'B. 第二项'),
    block('c', 5, 'C. 第三项'),
    block('q2', 6, '2. 请结合全文简要分析人物形象。'),
  ], { subject: 'chinese' })
  assert.equal(result.questions[0].questionType, 'objective')
  assert.equal(result.questions[1].questionType, 'subjective')
})

test('multiple choice wording remains multiple choice when its option group is present', () => {
  const result = analyzeQuestionStructure([
    block('q1', 1, '1. 多选题：选择所有正确答案。'),
    block('a', 2, 'A. 第一项'),
    block('b', 3, 'B. 第二项'),
    block('c', 4, 'C. 第三项'),
  ], { subject: 'chinese' })
  assert.equal(result.questions[0].questionType, 'objective')
  assert.equal(result.questions[0].questionSubtype, 'multiple_choice')
})

test('circled Chinese option labels also identify objective questions', () => {
  const result = analyzeQuestionStructure([
    block('q1', 1, '1. 选择正确的一项。'),
    block('one', 2, '①第一项'),
    block('two', 3, '②第二项'),
    block('three', 4, '③第三项'),
  ], { subject: 'chinese' })
  assert.equal(result.questions[0].questionType, 'objective')
})

test('Chinese and fullwidth option labels identify objective questions', () => {
  const result = analyzeQuestionStructure([
    block('q1', 1, '1．选择正确的一项。'),
    block('one', 2, '（甲）第一项'),
    block('two', 3, '（乙）第二项'),
    block('three', 4, '（丙）第三项'),
  ], { subject: 'chinese' })
  assert.equal(result.questions[0].questionType, 'objective')
})

test('parenthesized subquestions and formula steps do not become top-level questions', () => {
  const markers = extractQuestionMarkers('17. 计算 2) x=3\n(1) 第一步\n(2) 第二步\n18. 解释结果')
  assert.deepEqual(markers.map((item) => item.questionNo), ['17', '1', '2', '18'])
  const result = analyzeQuestionStructure([
    block('q17', 1, '17. 计算并说明过程。'),
    block('formula', 2, '2) x=3'),
    block('sub1', 3, '(1) 第一步'),
    block('sub2', 4, '(2) 第二步'),
    block('q18', 5, '18. 解释结果。'),
  ], { subject: 'math' })
  assert.deepEqual(result.questions.map((item) => item.questionNo), ['17', '18'])
})

test('Chinese explicit question numbers normalize consistently', () => {
  const result = analyzeQuestionStructure([
    block('q17', 1, '第十七题：简述原因。'),
    block('q18', 2, '第十八题：分析结果。'),
  ], { subject: 'chinese' })
  assert.deepEqual(result.questions.map((item) => item.questionNo), ['17', '18'])
})

test('a paper numbered entirely with parenthesized question labels is retained', () => {
  const result = analyzeQuestionStructure([
    block('q1', 1, '(1) 选择正确的一项。'),
    block('q2', 2, '(2) 请简要分析原因。'),
  ], { subject: 'chinese' })
  assert.deepEqual(result.questions.map((item) => item.questionNo), ['1', '2'])
})

test('OCR blocks map printed numbers and leading choices onto internal batch order', () => {
  const blocks = sameExamBlocks()
  const questions = ['1', '2', '3', '4'].map((questionNo) => ({ id: `internal-${questionNo}`, question_no: questionNo }))
  const assignments = inferOcrBlockAssignments(blocks, questions, { mode: 'question_paper' })
  assert.ok(['a', 'b', 'c'].every((id) => assignments['internal-1'].includes(id)))
  assert.ok(assignments['internal-1'].includes('a-cont'))
  assert.ok(assignments['internal-2'].includes('q17'))
  assert.ok(assignments['internal-3'].includes('q18'))
  assert.ok(assignments['internal-4'].includes('q19'))
  assert.doesNotMatch(textForAssignedBlocks(blocks, assignments['internal-1']), /前文阅读材料/)
  assert.doesNotMatch(textForAssignedBlocks(blocks, assignments['internal-2']), /前文阅读材料/)
})

test('missing marker inside a numbered run still maps later questions to their matching order', () => {
  const blocks = [block('q17', 1, '17. 第一题内容'), block('between', 2, '中间 OCR 文本'), block('q19', 3, '19. 第三题内容')]
  const questions = ['1', '2', '3'].map((questionNo) => ({ id: `internal-${questionNo}`, question_no: questionNo }))
  const assignments = inferOcrBlockAssignments(blocks, questions, { mode: 'question_paper' })
  assert.ok(assignments['internal-1'].includes('q17'))
  assert.ok(assignments['internal-3'].includes('q19'))
})

test('an orphan choice group on a later page creates and maps the missing objective question', () => {
  const blocks = [
    block('q17', 100001, '17. 第一页末尾题目', 600, 500),
    block('a18', 200001, 'A. 第二页缺号题选项一', 600, 100),
    block('b18', 200002, 'B. 第二页缺号题选项二', 600, 160),
    block('c18', 200003, 'C. 第二页缺号题选项三', 600, 220),
    block('q19', 200004, '19. 第二页后续题目', 600, 300),
  ]
  const result = analyzeQuestionStructure(blocks, { subject: 'chinese' })
  assert.deepEqual(result.questions.map((item) => item.questionNo), ['17', '18', '19'])
  assert.equal(result.questions[1].questionType, 'objective')
  const questions = result.questions.map((item) => ({ id: item.questionNo, question_no: item.questionNo }))
  const assignments = inferOcrBlockAssignments(blocks, questions, { mode: 'question_paper' })
  assert.deepEqual(assignments['18'], ['a18', 'b18', 'c18'])
  assert.ok(assignments['17'].includes('q17'))
  assert.ok(assignments['19'].includes('q19'))
})

test('OCR fills an empty or placeholder prompt, then persisted teacher edits take precedence', () => {
  assert.equal(questionPromptDraftFromSavedOrOcr('', '1. 识别出的题干'), '1. 识别出的题干')
  assert.equal(questionPromptDraftFromSavedOrOcr('1', '1. 识别出的题干'), '1. 识别出的题干')
  assert.equal(questionPromptDraftFromSavedOrOcr('教师修改并保存的题干', '1. 原始 OCR 题干'), '教师修改并保存的题干')
})

test('an incomplete inferred prompt keeps its saved value instead of replacing it with option text', () => {
  assert.equal(questionPromptDraftFromSavedOrOcr('原题干', 'A. 选项一\nB. 选项二\nC. 选项三', true), '原题干')
  assert.equal(questionPromptDraftFromSavedOrOcr('', 'A. 选项一\nB. 选项二\nC. 选项三', true), '')
})

test('automatic student OCR sync preserves teacher edits and confirmed blanks', () => {
  assert.equal(shouldSyncAutoOcrDraft({ answer_text: '老师修改后的作答', is_blank_confirmed: false, source_type: 'teacher_corrected' }, '原始 OCR 答案'), false)
  assert.equal(shouldSyncAutoOcrDraft({ answer_text: '', is_blank_confirmed: true, source_type: 'teacher_corrected' }, '原始 OCR 答案'), false)
  assert.equal(shouldSyncAutoOcrDraft({ answer_text: '手动录入内容', is_blank_confirmed: false, source_type: 'manual' }, '原始 OCR 答案'), false)
  assert.equal(shouldSyncAutoOcrDraft({ answer_text: '已经确认的作答', is_blank_confirmed: false, source_type: 'ocr', coverage_status: 'reviewed' }, '新的 OCR 答案'), false)
  assert.equal(shouldSyncAutoOcrDraft({ answer_text: '原始 OCR 答案', is_blank_confirmed: false, source_type: 'ocr' }, '原始 OCR 答案'), false)
  assert.equal(shouldSyncAutoOcrDraft({ answer_text: '旧的串题文本，其中含有新的 OCR 答案', is_blank_confirmed: false, source_type: 'ocr' }, '新的 OCR 答案'), true)
  assert.equal(shouldSyncAutoOcrDraft(null, '首次识别内容'), true)
})

test('spatial reading order fixes existing OCR drafts only when they still match the old block order', () => {
  const saved = { answer_text: '下行续文\n上行开头', source_type: 'ocr', is_blank_confirmed: false, coverage_status: 'reviewed' }
  assert.equal(savedAnswerMatchesLegacyOcrOrder(saved, '下行续文\n上行开头'), true)
  assert.equal(savedAnswerMatchesLegacyOcrOrder({ ...saved, answer_text: '老师修改过的文本' }, '下行续文\n上行开头'), false)
  assert.equal(savedAnswerMatchesLegacyOcrOrder({ ...saved, source_type: 'teacher_corrected' }, '下行续文\n上行开头'), false)
  assert.equal(savedAnswerMatchesLegacyOcrOrder({ ...saved, is_blank_confirmed: true }, '下行续文\n上行开头'), false)
})

test('student OCR answer follows visible line coordinates when model block indexes are reversed', () => {
  const blocks = [
    { block_index: 13, text_raw: '第二行续文', x1: '3259', y1: '3263', x2: '5749', y2: '3557' },
    { block_index: 14, text_raw: '第一行开头', x1: '3294', y1: '3131', x2: '5778', y2: '3400' },
    { block_index: 15, text_raw: '第三行', x1: '3259', y1: '3398', x2: '5795', y2: '3716' },
    { block_index: 16, text_raw: '第四行', x1: '3285', y1: '3629', x2: '4361', y2: '3827' },
  ]
  assert.equal(studentAnswerFromOcrBlocks('subjective', blocks), '第一行开头\n第二行续文\n第三行\n第四行')
  assert.equal(studentAnswerFromOcrBlocks('subjective', blocks, true), '第二行续文\n第一行开头\n第三行\n第四行')
})

test('assigned question text is displayed in clear two-column reading order', () => {
  const blocks = [
    { id: 'title', block_index: 1, text_raw: '页面标题', x1: '0', y1: '0', x2: '1000', y2: '40' },
    { id: 'left-1', block_index: 2, text_raw: '左栏第一行', x1: '50', y1: '100', x2: '400', y2: '130' },
    { id: 'right-1', block_index: 3, text_raw: '右栏第一行', x1: '600', y1: '100', x2: '950', y2: '130' },
    { id: 'left-2', block_index: 4, text_raw: '左栏第二行', x1: '50', y1: '200', x2: '400', y2: '230' },
    { id: 'right-2', block_index: 5, text_raw: '右栏第二行', x1: '600', y1: '200', x2: '950', y2: '230' },
  ]
  assert.equal(textForAssignedBlocks(blocks, blocks.map((item) => item.id)), '页面标题\n左栏第一行\n左栏第二行\n右栏第一行\n右栏第二行')
})

test('student answer layout uses page-aware question regions without importing the opposite column', () => {
  const questions = ['16', '17', '18', '19'].map((questionNo, index) => ({
    id: `question-${questionNo}`,
    question_no: questionNo,
    question_type: index === 0 ? 'objective' : 'subjective',
  }))
  const page = {
    id: 'paper-page',
    page_index: 1,
    transform: { source_render: { width: 5712, height: 4284 } },
    latest_ocr_run: { blocks: sameExamBlocks() },
  }
  const draft = buildQuestionLayoutDraft([page], questions)
  assert.ok(draft)
  assert.deepEqual(draft.missing_question_nos, [])
  assert.deepEqual(questionLayoutIssues(draft.regions), [])
  const firstQuestionRegion = draft.regions.find((region) => region.question_id === 'question-16')
  const lastQuestionRegion = draft.regions.find((region) => region.question_id === 'question-19')
  assert.ok(firstQuestionRegion)
  assert.ok(lastQuestionRegion)
  assert.ok(firstQuestionRegion.x1 > 0.45, 'the left-page reading passage must not widen the first question crop')
  assert.ok(lastQuestionRegion.x1 > 0.45, 'the last question must not inherit text from the opposite page column')
})

test('objective answers return only a unique standalone choice label', () => {
  assert.equal(studentAnswerText('objective', 'D'), 'D')
  assert.equal(studentAnswerText('objective', '（A）'), 'A')
  assert.equal(studentAnswerText('objective', '答案：C'), 'C')
  assert.equal(studentAnswerText('objective', 'A. 第一项\nB. 第二项\nC. 第三项\nD. 第四项'), '')
  assert.equal(studentAnswerText('objective', '答案可能是 A 或 D'), '')
  assert.equal(studentAnswerText('subjective', '写出完整解答'), '写出完整解答')
  assert.equal(studentAnswerFromOcrBlocks('objective', [
    { block_index: 1, text_raw: '一项是（', confidence: '0.98' },
    { block_index: 2, text_raw: 'D', confidence: '0.99' },
  ]), 'D')
  assert.equal(studentAnswerFromOcrBlocks('objective', [
    { block_index: 1, text_raw: 'A', confidence: '0.98' },
    { block_index: 2, text_raw: 'B', confidence: '0.99' },
  ]), '')
})
