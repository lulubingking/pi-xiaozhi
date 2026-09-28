import { useEffect, useMemo, useRef, useState, type ComponentType, type FormEvent } from 'react'
import { ApiClientError, api, type AppSettings, type AssignmentBatch, type BatchQuestionInput, type ClassRoom, type GradingRun, type OcrBlock, type QuestionLayout, type ReportStudentRow, type ReviewHistory, type ReviewItem, type Rubric, type RubricVersion, type SessionInfo, type SourceFile, type SourcePage, type UploadTask } from './api/client'
import { analyzeQuestionStructure, isQuestionStructureNeedsReview, questionPromptFromOcr, questionSubtypeLabels, type QuestionStructureResult, type QuestionStructureSubject } from './utils/question-structure'
import { answerTextFromOcr, inferOcrBlockAssignments, textForAssignedBlocks } from './utils/ocr-auto-assignment'
import { buildQuestionLayoutDraft, pageUsesQuestionLayout, questionLayoutDraftFromSaved, questionLayoutIssues, type QuestionLayoutDraft } from './utils/question-layout'
import { QuestionLayoutEditor } from './components/QuestionLayoutEditor'
import { parseRubricInput } from './utils/rubric-input'
import { questionPromptDraftFromSavedOrOcr, savedAnswerMatchesLegacyOcrOrder, shouldSyncAutoOcrDraft } from './utils/ocr-draft-persistence'
import { studentAnswerFromOcrBlocks, studentAnswerText } from './utils/student-answer'
import { sortOcrBlocksReadingOrder } from './utils/ocr-reading-order'
import {
  ArrowRight,
  ArrowUpRight,
  ArrowsClockwise,
  Bell,
  BookOpen,
  CaretDown,
  CaretRight,
  Check,
  CheckCircle,
  ChartBar,
  CheckSquare,
  Clock,
  CloudArrowUp,
  Database,
  DotsThree,
  Eye,
  FileText,
  Gear,
  House,
  Info,
  List,
  MagicWand,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  Scan,
  ShieldCheck,
  SignOut,
  Sparkle,
  Stack,
  Timer,
  TrashSimple,
  UploadSimple,
  UserPlus,
  UsersThree,
  WarningCircle,
  X,
} from '@phosphor-icons/react'

type PageKey = 'workbench' | 'assignments' | 'ocr' | 'grading' | 'rubrics' | 'reports' | 'settings'
type IconType = ComponentType<{ size?: number; weight?: 'regular' | 'bold' | 'fill' | 'duotone'; className?: string; 'aria-hidden'?: boolean | 'true' | 'false' }>

type Batch = {
  id: string
  title: string
  className: string
  subject: string
  updated: string
  statusCode: string
  status: string
  progress: number | null
  count: number | null
  exceptions: number | null
}

type BatchFormState = {
  title: string
  classId: string
  subject: 'chinese' | 'math' | 'english'
  totalScore: string
  questions: BatchQuestionInput[]
}

type OcrRecognitionMode = 'printed' | 'chinese_handwriting' | 'english_handwriting' | 'math_handwriting'

function getOcrBlocks(page: SourcePage | null | undefined): OcrBlock[] {
  const latest = page?.latest_ocr_run
  if (latest?.blocks?.length) return latest.blocks
  const detailRun = page?.ocr_runs?.find((run) => run.id === latest?.id && run.blocks?.length)
  return detailRun?.blocks ?? []
}

function getOrderedOcrBlocks(pages: SourcePage[]): OcrBlock[] {
  return pages.flatMap((page) => getOcrBlocks(page).map((block) => ({
    ...block,
    // block_index 在每个页面内从 1 重新开始；跨页分析时先编码页面顺序，避免题干拼接交错。
    block_index: page.page_index * 100000 + Number(block.block_index),
  })))
}

function hasMatchingFileContent(questionPaperFiles: SourceFile[], studentWorkFiles: SourceFile[]): boolean {
  const studentHashes = new Set(studentWorkFiles.map((file) => file.sha256).filter(Boolean))
  return questionPaperFiles.some((file) => Boolean(file.sha256) && studentHashes.has(file.sha256))
}

function recommendedStudentOcrMode(subject: string | undefined): OcrRecognitionMode {
  if (subject === 'english') return 'english_handwriting'
  if (subject === 'math') return 'math_handwriting'
  if (subject === 'chinese') return 'chinese_handwriting'
  return 'printed'
}

function recommendedQuestionPaperOcrMode(subject: string | undefined): OcrRecognitionMode {
  return subject === 'math' ? 'math_handwriting' : 'printed'
}

const defaultBatchForm: BatchFormState = {
  title: '',
  classId: '',
  subject: 'english',
  totalScore: '100',
  questions: [{ question_no: '1', question_type: 'objective', max_score: '100', question_prompt: null, reference_answer: '', rubric_version_id: null }],
}

const subjectLabels: Record<BatchFormState['subject'], string> = { chinese: '语文', math: '数学', english: '英语' }
const subjectApiValues: Record<string, 'chinese' | 'math' | 'english'> = { 语文: 'chinese', 数学: 'math', 英语: 'english' }

function displaySubject(subject: string | undefined) {
  return subjectLabels[subject as BatchFormState['subject']] ?? subject ?? '未选择学科'
}

// 工作台和四段业务流程之外，保留评分标准和模型配置入口：它们是批改流程的必要配置，
// 但不会插入上传、OCR、批改、报告的线性步骤。
const navItems: { key: PageKey; label: string; icon: IconType }[] = [
  { key: 'workbench', label: '工作台', icon: House },
  { key: 'assignments', label: '批次与上传', icon: Stack },
  { key: 'ocr', label: '题干与 OCR', icon: Scan },
  { key: 'grading', label: '学生批改', icon: CheckSquare },
  { key: 'reports', label: '评分与报告', icon: ChartBar },
  { key: 'rubrics', label: '评分标准', icon: BookOpen },
  { key: 'settings', label: '模型配置', icon: Gear },
]

const batchStatusLabels: Record<string, string> = {
  draft: '待上传',
  uploading: '上传中',
  pending_upload: '待上传',
  pending_ocr: '待识别',
  ocr_processing: '识别中',
  pending_correction: '待校对',
  pending_grading: '待批改',
  grading: '批改中',
  pending_review: '待复核',
  reviewed: '已复核',
  partial_failure: '部分失败',
  archived: '已归档',
}

const batchPurgeStatuses = new Set(['uploading', 'pending_ocr', 'ocr_processing', 'pending_correction', 'partial_failure'])
const batchEditableStatuses = new Set(['draft', 'pending_correction'])

function canPurgeBatch(status: string): boolean {
  return batchPurgeStatuses.has(status)
}

function assignmentBatchToRow(value: AssignmentBatch): Batch {
  return {
    id: value.id,
    title: value.title,
    className: value.class_name || value.class_id,
    subject: subjectLabels[value.subject as BatchFormState['subject']] || value.subject,
    updated: value.updated_at,
    statusCode: value.status,
    status: batchStatusLabels[value.status] || value.status,
    progress: null,
    count: null,
    exceptions: null,
  }
}

function App() {
  const [authState, setAuthState] = useState<'loading' | 'authenticated' | 'unauthenticated'>('loading')
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [authError, setAuthError] = useState('')
  const [activePage, setActivePage] = useState<PageKey>('workbench')
  const [ocrBatchId, setOcrBatchId] = useState<string | null>(null)
  const [gradingBatchId, setGradingBatchId] = useState<string | null>(null)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [showBatchModal, setShowBatchModal] = useState(false)
  const [editingBatchId, setEditingBatchId] = useState<string | null>(null)
  const [editingBatchVersion, setEditingBatchVersion] = useState(1)
  const [classModalMode, setClassModalMode] = useState<'create' | 'manage' | null>(null)
  const [showRubricModal, setShowRubricModal] = useState(false)
  const [batches, setBatches] = useState<Batch[]>([])
  const [batchDataError, setBatchDataError] = useState('')
  const [batchDataLoading, setBatchDataLoading] = useState(true)
  const [batchRetry, setBatchRetry] = useState(0)
  const [toast, setToast] = useState('')
  const [reportMode, setReportMode] = useState<'reviewed' | 'ai'>('reviewed')
  const [globalSearch, setGlobalSearch] = useState('')
  const [apiKeyVisible, setApiKeyVisible] = useState(false)
  const [batchForm, setBatchForm] = useState<BatchFormState>(defaultBatchForm)
  const [batchErrors, setBatchErrors] = useState<Record<string, string>>({})
  const [batchSubmitting, setBatchSubmitting] = useState(false)
  const [batchSubmitStage, setBatchSubmitStage] = useState('保存中…')
  const [deletingBatchId, setDeletingBatchId] = useState<string | null>(null)
  const [batchPurgeCandidate, setBatchPurgeCandidate] = useState<Batch | null>(null)
  const [batchPurgeError, setBatchPurgeError] = useState('')
  const [purgingBatchId, setPurgingBatchId] = useState<string | null>(null)
  const [reviewedDeleteCandidate, setReviewedDeleteCandidate] = useState<Batch | null>(null)
  const [reviewedDeleteError, setReviewedDeleteError] = useState('')
  const [deletingReviewedBatchId, setDeletingReviewedBatchId] = useState<string | null>(null)
  const [rubricForm, setRubricForm] = useState({ name: '', subject: '英语', score: '15', points: '', examples: '' })
  const [rubricFormError, setRubricFormError] = useState('')
  const [rubricSubmitting, setRubricSubmitting] = useState(false)
  const [rubricRevision, setRubricRevision] = useState(0)

  useEffect(() => {
    let cancelled = false
    const restoreSession = async () => {
      try {
        const response = await api.auth.session()
        if (cancelled) return
        setSession(response.data)
        setBatchDataLoading(response.data.authenticated)
        setAuthState(response.data.authenticated ? 'authenticated' : 'unauthenticated')
      } catch (cause) {
        if (cancelled) return
        setAuthState('unauthenticated')
        setAuthError(cause instanceof ApiClientError ? cause.message : '无法连接认证服务，请确认后端 API 已启动。')
      }
    }
    void restoreSession()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (authState !== 'authenticated') return
    let cancelled = false
    const loadBatches = async () => {
      setBatchDataLoading(true)
      setBatchDataError('')
      setBatches((current) => current.length ? [] : current)
      try {
        const response = await api.batches.list({ page_size: 100 })
        if (cancelled) return
        setBatches(response.data.map(assignmentBatchToRow))
        setBatchDataError('')
      } catch (cause) {
        if (cancelled) return
        setBatches([])
        setBatchDataError(cause instanceof ApiClientError ? cause.message : '无法加载真实批次数据，请刷新后重试。')
      } finally {
        if (!cancelled) setBatchDataLoading(false)
      }
    }
    void loadBatches()
    return () => { cancelled = true }
  }, [authState, batchRetry])

  const currentLabel = navItems.find((item) => item.key === activePage)?.label ?? '工作台'

  const navigate = (page: PageKey) => {
    setActivePage(page)
    setMobileNavOpen(false)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const navigateToOcr = (batchId?: string) => {
    if (batchId) setOcrBatchId(batchId)
    navigate('ocr')
  }

  const navigateToBatch = (batchId: string, status: string) => {
    const page = batchPage(status)
    if (page === 'ocr') {
      navigateToOcr(batchId)
      return
    }
    if (page === 'grading') setGradingBatchId(batchId)
    navigate(page)
  }

  const submitGlobalSearch = (event: FormEvent) => {
    event.preventDefault()
    if (!globalSearch.trim()) return
    navigate('assignments')
  }

  const notify = (message: string) => {
    setToast(message)
    window.setTimeout(() => setToast(''), 3600)
  }

  const handleLogin = async (email: string, password: string, rememberMe: boolean) => {
    setAuthError('')
    try {
      const response = await api.auth.login({ email, password, remember_me: rememberMe })
      setBatchDataLoading(true)
      setSession({ authenticated: true, user: response.data.user, expires_at: response.data.expires_at })
      setAuthState('authenticated')
    } catch (cause) {
      setAuthError(cause instanceof ApiClientError ? cause.message : '登录请求失败，请检查网络或后端服务。')
      throw cause
    }
  }

  const handleRegister = async (displayName: string, email: string, password: string) => {
    setAuthError('')
    try {
      const response = await api.auth.register({ display_name: displayName, email, password })
      setBatchDataLoading(true)
      setSession({ authenticated: true, user: response.data.user, expires_at: response.data.expires_at })
      setAuthState('authenticated')
    } catch (cause) {
      setAuthError(cause instanceof ApiClientError ? cause.message : '注册请求失败，请检查网络或后端服务。')
      throw cause
    }
  }

  const handleLogout = async () => {
    try {
      await api.auth.logout()
    } catch (cause) {
      setAuthError(cause instanceof ApiClientError ? cause.message : '退出登录请求失败，已清理当前页面会话。')
    } finally {
      setSession(null)
      setAuthState('unauthenticated')
      setActivePage('workbench')
      setOcrBatchId(null)
      setGradingBatchId(null)
      setBatches([])
      setBatchDataError('')
      setBatchDataLoading(false)
    }
  }

  const retryBatchData = () => setBatchRetry((current) => current + 1)

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (!batchSubmitting) setShowBatchModal(false)
        if (!rubricSubmitting) setShowRubricModal(false)
        setMobileNavOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  if (authState === 'loading') return <AuthLoading />
  if (authState !== 'authenticated') return <LoginPage error={authError} onLogin={handleLogin} onRegister={handleRegister} onClearError={() => setAuthError('')} />

  const teacherName = session?.user?.display_name || '教师'

  const openCreateBatch = () => {
    setEditingBatchId(null)
    setEditingBatchVersion(1)
    setBatchForm(defaultBatchForm)
    setBatchErrors({})
    setShowBatchModal(true)
  }

  const openCreateClass = () => setClassModalMode('create')
  const openManageClass = () => setClassModalMode('manage')

  const openEditBatch = async (batchId: string) => {
    try {
      const response = await api.batches.get(batchId)
      setEditingBatchId(response.data.id)
      setEditingBatchVersion(response.data.version)
      setBatchForm({
        title: response.data.title,
        classId: response.data.class_id,
        subject: response.data.subject as BatchFormState['subject'],
        totalScore: response.data.total_score,
        questions: response.data.questions.map((question) => ({
          question_no: question.question_no,
          question_type: question.question_type as 'objective' | 'subjective',
          max_score: question.max_score,
          question_prompt: question.question_prompt,
          reference_answer: question.reference_answer ?? '',
          rubric_version_id: question.rubric_version_id,
        })),
      })
      setBatchErrors({})
      setShowBatchModal(true)
    } catch (cause) {
      notify(cause instanceof ApiClientError ? cause.message : '批次详情加载失败，暂时不能编辑。')
    }
  }

  const handleBatchSubmit = async (event: FormEvent, saveMode: 'draft' | 'upload', questionPaperFile: File | null) => {
    event.preventDefault()
    const errors: Record<string, string> = {}
    if (!batchForm.title.trim()) errors.title = '请填写批次名称'
    if (!batchForm.classId.trim()) errors.classId = '请选择一个启用中的班级'
    const totalScore = Number(batchForm.totalScore)
    if (!batchForm.totalScore.trim() || !Number.isFinite(totalScore) || totalScore <= 0) errors.totalScore = '请输入大于 0 的批次总分'
    if (!batchForm.questions.length) errors.questions = '至少配置一道题目'
    const questionNumbers = batchForm.questions.map((item) => item.question_no.trim())
    if (questionNumbers.some((value) => !value)) errors.questions = '每道题都必须填写题号'
    if (new Set(questionNumbers).size !== questionNumbers.length) errors.questions = '题号不能重复'
    const questionScore = batchForm.questions.reduce((sum, item) => sum + (Number.isFinite(Number(item.max_score)) ? Number(item.max_score) : 0), 0)
    if (Number.isFinite(totalScore) && Math.abs(questionScore - totalScore) > 0.0001) errors.totalScore = `题目分值合计为 ${questionScore}，必须等于批次总分`
    batchForm.questions.forEach((item, index) => {
      if (!item.max_score || !Number.isFinite(Number(item.max_score)) || Number(item.max_score) <= 0) errors[`question_${index}`] = '单题分值必须大于 0'
      if (saveMode === 'upload' && item.question_type === 'objective' && !item.reference_answer?.trim() && !questionPaperFile) errors[`question_${index}`] = '客观题必须填写参考答案；如果由图片识别，请在识别后补充'
      if (saveMode === 'upload' && !questionPaperFile && !item.question_prompt?.trim()) errors[`question_${index}`] = '未上传题目图片时，请直接录入题目文本'
    })
    if (Object.keys(errors).length > 0) {
      setBatchErrors({
        ...errors,
        form: saveMode === 'draft'
          ? '草稿未保存，请检查批次名称、班级、总分和题目分值；草稿可以暂不填写题干、参考答案或评分标准。'
          : '暂时不能继续，请检查弹窗中标红的字段后重试。',
      })
      notify(saveMode === 'draft' ? '草稿暂未保存，请检查批次名称、班级、总分和题目分值。' : '请检查弹窗中标红的字段后重试。')
      return
    }
    setBatchErrors({})
    setBatchSubmitting(true)
    setBatchSubmitStage(questionPaperFile ? (isQuestionPaperImage(questionPaperFile) ? '正在上传并识别题目…' : '正在上传题干/答案文件…') : '保存中…')
    const wasEditing = Boolean(editingBatchId)
    try {
      const created = editingBatchId
        ? (await api.batches.replaceQuestions(editingBatchId, {
          questions: batchForm.questions,
          version: (await api.batches.update(editingBatchId, { title: batchForm.title.trim(), class_id: batchForm.classId, subject: batchForm.subject, total_score: batchForm.totalScore, version: editingBatchVersion })).data.version,
        })).data
        : (await api.batches.create({ title: batchForm.title.trim(), class_id: batchForm.classId, subject: batchForm.subject, total_score: batchForm.totalScore, questions: batchForm.questions, save_mode: saveMode })).data
      const newBatch: Batch = {
        id: created.id,
        title: created.title,
        className: created.class_name || '未命名班级',
        subject: subjectLabels[created.subject as BatchFormState['subject']] || created.subject,
        updated: '刚刚保存',
        statusCode: created.status,
        status: created.status === 'draft' ? '待上传' : created.status,
        progress: null,
        count: null,
        exceptions: null,
      }
      setBatches((current) => [newBatch, ...current.filter((item) => item.id !== newBatch.id)])
      let structureResult: QuestionStructureResult | null = null
      if (questionPaperFile) {
        try {
          await api.batches.uploadQuestionPaper(created.id, questionPaperFile, createIdempotencyKey())
        } catch (cause) {
          setEditingBatchId(created.id)
          setEditingBatchVersion(created.version)
          setBatchErrors({ form: `批次已保存，但题干/答案图片上传失败：${cause instanceof ApiClientError ? cause.message : '请检查文件格式和后端服务后重试。'}` })
          return
        }
        if (isQuestionPaperImage(questionPaperFile)) {
          try {
            structureResult = await recognizeQuestionPaperStructure(created.id, created.questions.length === 1 ? created.questions[0].question_no : undefined, created.subject as QuestionStructureSubject)
          } catch (cause) {
            setShowBatchModal(false)
            setEditingBatchId(null)
            setBatchErrors({})
            setOcrBatchId(created.id)
            notify(`题干/答案图片已上传，但自动题目识别未完成：${cause instanceof ApiClientError ? cause.message : '请在 OCR 校对页重试。'}`)
            navigateToOcr(created.id)
            return
          }
        }
      }
      setShowBatchModal(false)
      setEditingBatchId(null)
      setEditingBatchVersion(created.version)
      setBatchForm(defaultBatchForm)
      setBatchErrors({})
      if (questionPaperFile) {
        notify(structureResult?.questions.length ? `已识别 ${structureResult.questions.length} 道题，请在 OCR 页面确认题目数量后再生成题目` : '批次已保存，题干/答案图片已上传，请继续预处理、区域提取和 OCR 校对')
        navigateToOcr(created.id)
      } else {
        notify(wasEditing ? '批次草稿已更新，原文件和页面关联保持不变' : saveMode === 'upload' ? '批次已保存，请继续上传学生作业文件' : '批次草稿已保存')
        navigate(saveMode === 'upload' ? 'assignments' : 'assignments')
      }
    } catch (cause) {
      const message = cause instanceof ApiClientError ? cause.message : '批次保存失败，请检查后端服务后重试。'
      setBatchErrors({ form: message })
    } finally {
      setBatchSubmitting(false)
      setBatchSubmitStage('保存中…')
    }
  }

  const handleDeleteBatch = async (batch: Batch) => {
    if (batch.statusCode !== 'draft') {
      notify('只有未上传文件的草稿批次可以删除。')
      return
    }
    if (!window.confirm(`确定删除“${batch.title}”？\n\n仅会删除未上传文件的草稿批次，删除后无法恢复。`)) return
    setDeletingBatchId(batch.id)
    try {
      await api.batches.remove(batch.id)
      setBatches((current) => current.filter((item) => item.id !== batch.id))
      notify(`批次“${batch.title}”已删除`)
    } catch (cause) {
      notify(cause instanceof ApiClientError ? cause.message : '批次删除失败，请稍后重试。')
    } finally {
      setDeletingBatchId(null)
    }
  }

  const openBatchPurge = (batch: Batch) => {
    setBatchPurgeCandidate(batch)
    setBatchPurgeError('')
  }

  const handlePurgeBatch = async (batch: Batch, confirmName: string) => {
    if (confirmName !== batch.title) {
      setBatchPurgeError(`请输入与批次名称完全一致的文字：“${batch.title}”。`)
      return
    }
    setPurgingBatchId(batch.id)
    setBatchPurgeError('')
    try {
      const response = await api.batches.purge(batch.id, confirmName)
      setBatches((current) => current.filter((item) => item.id !== batch.id))
      setBatchPurgeCandidate(null)
      notify(`错误批次“${batch.title}”已撤销 OCR 并删除（${response.data.deleted_files} 个文件，${response.data.deleted_pages} 页）`)
    } catch (cause) {
      setBatchPurgeError(cause instanceof ApiClientError ? cause.message : '批次撤销失败，请稍后重试。')
    } finally {
      setPurgingBatchId(null)
    }
  }

  const openReviewedBatchDelete = (batch: Batch) => {
    setReviewedDeleteCandidate(batch)
    setReviewedDeleteError('')
  }

  const handleDeleteReviewedBatch = async (batch: Batch, password: string, confirmName: string) => {
    if (confirmName !== batch.title) {
      setReviewedDeleteError(`请输入与批次名称完全一致的文字：“${batch.title}”。`)
      return
    }
    setDeletingReviewedBatchId(batch.id)
    setReviewedDeleteError('')
    try {
      const response = await api.batches.deleteReviewed(batch.id, { password, confirm_name: confirmName })
      setBatches((current) => current.filter((item) => item.id !== batch.id))
      setReviewedDeleteCandidate(null)
      notify(`已物理删除批次“${batch.title}”（${response.data.deleted_files} 个文件，${response.data.deleted_pages} 页）`)
    } catch (cause) {
      setReviewedDeleteError(cause instanceof ApiClientError ? cause.message : '批次物理删除失败，请稍后重试。')
    } finally {
      setDeletingReviewedBatchId(null)
    }
  }

  const handleRubricSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setRubricFormError('')
    if (!rubricForm.name.trim() || !rubricForm.points.trim()) {
      setRubricFormError('请补充评分标准名称和给大模型使用的评分说明。')
      return
    }
    const parsed = parseRubricInput(rubricForm.points, rubricForm.score)
    if (parsed.error) {
      setRubricFormError(parsed.error)
      return
    }
    const validPoints = parsed.points
    setRubricSubmitting(true)
    try {
      const examples = rubricForm.examples.split(/\r?\n/).map((content) => content.trim()).filter(Boolean).map((content) => ({ content }))
      await api.rubrics.create({ name: rubricForm.name.trim(), subject: subjectApiValues[rubricForm.subject] || 'english', question_type: 'subjective', total_score: rubricForm.score, points: validPoints, examples })
      setShowRubricModal(false)
      setRubricForm({ name: '', subject: '英语', score: '15', points: '', examples: '' })
      setRubricRevision((value) => value + 1)
      notify('评分标准已保存为新版本；如需用于主观题，请在批次中绑定对应版本。')
    } catch (cause) {
      const message = cause instanceof ApiClientError ? cause.message : '评分标准保存失败，请检查评分说明和分值后重试。'
      setRubricFormError(message)
      notify(message)
    } finally {
      setRubricSubmitting(false)
    }
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <aside className={`sidebar ${mobileNavOpen ? 'sidebar-open' : ''}`} aria-label="主导航">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><MagicWand size={22} weight="bold" /></div>
          <div>
            <div className="brand-name">批小智</div>
            <div className="brand-caption">AI 作业批改工作台</div>
          </div>
          <button className="icon-button mobile-close" type="button" aria-label="关闭导航" onClick={() => setMobileNavOpen(false)}><X size={20} /></button>
        </div>

        <div className="sidebar-section-label">工作空间</div>
        <nav className="primary-nav">
          {navItems.map((item) => {
            const Icon = item.icon
            return (
              <button key={item.key} className={`nav-item ${item.key === 'workbench' ? 'nav-item-home' : ''} ${activePage === item.key ? 'active' : ''}`} type="button" aria-current={activePage === item.key ? 'page' : undefined} onClick={() => navigate(item.key)}>
                <Icon size={20} weight={activePage === item.key ? 'bold' : 'regular'} aria-hidden="true" />
                <span>{item.label}</span>
              </button>
            )
          })}
        </nav>

        <div className="sidebar-bottom">
          <div className="privacy-card">
            <div className="privacy-icon"><ShieldCheck size={19} weight="duotone" aria-hidden="true" /></div>
            <div>
              <strong>教师复核保护</strong>
              <p>AI 结果仅作建议</p>
            </div>
          </div>
          <div className="profile-card" aria-label="当前教师账号">
            <div className="avatar">{teacherName.slice(0, 1)}</div>
            <div className="profile-copy"><strong>{teacherName}</strong><span>{session?.user?.email || '个人教师'}</span></div>
            <DotsThree size={20} aria-hidden="true" />
          </div>
          <button className="logout-button" type="button" onClick={() => void handleLogout()}><SignOut size={17} aria-hidden="true" />退出登录</button>
        </div>
      </aside>

      {mobileNavOpen && <button className="mobile-scrim" type="button" aria-label="关闭导航" onClick={() => setMobileNavOpen(false)} />}

      <div className="main-column">
        <header className="topbar">
          <div className="topbar-left">
            <button className="icon-button menu-trigger" type="button" aria-label="打开导航" aria-expanded={mobileNavOpen} onClick={() => setMobileNavOpen(true)}><List size={22} /></button>
            <div className="breadcrumbs"><span>批小智</span><CaretRight size={13} aria-hidden="true" /><strong>{currentLabel}</strong></div>
          </div>
          <div className="topbar-actions">
            <form className="global-search" role="search" onSubmit={submitGlobalSearch}><MagnifyingGlass size={18} aria-hidden="true" /><label className="sr-only" htmlFor="global-search-input">搜索批次、班级或学科</label><input id="global-search-input" type="search" value={globalSearch} onChange={(event) => setGlobalSearch(event.target.value)} placeholder="搜索批次、班级或学科" /></form>
            <div className="class-shortcuts" aria-label="班级快捷操作">
              <button className="topbar-class-button topbar-class-button-primary" type="button" onClick={openCreateClass}><Plus size={16} weight="bold" aria-hidden="true" />创建班级</button>
              <button className="topbar-class-button" type="button" onClick={openManageClass}><UsersThree size={16} weight="duotone" aria-hidden="true" />管理班级</button>
            </div>
            <button className="icon-button notification-button" type="button" aria-label="进入智能批改查看待复核任务" onClick={() => navigate('grading')}><Bell size={20} /></button>
            <div className="topbar-avatar" aria-hidden="true">{teacherName.slice(0, 1)}</div>
          </div>
        </header>

        <main id="main-content" className="main-content" tabIndex={-1}>
          {activePage === 'workbench' && <Workbench teacherName={teacherName} navigate={navigate} onOpenBatch={navigateToBatch} batches={batches} batchDataError={batchDataError} batchDataLoading={batchDataLoading} onRetryBatches={retryBatchData} onCreate={openCreateBatch} notify={notify} />}
          {activePage === 'assignments' && <Assignments batches={batches} batchDataError={batchDataError} batchDataLoading={batchDataLoading} onRetryBatches={retryBatchData} globalSearch={globalSearch} onCreate={openCreateBatch} onEditBatch={openEditBatch} onDeleteBatch={handleDeleteBatch} deletingBatchId={deletingBatchId} onPurgeBatch={openBatchPurge} purgingBatchId={purgingBatchId} onDeleteReviewedBatch={openReviewedBatchDelete} deletingReviewedBatchId={deletingReviewedBatchId} navigate={navigate} onOpenBatch={navigateToBatch} notify={notify} />}
          {activePage === 'ocr' && <OcrReview notify={notify} initialBatchId={ocrBatchId ?? undefined} />}
          {activePage === 'grading' && <GradingReview notify={notify} initialBatchId={gradingBatchId ?? undefined} />}
          {activePage === 'rubrics' && <Rubrics onCreate={() => { setRubricFormError(''); setShowRubricModal(true) }} notify={notify} refreshToken={rubricRevision} />}
          {activePage === 'reports' && <Reports mode={reportMode} setMode={setReportMode} notify={notify} navigate={navigate} />}
          {activePage === 'settings' && <Settings apiKeyVisible={apiKeyVisible} setApiKeyVisible={setApiKeyVisible} notify={notify} />}
        </main>
      </div>

      {toast && <div className="toast" role="status" aria-live="polite"><CheckCircle size={20} weight="fill" aria-hidden="true" /><span>{toast}</span><button type="button" aria-label="关闭提示" onClick={() => setToast('')}><X size={16} /></button></div>}
      {classModalMode && <ClassManagement mode={classModalMode} notify={notify} onClose={() => setClassModalMode(null)} />}
      {showBatchModal && <BatchModal editing={Boolean(editingBatchId)} form={batchForm} setForm={setBatchForm} errors={batchErrors} submitting={batchSubmitting} submittingMessage={batchSubmitStage} onClose={() => { if (!batchSubmitting) { setShowBatchModal(false); setEditingBatchId(null) } }} onSubmit={handleBatchSubmit} />}
      {showRubricModal && <RubricModal form={rubricForm} setForm={setRubricForm} submitting={rubricSubmitting} error={rubricFormError} onClose={() => { setRubricFormError(''); setShowRubricModal(false) }} onSubmit={handleRubricSubmit} />}
      {batchPurgeCandidate && <BatchPurgeModal batch={batchPurgeCandidate} error={batchPurgeError} submitting={purgingBatchId === batchPurgeCandidate.id} onClose={() => { if (!purgingBatchId) { setBatchPurgeCandidate(null); setBatchPurgeError('') } }} onConfirm={(confirmName) => void handlePurgeBatch(batchPurgeCandidate, confirmName)} />}
      {reviewedDeleteCandidate && <ReviewedBatchDeleteModal batch={reviewedDeleteCandidate} error={reviewedDeleteError} submitting={deletingReviewedBatchId === reviewedDeleteCandidate.id} onClose={() => { if (!deletingReviewedBatchId) { setReviewedDeleteCandidate(null); setReviewedDeleteError('') } }} onConfirm={(password, confirmName) => void handleDeleteReviewedBatch(reviewedDeleteCandidate, password, confirmName)} />}
    </div>
  )
}

function AuthLoading() {
  return <main className="auth-shell" aria-live="polite"><section className="auth-card auth-loading"><div className="brand-mark" aria-hidden="true"><MagicWand size={22} weight="bold" /></div><strong>正在恢复教师会话</strong><span>请稍候，系统正在检查服务端登录状态。</span></section></main>
}

function LoginPage({ error, onLogin, onRegister, onClearError }: { error: string; onLogin: (email: string, password: string, rememberMe: boolean) => Promise<void>; onRegister: (displayName: string, email: string, password: string) => Promise<void>; onClearError: () => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [rememberMe, setRememberMe] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [localError, setLocalError] = useState('')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!email.trim() || !password || (mode === 'register' && !displayName.trim())) {
      setLocalError(mode === 'register' ? '请填写教师姓名、邮箱和密码。' : '请输入邮箱和密码。')
      return
    }
    if (mode === 'register' && password.length < 8) {
      setLocalError('密码至少需要 8 位。')
      return
    }
    if (mode === 'register' && password !== confirmPassword) {
      setLocalError('两次输入的密码不一致。')
      return
    }
    setLocalError('')
    setSubmitting(true)
    try {
      if (mode === 'register') await onRegister(displayName.trim(), email.trim(), password)
      else await onLogin(email.trim(), password, rememberMe)
    } catch {
      // 错误已由父级保留，输入内容不清空，方便教师修正后重试。
    } finally {
      setSubmitting(false)
    }
  }

  const visibleError = localError || error
  const isRegister = mode === 'register'
  return <main className="auth-shell"><section className="auth-card" aria-labelledby="login-title"><div className="auth-brand"><div className="brand-mark" aria-hidden="true"><MagicWand size={22} weight="bold" /></div><div><strong>批小智</strong><span>AI 作业批改工作台</span></div></div><div className="auth-heading"><div className="eyebrow">教师工作空间</div><h1 id="login-title">{isRegister ? '创建教师账号' : '登录批小智'}</h1><p>{isRegister ? '注册后即可创建班级、管理批次并使用 AI 作业批改工作台。' : '登录后管理班级、批次、作业识别和教师复核结果。'}</p></div>{visibleError && <div className="auth-error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{visibleError}</span></div>}<form className="auth-form" onSubmit={submit}>{isRegister && <label className="field"><span>教师姓名 <em>*</em></span><input type="text" value={displayName} onChange={(event) => setDisplayName(event.target.value)} autoComplete="name" placeholder="例如：张老师" /></label>}<label className="field"><span>邮箱 <em>*</em></span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" placeholder="teacher@example.com" aria-invalid={Boolean(visibleError)} /></label><label className="field"><span>密码 <em>*</em></span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={isRegister ? 'new-password' : 'current-password'} placeholder={isRegister ? '至少 8 位密码' : '请输入登录密码'} /></label>{isRegister ? <label className="field"><span>确认密码 <em>*</em></span><input type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} autoComplete="new-password" placeholder="再次输入密码" /></label> : <label className="remember-option"><input type="checkbox" checked={rememberMe} onChange={(event) => setRememberMe(event.target.checked)} /><span>在本机保持登录</span></label>}<PrimaryButton type="submit" icon={isRegister ? UserPlus : ArrowRight} disabled={submitting}>{submitting ? (isRegister ? '注册中…' : '登录中…') : (isRegister ? '创建账号' : '登录')}</PrimaryButton></form><div className="auth-switch">{isRegister ? <><span>已有教师账号？</span><button type="button" onClick={() => { setMode('login'); setLocalError(''); onClearError() }}>返回登录</button></> : <><span>还没有教师账号？</span><button type="button" onClick={() => { setMode('register'); setLocalError(''); onClearError() }}>立即注册</button></>}</div><p className="auth-footnote"><ShieldCheck size={16} aria-hidden="true" />账号和会话由服务端管理；浏览器不会保存正式密码。</p></section></main>
}

function PageHeader({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) {
  const variant = title.includes('批次') ? 'assignments' : title.includes('原图') ? 'ocr' : title.includes('复核') ? 'grading' : title.includes('评分依据') ? 'rubrics' : title.includes('成绩') ? 'reports' : title.includes('运行') ? 'settings' : 'workbench'
  return <div className={`page-header page-header-${variant}`}><div className="page-header-copy"><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div><PageHeaderArt variant={variant} />{action && <div className="page-header-action">{action}</div>}</div>
}

type PageHeaderVariant = 'workbench' | 'assignments' | 'ocr' | 'grading' | 'rubrics' | 'reports' | 'settings'

function PageHeaderArt({ variant }: { variant: PageHeaderVariant }) {
  const config: Record<PageHeaderVariant, { icon: IconType; label: string; caption: string }> = {
    workbench: { icon: MagicWand, label: '批改工作台', caption: '让批改更轻松！' },
    assignments: { icon: UsersThree, label: '班级与作业', caption: '高效管理班级' },
    ocr: { icon: Scan, label: 'OCR 识别', caption: '让校对更高效！' },
    grading: { icon: CheckSquare, label: '教师复核', caption: '让判断更有依据' },
    rubrics: { icon: BookOpen, label: '评分标准', caption: '让批改更专业！' },
    reports: { icon: ChartBar, label: '数据报告', caption: '让教学更有针对性！' },
    settings: { icon: Gear, label: '运行配置', caption: '让 AI 稳定服务教学！' },
  }
  const Icon = config[variant].icon
  return <div className="page-header-art" aria-hidden="true"><span className="annotation-grid" /><span className="art-paper paper-back"><List size={25} weight="bold" /></span><span className="art-paper paper-front"><Icon size={30} weight="duotone" /></span><span className="art-rule rule-one" /><span className="art-rule rule-two" /><span className="art-mark"><CheckCircle size={16} weight="fill" /></span><span className="art-badge"><Sparkle size={14} weight="fill" />{config[variant].label}</span><span className="art-caption">{config[variant].caption}</span></div>
}

function PrimaryButton({ children, onClick, type = 'button', icon: Icon = Plus, disabled = false }: { children: React.ReactNode; onClick?: () => void; type?: 'button' | 'submit'; icon?: IconType; disabled?: boolean }) {
  const actionLabel = children === '保存班级' ? '创建班级' : children
  return <button className="button button-primary" type={type} onClick={onClick} disabled={disabled}><Icon size={18} weight="bold" aria-hidden="true" />{actionLabel}</button>
}

function useModalAccessibility(open: boolean, blocked: boolean, onClose: () => void) {
  const modalRef = useRef<HTMLElement | null>(null)
  const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose }, [onClose])
  useEffect(() => {
    if (!open) return
    const previousFocus = document.activeElement as HTMLElement | null
    const getFocusable = () => Array.from(modalRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])') ?? [])
    const focusFirst = () => getFocusable()[0]?.focus()
    const timer = window.setTimeout(focusFirst, 0)
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (!blocked) closeRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = getFocusable()
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('keydown', handleKeyDown)
      previousFocus?.focus()
    }
  }, [open, blocked])
  return modalRef
}

type ImagePreviewTarget = { src: string; alt: string; title: string }

function ZoomableImage({ src, alt, onOpen }: { src: string; alt: string; onOpen: () => void }) {
  return <button className="image-preview-trigger" type="button" onClick={onOpen} aria-label={`放大预览：${alt}`} title="点击放大预览"><img src={src} alt={alt} /></button>
}

function ImageLightbox({ target, onClose }: { target: ImagePreviewTarget; onClose: () => void }) {
  const [scale, setScale] = useState(1)
  const modalRef = useModalAccessibility(true, false, onClose)

  useEffect(() => {
    setScale(1)
  }, [target.src])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === '+' || event.key === '=') {
        event.preventDefault()
        setScale((current) => Math.min(4, Number((current + 0.25).toFixed(2))))
      } else if (event.key === '-') {
        event.preventDefault()
        setScale((current) => Math.max(0.5, Number((current - 0.25).toFixed(2))))
      } else if (event.key === '0') {
        event.preventDefault()
        setScale(1)
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  return <div className="image-lightbox-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <section ref={modalRef} className="image-lightbox" role="dialog" aria-modal="true" aria-labelledby="image-lightbox-title">
      <div className="image-lightbox-header">
        <div><span className="eyebrow">原图证据 · 图片预览</span><h2 id="image-lightbox-title">{target.title}</h2><p>使用下方按钮或键盘 + / − / 0 调整大小，按 Esc 关闭。</p></div>
        <button className="icon-button" type="button" aria-label="关闭图片预览" onClick={onClose}><X size={21} /></button>
      </div>
      <div className="image-lightbox-stage">
        <img src={target.src} alt={target.alt} style={{ transform: `scale(${scale})` }} />
      </div>
      <div className="image-lightbox-toolbar" aria-label="图片缩放工具">
        <button className="button button-secondary" type="button" onClick={() => setScale((current) => Math.max(0.5, Number((current - 0.25).toFixed(2))))}>缩小</button>
        <span className="image-lightbox-zoom" aria-live="polite">{Math.round(scale * 100)}%</span>
        <button className="button button-secondary" type="button" onClick={() => setScale((current) => Math.min(4, Number((current + 0.25).toFixed(2))))}>放大</button>
        <button className="button button-secondary" type="button" onClick={() => setScale(1)}>还原</button>
      </div>
    </section>
  </div>
}

function SecondaryButton({ children, onClick, icon: Icon, disabled = false }: { children: React.ReactNode; onClick?: () => void; icon?: IconType; disabled?: boolean }) {
  return <button className="button button-secondary" type="button" onClick={onClick} disabled={disabled}>{Icon && <Icon size={17} aria-hidden="true" />}{children}</button>
}

function StatusPill({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'success' | 'warning' | 'danger' | 'info' | 'neutral' }) {
  return <span className={`status-pill ${tone}`}><span className="status-dot" aria-hidden="true" />{children}</span>
}

function MetricCard({ label, value, note, icon: Icon, tone }: { label: string; value: string; note: string; icon: IconType; tone: 'blue' | 'orange' | 'green' | 'purple' }) {
  return <div className="metric-card"><div className={`metric-icon ${tone}`}><Icon size={21} weight="duotone" aria-hidden="true" /></div><div className="metric-copy"><span>{label}</span><strong>{value}</strong><small>{note}</small></div><ArrowUpRight size={18} className="metric-arrow" aria-hidden="true" /></div>
}

function batchPage(status: string): PageKey {
  if (['待识别', '识别中', '待校对'].includes(status)) return 'ocr'
  if (['待批改', '批改中', '待复核'].includes(status)) return 'grading'
  return 'assignments'
}

function Workbench({ teacherName, navigate, onOpenBatch, batches, batchDataError, batchDataLoading, onRetryBatches, onCreate, notify }: { teacherName: string; navigate: (page: PageKey) => void; onOpenBatch: (batchId: string, status: string) => void; batches: Batch[]; batchDataError: string; batchDataLoading: boolean; onRetryBatches: () => void; onCreate: () => void; notify: (message: string) => void }) {
  const [summary, setSummary] = useState({ pending: 0, processing: 0, reviewed: 0, rubrics: 0 })
  const [taskItems, setTaskItems] = useState<Array<{ id: string; student: string; question: string; status: string; tone: 'exception' | 'review' | 'pending'; batchId: string; batchStatus: string }>>([])
  const [loadingSummary, setLoadingSummary] = useState(true)
  const [summaryError, setSummaryError] = useState('')
  const [summaryRetry, setSummaryRetry] = useState(0)

  useEffect(() => {
    let active = true
    const loadSummary = async () => {
      setLoadingSummary(true)
      setSummaryError('')
      setTaskItems([])
      try {
        const response = await api.batches.summary()
        if (!active) return
        setSummary(response.data)
        setTaskItems(response.data.tasks.map((task) => ({
          id: task.id,
          student: task.student,
          question: task.question,
          status: task.status,
          tone: task.tone,
          batchId: task.batch_id,
          batchStatus: batchStatusLabels[task.batch_status] || task.batch_status,
        })))
      } catch (cause) {
        if (active) {
          setTaskItems([])
          setSummaryError(cause instanceof ApiClientError ? cause.message : '无法读取批改概览，请检查后端服务后重试。')
        }
      } finally {
        if (active) setLoadingSummary(false)
      }
    }
    void loadSummary()
    return () => { active = false }
  }, [batches, summaryRetry])

  const retrySummary = () => setSummaryRetry((current) => current + 1)

  const currentBatch = batchDataLoading || batchDataError ? undefined : batches[0]
  const statusDetail = batchDataLoading ? '正在读取批次…' : batchDataError ? '批次数据暂不可用' : currentBatch ? `${currentBatch.status} · ${currentBatch.subject} · ${currentBatch.className}` : '暂无真实批次'
  const summaryValue = (value: number, unit: string) => loadingSummary ? '…' : summaryError ? '—' : `${value} ${unit}`
  const summaryNote = (note: string) => summaryError ? '概览暂不可用，请重试读取' : loadingSummary ? '正在读取服务端数据' : note
  return <div className="content-wrap">
    <PageHeader eyebrow="教师工作台" title={`早上好，${teacherName || '教师'}`} description="这里是你的批改工作台，优先处理真实任务中的异常和待复核结果。" action={<PrimaryButton onClick={onCreate}>创建批次</PrimaryButton>} />
    <div className={`demo-notice ${batchDataError ? 'error' : ''}`}><Info size={18} weight="fill" aria-hidden="true" /><span><strong>{batchDataError ? '真实批次加载失败' : '实时数据说明'}</strong>　{batchDataError || '概览由服务端批次、批改任务和评分标准统计生成；没有数据时显示空状态。'}</span><button type="button" onClick={batchDataError ? onRetryBatches : () => notify('正式成绩以教师复核结果为准')}>{batchDataError ? <><ArrowsClockwise size={15} aria-hidden="true" />重试读取</> : '了解口径'}</button></div>
    <section className="metric-grid" aria-label="批改概览">
      <MetricCard label="待教师复核" value={summaryValue(summary.pending, '条')} note={summaryNote('当前批次真实待复核结果')} icon={WarningCircle} tone="orange" />
      <MetricCard label="正在处理" value={summaryValue(summary.processing, '条')} note={summaryNote('队列中的 OCR / 批改任务')} icon={Timer} tone="blue" />
      <MetricCard label="已复核结果" value={summaryValue(summary.reviewed, '条')} note={summaryNote('可进入正式报告的题目结果')} icon={CheckCircle} tone="green" />
      <MetricCard label="评分标准" value={summaryValue(summary.rubrics, '个')} note={summaryNote('服务端启用中的标准')} icon={BookOpen} tone="purple" />
    </section>
    <div className="workbench-layout">
      <div className="workbench-primary-column">
        <section className="panel task-panel"><div className="panel-heading"><div><h2>需要你处理 <span className="count-label">{summaryError ? '—' : loadingSummary ? '…' : taskItems.length}</span></h2><p>异常优先，处理后才能进入最终成绩</p></div><button className="text-button" type="button" onClick={() => navigate('grading')}>查看全部 <ArrowRight size={16} aria-hidden="true" /></button></div>
          <div className="task-list">
            {taskItems.map((task) => <button className="task-row" key={task.id} type="button" onClick={() => onOpenBatch(task.batchId, task.batchStatus)}><div className={`task-leading ${task.tone}`}><WarningCircle size={20} weight={task.tone === 'exception' ? 'fill' : 'duotone'} aria-hidden="true" /></div><div className="task-copy"><strong>{task.student}</strong><span>{task.question} · {task.status}</span></div><span className="task-action-label">{task.tone === 'exception' ? '查看异常' : task.tone === 'review' ? '去复核' : '查看进度'}</span><span className="task-time">{task.batchId.slice(-6)}</span><CaretRight size={18} className="row-chevron" aria-hidden="true" /></button>)}
            {!taskItems.length && <div className={`empty-inline ${summaryError ? 'error' : ''}`}>{summaryError ? <WarningCircle size={25} weight="fill" aria-hidden="true" /> : <CheckCircle size={25} aria-hidden="true" />}<span>{loadingSummary ? '正在读取任务…' : summaryError ? `任务概览暂时无法读取：${summaryError}` : '当前没有需要处理的真实批改任务。'}</span>{summaryError && <button className="summary-retry" type="button" onClick={retrySummary}><ArrowsClockwise size={15} aria-hidden="true" />重试</button>}</div>}
          </div>
        </section>
        <section className="panel recent-panel"><div className="panel-heading"><div><h2>最近批次</h2><p>按最近更新时间排序</p></div><button className="text-button" type="button" onClick={() => navigate('assignments')}>进入作业管理 <ArrowRight size={16} aria-hidden="true" /></button></div><div className="batch-table" role="table" aria-label="最近批次列表"><div className="batch-table-head" role="row"><span>批次</span><span>学科 / 班级</span><span>进度</span><span>状态</span><span aria-label="操作" /></div>{!batchDataLoading && !batchDataError && batches.slice(0, 3).map((batch) => <BatchRow key={batch.id} batch={batch} onClick={() => onOpenBatch(batch.id, batch.status)} />)}</div>{batchDataLoading && <div className="empty-inline recent-batch-status"><ArrowsClockwise size={19} aria-hidden="true" /><span>正在读取真实批次…</span></div>}{batchDataError && <div className="empty-inline recent-batch-status error"><WarningCircle size={21} weight="fill" aria-hidden="true" /><span>读取批次失败。请使用上方重试入口重新加载。</span></div>}{!batches.length && !batchDataError && !batchDataLoading && <div className="first-run-panel"><div><strong>还没有批改批次</strong><span>先建立班级和学生，再创建第一份试卷批次。</span></div><button className="button button-secondary" type="button" onClick={() => navigate('assignments')}>进入作业管理 <ArrowRight size={16} aria-hidden="true" /></button></div>}</section>
      </div>
      <section className="panel progress-panel"><div className="panel-heading"><div><h2>处理流程</h2><p>{batchDataLoading ? '正在读取最近批次…' : batchDataError ? '批次数据暂不可用' : currentBatch ? `最近批次：${currentBatch.title}` : '暂无当前批次'}</p></div><StatusPill tone={summaryError || batchDataError ? 'danger' : loadingSummary || batchDataLoading ? 'neutral' : currentBatch?.status === '已复核' ? 'success' : currentBatch ? 'warning' : 'neutral'}>{summaryError || batchDataError ? '数据未读取' : loadingSummary || batchDataLoading ? '正在读取' : currentBatch?.status || '暂无批次'}</StatusPill></div><div className="pipeline"><PipelineStep step="01" label="班级、批次与上传" detail={batchDataLoading ? '正在读取批次…' : batchDataError ? '批次数据暂不可用' : currentBatch ? '已建立业务批次' : '等待创建'} done={Boolean(!batchDataLoading && !batchDataError && currentBatch)} /><PipelineStep step="02" label="题干/答案与 OCR 校对" detail={currentBatch ? statusDetail : batchDataLoading ? '正在读取批次…' : batchDataError ? '批次数据暂不可用' : '等待上传题干与答案'} done={Boolean(!batchDataLoading && !batchDataError && currentBatch && !['待上传', '上传中'].includes(currentBatch.status))} /><PipelineStep step="03" label="学生 OCR 与批改" detail={summaryError ? '队列状态暂不可用' : loadingSummary ? '正在读取队列状态…' : `${summary.processing} 条处理中`} current={Boolean(!summaryError && !loadingSummary && currentBatch && ['批改中', '待复核'].includes(currentBatch.status))} /><PipelineStep step="04" label="评分与报告" detail={summaryError ? '复核状态暂不可用' : loadingSummary ? '正在读取复核状态…' : summary.pending ? `${summary.pending} 条待复核` : '教师复核后生成报告'} /></div><button className="wide-action" type="button" disabled={(!batchDataError && loadingSummary && !summaryError) || batchDataLoading} onClick={() => { if (batchDataError) onRetryBatches(); else if (summaryError) retrySummary(); else navigate(summary.pending ? 'grading' : 'assignments') }}>{batchDataError ? '重试读取批次' : summaryError ? '重试加载概览' : loadingSummary || batchDataLoading ? '正在读取数据…' : summary.pending ? '继续批改与复核' : currentBatch ? '进入作业管理' : '创建第一个批次'} {batchDataError || summaryError ? <ArrowsClockwise size={17} aria-hidden="true" /> : <ArrowRight size={17} aria-hidden="true" />}</button></section>
      <div className="workbench-side-column">
        <section className="panel quick-actions-panel"><div className="panel-heading"><div><h2>继续流程</h2><p>只保留当前批改链路的入口</p></div><MagicWand size={22} className="heading-icon" aria-hidden="true" /></div><div className="quick-action-grid"><button type="button" className="quick-action blue" onClick={onCreate}><FileText size={20} weight="duotone" aria-hidden="true" /><span><strong>创建批次</strong><small>配置题目、分值和题干来源</small></span><ArrowRight size={16} aria-hidden="true" /></button><button type="button" className="quick-action green" onClick={() => navigate('assignments')}><Stack size={20} weight="duotone" aria-hidden="true" /><span><strong>批次与上传</strong><small>班级、学生照片和题干答案</small></span><ArrowRight size={16} aria-hidden="true" /></button><button type="button" className="quick-action orange" onClick={() => navigate('reports')}><ChartBar size={20} weight="duotone" aria-hidden="true" /><span><strong>评分与报告</strong><small>复核完成后查看正式结果</small></span><ArrowRight size={16} aria-hidden="true" /></button></div></section>
        <section className="panel session-status-panel"><div className="panel-heading"><div><h2>数据状态</h2><p>根据已读取数据展示，不代替后端健康检查</p></div><StatusPill tone={batchDataError || summaryError ? 'danger' : loadingSummary || batchDataLoading ? 'neutral' : 'success'}>{batchDataError || summaryError ? '读取异常' : loadingSummary || batchDataLoading ? '正在读取' : '已读取'}</StatusPill></div><div className="session-status-list"><SessionStatus icon={Database} label="批次数据" value={batchDataError ? '加载异常' : batchDataLoading ? '正在读取…' : batches.length ? `${batches.length} 个批次` : '暂无数据'} tone={batchDataError ? 'danger' : batchDataLoading ? 'neutral' : 'success'} /><SessionStatus icon={Timer} label="OCR / 批改任务" value={summaryError ? '概览加载失败' : loadingSummary ? '正在读取…' : summary.processing ? `${summary.processing} 条处理中` : '当前无处理中任务'} tone={summaryError ? 'danger' : loadingSummary ? 'neutral' : summary.processing ? 'info' : 'success'} /><SessionStatus icon={WarningCircle} label="教师复核" value={summaryError ? '状态未知' : loadingSummary ? '正在读取…' : summary.pending ? `${summary.pending} 条待处理` : '当前无待复核'} tone={summaryError ? 'danger' : loadingSummary ? 'neutral' : summary.pending ? 'warning' : 'success'} /><SessionStatus icon={ChartBar} label="报告口径" value={summaryError ? '状态未知' : loadingSummary ? '正在读取…' : summary.reviewed ? '已有可复核结果' : '等待复核结果'} tone={summaryError ? 'neutral' : loadingSummary ? 'neutral' : summary.reviewed ? 'success' : 'neutral'} /></div></section>
      </div>
    </div>
    <div className="footer-note"><ShieldCheck size={16} aria-hidden="true" /><span>原始作业与原始 OCR 结果只读保存，教师修正会形成新版本并记录操作人、时间和原因。</span></div>
  </div>
}

function SessionStatus({ icon: Icon, label, value, tone }: { icon: IconType; label: string; value: string; tone: 'success' | 'warning' | 'danger' | 'info' | 'neutral' }) {
  return <div className="session-status-row"><div className={`session-status-icon ${tone}`}><Icon size={18} weight="duotone" aria-hidden="true" /></div><div><strong>{label}</strong><span>{value}</span></div><StatusPill tone={tone}>{tone === 'danger' ? '异常' : tone === 'warning' ? '待处理' : tone === 'info' ? '处理中' : tone === 'success' ? '正常' : '待定'}</StatusPill></div>
}

function PipelineStep({ step, label, detail, done = false, current = false }: { step: string; label: string; detail: string; done?: boolean; current?: boolean }) {
  return <div className={`pipeline-step ${done ? 'done' : ''} ${current ? 'current' : ''}`}><div className="pipeline-number">{done ? <Check size={15} weight="bold" aria-label="已完成" /> : step}</div><div><strong>{label}</strong><span>{detail}</span></div></div>
}

function BatchRow({ batch, onClick, onEdit, onDelete, onPurge, onHardDelete, deleting, purging, hardDeleting, deleteLocked = false }: { batch: Batch; onClick: () => void; onEdit?: () => void; onDelete?: () => void; onPurge?: () => void; onHardDelete?: () => void; deleting?: boolean; purging?: boolean; hardDeleting?: boolean; deleteLocked?: boolean }) {
  const tone = batch.status === '已复核' ? 'success' : batch.status === '待复核' ? 'warning' : batch.status === '部分失败' ? 'danger' : 'info'
  const busy = Boolean(deleting || purging || hardDeleting)
  const row = <button className="batch-table-row" type="button" role="row" onClick={onClick}><div className="batch-name"><div className="file-icon"><FileText size={19} weight="duotone" aria-hidden="true" /></div><div><strong>{batch.title}</strong><span>{batch.id} · {batch.updated}</span></div></div><div className="batch-context"><strong>{batch.subject}</strong><span>{batch.className} · {batch.count === null ? '待读取' : `${batch.count} 份`}</span></div><div className="progress-cell"><div className="progress-label"><span>{batch.progress === null ? '待统计' : `${batch.progress}%`}</span><span>{batch.exceptions === null ? '详情查看' : batch.exceptions ? `${batch.exceptions} 条异常` : '无异常'}</span></div><div className="progress-track"><span style={{ width: `${batch.progress ?? 0}%` }} /></div></div><StatusPill tone={tone}>{batch.status}</StatusPill><CaretRight size={18} className="row-chevron" aria-hidden="true" /></button>
  if (!onEdit && !onDelete && !onPurge && !onHardDelete && !deleteLocked) return row
  const actionLayout = onDelete || (onPurge && onEdit) ? 'has-delete' : onPurge ? 'has-purge' : onHardDelete ? 'has-hard-delete' : ''
  return <div className={`batch-row-actions ${actionLayout} ${deleteLocked && !onPurge && !onHardDelete ? 'has-lock' : ''}`}>{row}{onEdit && <button className="icon-button batch-edit-button" type="button" aria-label={`编辑${batch.title}`} onClick={(event) => { event.stopPropagation(); onEdit() }} disabled={busy}><PencilSimple size={18} aria-hidden="true" /></button>}{onDelete && <button className="icon-button batch-delete-button" type="button" aria-label={deleting ? `正在删除${batch.title}` : `删除${batch.title}`} title="删除草稿批次" onClick={(event) => { event.stopPropagation(); onDelete() }} disabled={busy}>{deleting ? <Clock size={18} aria-hidden="true" /> : <TrashSimple size={18} aria-hidden="true" />}</button>}{onPurge && <button className="icon-button batch-delete-button" type="button" aria-label={purging ? `正在撤销 OCR 并删除${batch.title}` : `撤销 OCR 并删除${batch.title}`} title="撤销 OCR 并删除整个批次" onClick={(event) => { event.stopPropagation(); onPurge() }} disabled={busy}>{purging ? <Clock size={18} aria-hidden="true" /> : <TrashSimple size={18} aria-hidden="true" />}</button>}{onHardDelete && <button className="icon-button batch-delete-button" type="button" aria-label={hardDeleting ? `正在物理删除${batch.title}` : `物理删除${batch.title}`} title="物理删除已复核批次" onClick={(event) => { event.stopPropagation(); onHardDelete() }} disabled={busy}>{hardDeleting ? <Clock size={18} aria-hidden="true" /> : <TrashSimple size={18} aria-hidden="true" />}</button>}{deleteLocked && !onPurge && !onHardDelete && <span className="batch-delete-locked" role="note" title="当前批次已进入不可删除的后续流程。">已锁定</span>}</div>
}

function Assignments({ batches, batchDataError, batchDataLoading, onRetryBatches, globalSearch, onCreate, onEditBatch, onDeleteBatch, deletingBatchId, onPurgeBatch, purgingBatchId, onDeleteReviewedBatch, deletingReviewedBatchId, navigate, onOpenBatch, notify }: { batches: Batch[]; batchDataError: string; batchDataLoading: boolean; onRetryBatches: () => void; globalSearch: string; onCreate: () => void; onEditBatch: (batchId: string) => void; onDeleteBatch: (batch: Batch) => void; deletingBatchId: string | null; onPurgeBatch: (batch: Batch) => void; purgingBatchId: string | null; onDeleteReviewedBatch: (batch: Batch) => void; deletingReviewedBatchId: string | null; navigate: (page: PageKey) => void; onOpenBatch: (batchId: string, status: string) => void; notify: (message: string) => void }) {
  const [query, setQuery] = useState(globalSearch)
  const [statusFilter, setStatusFilter] = useState('all')
  const [subjectFilter, setSubjectFilter] = useState('all')
  useEffect(() => { setQuery(globalSearch) }, [globalSearch])
  const filtered = useMemo(() => batches.filter((batch) => `${batch.id}${batch.title}${batch.className}${batch.subject}`.toLowerCase().includes(query.trim().toLowerCase()) && (statusFilter === 'all' || batch.status === statusFilter) && (subjectFilter === 'all' || batch.subject === subjectFilter)), [batches, query, statusFilter, subjectFilter])
  const hasFilters = Boolean(query.trim()) || statusFilter !== 'all' || subjectFilter !== 'all'
  const resetFilters = () => { setQuery(''); setStatusFilter('all'); setSubjectFilter('all') }
  return <div className="content-wrap"><PageHeader eyebrow="作业管理" title="批次与班级" description="创建批次、管理上传记录，并在批改前确认学生作业归属。" action={<PrimaryButton onClick={onCreate}>创建批次</PrimaryButton>} />
    <section className="panel assignment-toolbar"><label className="field compact-field"><span>搜索批次 / 班级 / 学科</span><div className="input-with-icon"><MagnifyingGlass size={18} aria-hidden="true" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="名称、批次 ID、班级或学科" /></div></label><label className="field compact-field"><span>学科</span><select value={subjectFilter} onChange={(event) => setSubjectFilter(event.target.value)}><option value="all">全部学科</option><option value="语文">语文</option><option value="数学">数学</option><option value="英语">英语</option></select></label><label className="field compact-field"><span>状态</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">全部状态</option><option value="待上传">待上传</option><option value="上传中">上传中</option><option value="待识别">待识别</option><option value="识别中">识别中</option><option value="待校对">待校对</option><option value="待批改">待批改</option><option value="批改中">批改中</option><option value="待复核">待复核</option><option value="已复核">已复核</option><option value="部分失败">部分失败</option><option value="已归档">已归档</option></select></label><span className="filter-result"><CaretDown size={17} aria-hidden="true" />{batchDataLoading ? '正在读取批次…' : batchDataError ? '批次读取失败' : `显示 ${filtered.length} / ${batches.length} 个批次`}</span>{hasFilters && <button className="text-button filter-reset" type="button" onClick={resetFilters}>清除筛选</button>}</section>
     {batchDataError && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{batchDataError}</span><button className="text-button workflow-retry-button" type="button" onClick={onRetryBatches} disabled={batchDataLoading}><ArrowsClockwise size={15} aria-hidden="true" />重试读取</button></div>}
    <div className="assignment-workspace-grid"><div className="assignment-primary-column"><section className="panel"><div className="panel-heading"><div><h2>作业批次 <span className="count-label">{batchDataLoading ? '…' : filtered.length}</span></h2><p>首版支持 JPG、PNG、PDF 批量上传；草稿可删除，上传或 OCR 阶段可撤销整批，已复核批次可在密码验证和二次确认后物理删除。</p></div><button className="text-button" type="button" onClick={() => document.getElementById('assignment-upload-history')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>查看上传记录 <ArrowRight size={16} aria-hidden="true" /></button></div><div className="batch-table" role="table" aria-label="作业批次列表"><div className="batch-table-head" role="row"><span>批次</span><span>学科 / 班级</span><span>进度</span><span>状态</span><span aria-label="操作" /></div>{!batchDataLoading && !batchDataError && filtered.map((batch) => { const purgeable = canPurgeBatch(batch.statusCode); const editable = batchEditableStatuses.has(batch.statusCode); const hardDeletable = batch.statusCode === 'reviewed'; return <BatchRow key={batch.id} batch={batch} onClick={() => onOpenBatch(batch.id, batch.status)} onEdit={editable ? () => onEditBatch(batch.id) : undefined} onDelete={batch.statusCode === 'draft' ? () => onDeleteBatch(batch) : undefined} onPurge={purgeable ? () => onPurgeBatch(batch) : undefined} onHardDelete={hardDeletable ? () => onDeleteReviewedBatch(batch) : undefined} deleting={deletingBatchId === batch.id} purging={purgingBatchId === batch.id} hardDeleting={deletingReviewedBatchId === batch.id} deleteLocked={batch.statusCode !== 'draft' && !purgeable && !hardDeletable} /> })}</div>{batchDataLoading && <div className="empty-state"><ArrowsClockwise size={24} aria-hidden="true" /><strong>正在读取批次</strong><span>正在从服务端加载真实批次数据。</span></div>}{batchDataError && !batchDataLoading && <div className="empty-state"><WarningCircle size={28} weight="fill" aria-hidden="true" /><strong>暂时无法读取批次</strong><span>请查看上方错误提示并重试。</span></div>}{!batchDataLoading && !batchDataError && filtered.length === 0 && <div className="empty-state"><Database size={28} aria-hidden="true" /><strong>{batches.length && hasFilters ? '没有符合条件的批次' : '暂无真实批次'}</strong><span>{batches.length && hasFilters ? '调整搜索或筛选条件后重试。' : '先创建班级，再创建作业批次。'}</span>{batches.length && hasFilters ? <button className="button button-secondary" type="button" onClick={resetFilters}>清除筛选</button> : <button className="button button-secondary" type="button" onClick={onCreate}>创建第一个批次</button>}</div>}</section><section className="panel assignment-ownership-panel"><div className="panel-heading"><div><h2>学生作业归属</h2><p>多页文件先归并，再进入 OCR 与批改</p></div><UsersThree size={22} className="heading-icon" aria-hidden="true" /></div><div className="info-list"><InfoRow label="自动归属" value="文件名、页眉/学号、连续页码" /><InfoRow label="人工确认点" value="标识冲突、页序不连续、多候选对象" /><InfoRow label="失败兜底" value="保留单页记录，状态标记为 needs_review" /></div><button className="button button-secondary full-button" type="button" onClick={() => navigate('ocr')}>进入 OCR 校对 <ArrowRight size={17} aria-hidden="true" /></button></section></div><AssignmentUploadPanel notify={notify} /></div>
  </div>
}

type UploadEntry = { id: string; file: File; fileId?: string; status: 'pending' | 'uploading' | 'success' | 'failed'; message: string }

function AssignmentUploadPanel({ notify }: { notify: (message: string) => void }) {
  const [batches, setBatches] = useState<AssignmentBatch[]>([])
  const [selectedBatchId, setSelectedBatchId] = useState('')
  const [entries, setEntries] = useState<UploadEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [history, setHistory] = useState<SourceFile[]>([])
  const [historyPages, setHistoryPages] = useState<SourcePage[]>([])
  const [previewImage, setPreviewImage] = useState<ImagePreviewTarget | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [deletingFileId, setDeletingFileId] = useState('')
  const [purgeCandidate, setPurgeCandidate] = useState<SourceFile | null>(null)

  const loadBatches = async () => {
    setLoading(true)
    try {
      const response = await api.batches.list({ page_size: 100 })
      setBatches(response.data)
      if (!selectedBatchId && response.data[0]) setSelectedBatchId(response.data[0].id)
      setError('')
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '无法加载批次，请先登录并确认后端服务已启动。')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadBatches()
  }, [])

  useEffect(() => {
    let active = true
    if (!selectedBatchId) {
      setHistory([])
      setHistoryPages([])
      setHistoryError('')
      return () => { active = false }
    }
    setHistoryLoading(true)
    api.batches.files(selectedBatchId, undefined, 'student_work').then((response) => {
      if (!active) return
      setHistory(response.data)
      setHistoryError('')
    }).catch((cause) => {
      if (!active) return
      setHistory([])
      setHistoryError(cause instanceof ApiClientError ? cause.message : '已保存文件记录加载失败。')
    }).finally(() => {
      if (active) setHistoryLoading(false)
    })
    api.batches.pages(selectedBatchId, undefined, 'student_work').then((response) => {
      if (active) setHistoryPages(response.data)
    }).catch(() => {
      if (active) setHistoryPages([])
    })
    return () => { active = false }
  }, [selectedBatchId])

  const chooseFiles = (fileList: FileList | null) => {
    const nextFiles = Array.from(fileList ?? [])
    if (!nextFiles.length) return
    setEntries((current) => [...current, ...nextFiles.map((file) => ({ id: createIdempotencyKey(), file, status: 'pending' as const, message: '等待上传' }))])
    setError('')
  }

  const updateEntry = (id: string, changes: Partial<UploadEntry>) => {
    setEntries((current) => current.map((entry) => entry.id === id ? { ...entry, ...changes } : entry))
  }

  const uploadEntry = async (entry: UploadEntry) => {
    if (!selectedBatchId) return false
    updateEntry(entry.id, { status: 'uploading', message: '正在校验并保存文件…' })
    try {
      const response = entry.fileId
        ? await api.files.retry(entry.fileId, entry.file, createIdempotencyKey())
        : await api.batches.uploadFile(selectedBatchId, entry.file, createIdempotencyKey())
      updateEntry(entry.id, { fileId: response.data.file_id, status: response.data.status === 'failed' ? 'failed' : 'success', message: response.data.status === 'failed' ? response.data.failure_code || '文件校验失败' : '文件已保存，等待后续处理' })
      return response.data.status !== 'failed'
    } catch (cause) {
      const detail = cause instanceof ApiClientError
        ? cause.details.map((item) => item.message || item.code).filter(Boolean).join('；')
        : ''
      updateEntry(entry.id, { status: 'failed', message: cause instanceof ApiClientError ? `${cause.message}${detail ? `（${detail}）` : ''}` : '上传失败，请单独重试' })
      return false
    }
  }

  const startUpload = async () => {
    if (!selectedBatchId) {
      setError('请先选择一个批次。')
      return
    }
    const pending = entries.filter((entry) => entry.status === 'pending')
    if (!pending.length) {
      setError('请先选择待上传文件。')
      return
    }
    setUploading(true)
    setError('')
    let successCount = 0
    for (const entry of pending) {
      if (await uploadEntry(entry)) successCount += 1
    }
    setUploading(false)
    await refreshUploadHistory(selectedBatchId)
    notify(`${successCount} 个文件已完成上传处理，失败项可单独重试`)
  }

  const retryEntry = async (entry: UploadEntry) => {
    if (uploading) return
    setUploading(true)
    await uploadEntry({ ...entry, status: 'pending', message: '等待重试' })
    setUploading(false)
    await refreshUploadHistory(selectedBatchId)
  }

  const refreshUploadHistory = async (batchToLoad = selectedBatchId) => {
    if (!batchToLoad) return
    setHistoryLoading(true)
    try {
      const response = await api.batches.files(batchToLoad, undefined, 'student_work')
      setHistory(response.data)
      setHistoryError('')
      try {
        const pagesResponse = await api.batches.pages(batchToLoad, undefined, 'student_work')
        setHistoryPages(pagesResponse.data)
      } catch {
        setHistoryPages([])
      }
    } catch (cause) {
      setHistoryError(cause instanceof ApiClientError ? cause.message : '已保存文件记录加载失败。')
    } finally {
      setHistoryLoading(false)
    }
  }

  const removeFile = async (file: SourceFile) => {
    if (deletingFileId) return
    const confirmed = window.confirm(`确定删除“${file.original_name}”？删除后无法恢复。`)
    if (!confirmed) return
    setDeletingFileId(file.file_id)
    setHistoryError('')
    setPurgeCandidate(null)
    try {
      await api.files.remove(file.file_id)
      setHistory((current) => current.filter((item) => item.file_id !== file.file_id))
      notify(`${file.original_name} 已删除`)
    } catch (cause) {
      if (cause instanceof ApiClientError && cause.code === 'FILE_DELETE_LOCKED') setPurgeCandidate(file)
      setHistoryError(cause instanceof ApiClientError ? cause.message : '文件删除失败，请稍后重试。')
    } finally {
      setDeletingFileId('')
    }
  }

  const purgeFile = async (file: SourceFile) => {
    if (deletingFileId) return
    const confirmed = window.confirm(`“${file.original_name}”已进入识别流程。\n\n继续将撤销该文件的 OCR 结果，并删除原图、页面记录和识别缓存，操作无法恢复。确定继续吗？`)
    if (!confirmed) return
    setDeletingFileId(file.file_id)
    setHistoryError('')
    try {
      await api.files.purge(file.file_id, file.original_name)
      setHistory((current) => current.filter((item) => item.file_id !== file.file_id))
      setPurgeCandidate(null)
      notify(`${file.original_name} 已撤销识别并彻底删除`)
    } catch (cause) {
      setHistoryError(cause instanceof ApiClientError ? cause.message : '彻底删除失败，请稍后重试。')
    } finally {
      setDeletingFileId('')
    }
  }

  const successCount = entries.filter((entry) => entry.status === 'success').length
  const failedCount = entries.filter((entry) => entry.status === 'failed').length
  return <><section className="upload-card assignment-upload-panel">
    <div className="upload-orb"><CloudArrowUp size={28} weight="duotone" aria-hidden="true" /></div>
    <div className="upload-panel-heading"><div><h2>批量上传作业</h2><p>先绑定具体批次，再逐文件校验 JPG、PNG 或 PDF。</p></div><button className="icon-button" type="button" aria-label="刷新批次列表" onClick={() => void loadBatches()} disabled={loading || uploading}><ArrowsClockwise size={19} /></button></div>
    <label className="field upload-batch-field"><span>所属批次 <em>*</em></span><select value={selectedBatchId} onChange={(event) => setSelectedBatchId(event.target.value)} disabled={loading || uploading}><option value="">{loading ? '正在加载批次…' : batches.length ? '请选择批次' : '暂无可上传批次'}</option>{batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.title} · {batch.class_name || batch.class_id}</option>)}</select></label>
    <label className="file-dropzone compact" htmlFor="student-work-files"><UploadSimple size={22} weight="duotone" aria-hidden="true" /><strong>选择作业文件</strong><span>可多选 JPG、PNG、PDF</span><input id="student-work-files" type="file" accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf" multiple onChange={(event) => { chooseFiles(event.target.files); event.currentTarget.value = '' }} disabled={uploading} /></label>
    {error && <div className="workflow-message error" role="alert"><WarningCircle size={17} weight="fill" aria-hidden="true" /><span>{error}</span></div>}
    <div className="upload-summary"><span>待处理 {entries.filter((entry) => entry.status === 'pending' || entry.status === 'uploading').length}</span><span>成功 {successCount}</span><span>失败 {failedCount}</span></div>
    {entries.length > 0 && <div className="upload-file-list" aria-label="待上传文件列表">{entries.map((entry) => <div className="upload-file-row" key={entry.id}><FileText size={18} weight="duotone" aria-hidden="true" /><div><strong>{entry.file.name}</strong><span>{formatFileSize(entry.file.size)} · {entry.message}</span></div>{entry.status === 'failed' && <button className="text-button" type="button" onClick={() => void retryEntry(entry)} disabled={uploading}>重试</button>}<StatusPill tone={entry.status === 'success' ? 'success' : entry.status === 'failed' ? 'danger' : entry.status === 'uploading' ? 'info' : 'neutral'}>{entry.status === 'uploading' ? '上传中' : entry.status === 'success' ? '已保存' : entry.status === 'failed' ? '失败' : '待上传'}</StatusPill></div>)}</div>}
    <div className="upload-history" id="assignment-upload-history" aria-live="polite">
      <div className="upload-panel-heading"><div><h3>已保存学生作业</h3><p>{historyLoading ? '正在读取记录…' : `当前批次 ${history.length} 个文件`}</p></div><button className="text-button" type="button" onClick={() => void refreshUploadHistory()} disabled={historyLoading || loading || uploading}>刷新</button></div>
      {historyError && <div className="workflow-message error" role="alert"><WarningCircle size={16} weight="fill" aria-hidden="true" /><span>{historyError}</span>{purgeCandidate && <button className="text-button file-purge-button" type="button" onClick={() => void purgeFile(purgeCandidate)} disabled={Boolean(deletingFileId)}>{deletingFileId === purgeCandidate.file_id ? '彻底删除中…' : '撤销识别并彻底删除'}</button>}<button className="text-button" type="button" onClick={() => void refreshUploadHistory()}>重试</button></div>}
      {history.length > 0 && <div className="upload-file-list" aria-label="已保存学生作业列表">{history.map((file) => { const page = historyPages.find((candidate) => candidate.source_file_id === file.file_id); const isPdf = file.extension.toLowerCase() === '.pdf' || file.original_name.toLowerCase().endsWith('.pdf'); return <div className="upload-file-row" key={file.file_id}>{page && !isPdf ? <ZoomableImage src={api.pages.originalUrl(page.id)} alt={`${file.original_name} 原图`} onOpen={() => setPreviewImage({ src: api.pages.originalUrl(page.id), alt: `${file.original_name} 原图`, title: `学生作业原图 · ${file.original_name}` })} /> : <FileText size={18} weight="duotone" aria-hidden="true" />}<div><strong>{file.original_name}</strong><span>{formatFileSize(file.size_bytes)} · {file.page_count ?? '—'} 页 · {file.status === 'success' ? page && !isPdf ? '已保存 · 点击缩略图放大' : '已保存，识别后可预览原图' : file.failure_code || file.status}</span></div>{file.status !== 'validating' && file.status !== 'pending' && <button className="text-button file-delete-button" type="button" onClick={() => void removeFile(file)} disabled={Boolean(deletingFileId)} title={isPdf ? '删除原始 PDF 文件' : '删除原始作业图片'}>{deletingFileId === file.file_id ? '删除中…' : isPdf ? '删除文件' : '删除图片'}</button>}<StatusPill tone={file.status === 'success' ? 'success' : file.status === 'failed' ? 'danger' : 'info'}>{file.status === 'success' ? '已保存' : file.status === 'failed' ? '失败' : '处理中'}</StatusPill></div> })}</div>}
      {!historyLoading && !historyError && history.length === 0 && <div className="empty-inline">当前批次暂无已保存学生作业文件。</div>}
    </div>
    <button className="button button-primary full-button" type="button" onClick={() => void startUpload()} disabled={uploading || loading || !selectedBatchId || !entries.some((entry) => entry.status === 'pending')}><UploadSimple size={18} weight="bold" aria-hidden="true" />{uploading ? '处理中…' : '开始上传'}</button>
  </section>{previewImage && <ImageLightbox target={previewImage} onClose={() => setPreviewImage(null)} />}</>
}

function formatFileSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.ceil(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function ClassManagement({ mode, notify, onClose }: { mode: 'create' | 'manage'; notify: (message: string) => void; onClose: () => void }) {
  const modalRef = useModalAccessibility(true, false, onClose)
  const titleId = mode === 'create' ? 'create-class-title' : 'manage-class-title'
  return <div className="modal-backdrop class-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><section ref={modalRef} className={`modal class-modal class-modal-${mode}`} role="dialog" aria-modal="true" aria-labelledby={titleId}><div className="modal-header"><div><span className="eyebrow">班级工作台</span><h2 id={titleId}>{mode === 'create' ? '创建班级' : '管理班级'}</h2><p>{mode === 'create' ? '先建立学生归属范围，后续创建批次时可以直接选择。' : '维护班级状态、学生编号和姓名；停用不会删除历史关联。'}</p></div><button className="icon-button" type="button" aria-label="关闭班级窗口" onClick={onClose}><X size={20} /></button></div><LegacyClassManagement mode={mode} notify={notify} onClose={onClose} /></section></div>
}

function LegacyClassManagement({ mode, notify, onClose }: { mode: 'create' | 'manage'; notify: (message: string) => void; onClose: () => void }) {
  const [classes, setClasses] = useState<ClassRoom[]>([])
  const [selectedClassId, setSelectedClassId] = useState('')
  const [selectedClass, setSelectedClass] = useState<ClassRoom | null>(null)
  const [className, setClassName] = useState('')
  const [studentsText, setStudentsText] = useState('')
  const [classDraftName, setClassDraftName] = useState('')
  const [classDraftStatus, setClassDraftStatus] = useState<'active' | 'inactive'>('active')
  const [studentCode, setStudentCode] = useState('')
  const [studentName, setStudentName] = useState('')
  const [editingStudentId, setEditingStudentId] = useState('')
  const [editingStudentCode, setEditingStudentCode] = useState('')
  const [editingStudentName, setEditingStudentName] = useState('')
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const loadClasses = async () => {
    setLoading(true)
    try {
      const response = await api.classes.list()
      setClasses(response.data)
      setSelectedClassId((current) => current && response.data.some((item) => item.id === current) ? current : response.data[0]?.id ?? '')
      setError('')
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '无法加载班级，请检查后端服务。')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (mode === 'manage') void loadClasses()
  }, [mode])

  useEffect(() => {
    if (!selectedClassId) {
      setSelectedClass(null)
      return
    }
    let cancelled = false
    setDetailLoading(true)
    api.classes.get(selectedClassId).then((response) => {
      if (cancelled) return
      setSelectedClass(response.data)
      setClassDraftName(response.data.name)
      setClassDraftStatus(response.data.status)
      setError('')
    }).catch((cause) => {
      if (!cancelled) setError(cause instanceof ApiClientError ? cause.message : '无法加载班级详情，请刷新后重试。')
    }).finally(() => {
      if (!cancelled) setDetailLoading(false)
    })
    return () => { cancelled = true }
  }, [selectedClassId])

  const createClass = async (event: FormEvent) => {
    event.preventDefault()
    if (!className.trim()) {
      setError('请填写班级名称。')
      return
    }
    const lines = studentsText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
    const students = [] as Array<{ student_code: string; display_name: string }>
    for (const line of lines) {
      const [studentCode, ...nameParts] = line.split(/[,，\t]/).map((part) => part.trim()).filter(Boolean)
      const displayName = nameParts.join(' ')
      if (!studentCode || !displayName) {
        setError(`学生行“${line}”格式不正确，请使用“学号,姓名”。`)
        return
      }
      students.push({ student_code: studentCode, display_name: displayName })
    }
    setSaving(true)
    setError('')
    try {
      const response = await api.classes.create({ name: className.trim(), students })
      setClasses((current) => [response.data, ...current.filter((item) => item.id !== response.data.id)])
      setSelectedClassId(response.data.id)
      setClassName('')
      setStudentsText('')
      notify('班级已保存，可用于创建作业批次')
      onClose()
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '班级保存失败，请检查后端服务。')
    } finally {
      setSaving(false)
    }
  }

  const saveClass = async (event: FormEvent) => {
    event.preventDefault()
    if (!selectedClass || !classDraftName.trim()) return
    setSaving(true)
    setError('')
    try {
      const response = await api.classes.update(selectedClass.id, { name: classDraftName.trim(), status: classDraftStatus, version: selectedClass.version })
      setSelectedClass(response.data)
      setClasses((current) => current.map((item) => item.id === response.data.id ? { ...item, ...response.data } : item))
      notify(response.data.status === 'active' ? '班级信息已更新' : '班级已停用，历史批次仍保留')
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '班级更新失败，请刷新后重试。')
    } finally {
      setSaving(false)
    }
  }

  const addStudent = async (event: FormEvent) => {
    event.preventDefault()
    if (!selectedClass || !studentCode.trim() || !studentName.trim()) return
    setSaving(true)
    setError('')
    try {
      const response = await api.classes.createStudent(selectedClass.id, { student_code: studentCode.trim(), display_name: studentName.trim() })
      const students = [...(selectedClass.students ?? []), response.data].sort((left, right) => left.student_code.localeCompare(right.student_code))
      setSelectedClass({ ...selectedClass, students, student_count: students.length })
      setClasses((current) => current.map((item) => item.id === selectedClass.id ? { ...item, student_count: students.length } : item))
      setStudentCode('')
      setStudentName('')
      notify('学生已添加到班级')
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '学生添加失败，请检查学号是否重复。')
    } finally {
      setSaving(false)
    }
  }

  const beginStudentEdit = (student: NonNullable<ClassRoom['students']>[number]) => {
    setEditingStudentId(student.id)
    setEditingStudentCode(student.student_code)
    setEditingStudentName(student.display_name)
  }

  const saveStudent = async (studentId: string) => {
    if (!selectedClass || !editingStudentCode.trim() || !editingStudentName.trim()) return
    setSaving(true)
    setError('')
    try {
      const response = await api.classes.updateStudent(studentId, { student_code: editingStudentCode.trim(), display_name: editingStudentName.trim() })
      const students = (selectedClass.students ?? []).map((item) => item.id === studentId ? response.data : item)
      setSelectedClass({ ...selectedClass, students })
      setEditingStudentId('')
      notify('学生信息已更新')
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '学生信息更新失败，请刷新后重试。')
    } finally {
      setSaving(false)
    }
  }

  const toggleStudentStatus = async (student: NonNullable<ClassRoom['students']>[number]) => {
    if (!selectedClass) return
    setSaving(true)
    setError('')
    try {
      const response = await api.classes.updateStudent(student.id, { status: student.status === 'active' ? 'inactive' : 'active' })
      setSelectedClass({ ...selectedClass, students: (selectedClass.students ?? []).map((item) => item.id === student.id ? response.data : item) })
      notify(response.data.status === 'active' ? '学生已启用' : '学生已停用')
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '学生状态更新失败，请刷新后重试。')
    } finally {
      setSaving(false)
    }
  }


  return <section className="panel class-management-panel" aria-labelledby="class-management-title"><div className="panel-heading"><div><h2 id="class-management-title">班级与学生</h2><p>创建班级后，可在右侧维护班级状态、学生编号和姓名；停用不会删除历史关联。</p></div><button className="icon-button" type="button" aria-label="刷新班级列表" onClick={() => void loadClasses()} disabled={loading || saving}><ArrowsClockwise size={19} /></button></div><div className="class-management-grid"><form className="class-create-form" onSubmit={createClass}><label className="field"><span>班级名称 <em>*</em></span><input value={className} onChange={(event) => setClassName(event.target.value)} placeholder="例如：八年级 2 班" disabled={saving} /></label><label className="field"><span>学生名单</span><textarea value={studentsText} onChange={(event) => setStudentsText(event.target.value)} placeholder="每行一名学生：学号,姓名\n例如：student_023,张同学" disabled={saving} /><small>可批量创建；创建后可在右侧逐名维护。</small></label>{error && <div className="workflow-message error" role="alert"><WarningCircle size={17} weight="fill" aria-hidden="true" /><span>{error}</span></div>}<PrimaryButton type="submit" icon={Check} disabled={saving}>{saving ? '保存中…' : '保存班级'}</PrimaryButton></form><div className="class-list"><div className="class-list-heading"><strong>班级列表</strong><span>{loading ? '加载中…' : `${classes.length} 个`}</span></div>{classes.length ? classes.map((item) => <button className={`class-list-item ${selectedClassId === item.id ? 'selected' : ''}`} type="button" key={item.id} onClick={() => setSelectedClassId(item.id)} disabled={saving}><div className="file-icon"><UsersThree size={18} aria-hidden="true" /></div><div><strong>{item.name}</strong><span>{item.student_count ?? 0} 名学生 · 版本 v{item.version}</span></div><StatusPill tone={item.status === 'active' ? 'success' : 'neutral'}>{item.status === 'active' ? '启用' : '停用'}</StatusPill></button>) : <div className="empty-inline"><UsersThree size={24} aria-hidden="true" /><span>{loading ? '正在读取班级…' : '暂无班级，请先创建班级。'}</span></div>}</div><div className="class-detail-panel">{selectedClass ? <><div className="class-detail-header"><div><div className="eyebrow">班级维护</div><h3>{selectedClass.name}</h3><p>{selectedClass.students?.length ?? 0} 名学生 · 当前版本 v{selectedClass.version}</p></div><StatusPill tone={selectedClass.status === 'active' ? 'success' : 'neutral'}>{selectedClass.status === 'active' ? '启用' : '停用'}</StatusPill></div><form className="class-detail-form" onSubmit={saveClass}><label className="field"><span>班级名称 <em>*</em></span><input value={classDraftName} onChange={(event) => setClassDraftName(event.target.value)} disabled={saving || detailLoading} /></label><label className="field"><span>班级状态</span><select value={classDraftStatus} onChange={(event) => setClassDraftStatus(event.target.value as 'active' | 'inactive')} disabled={saving || detailLoading}><option value="active">启用</option><option value="inactive">停用</option></select></label><PrimaryButton type="submit" icon={Check} disabled={saving || detailLoading || !classDraftName.trim()}>{saving ? '保存中…' : '保存班级信息'}</PrimaryButton></form><form className="student-add-form" onSubmit={addStudent}><div className="section-label">手动添加学生</div><div className="student-add-fields"><label className="field"><span>学号 <em>*</em></span><input value={studentCode} onChange={(event) => setStudentCode(event.target.value)} placeholder="student_023" disabled={saving || selectedClass.status !== 'active'} /></label><label className="field"><span>姓名 <em>*</em></span><input value={studentName} onChange={(event) => setStudentName(event.target.value)} placeholder="张同学" disabled={saving || selectedClass.status !== 'active'} /></label><PrimaryButton type="submit" icon={Plus} disabled={saving || selectedClass.status !== 'active' || !studentCode.trim() || !studentName.trim()}>添加学生</PrimaryButton></div>{selectedClass.status !== 'active' && <small className="field-hint">停用班级不能新增学生；重新启用后再添加。</small>}</form><div className="student-maintenance-list"><div className="class-list-heading"><strong>学生名单</strong><span>{selectedClass.students?.length ?? 0} 人</span></div>{selectedClass.students?.length ? selectedClass.students.map((student) => editingStudentId === student.id ? <div className="student-maintenance-row editing" key={student.id}><input aria-label="编辑学生学号" value={editingStudentCode} onChange={(event) => setEditingStudentCode(event.target.value)} disabled={saving} /><input aria-label="编辑学生姓名" value={editingStudentName} onChange={(event) => setEditingStudentName(event.target.value)} disabled={saving} /><div><button className="text-button" type="button" onClick={() => void saveStudent(student.id)} disabled={saving || !editingStudentCode.trim() || !editingStudentName.trim()}>保存</button><button className="text-button muted" type="button" onClick={() => setEditingStudentId('')} disabled={saving}>取消</button></div></div> : <div className="student-maintenance-row" key={student.id}><div><strong>{student.display_name}</strong><span>{student.student_code}</span></div><StatusPill tone={student.status === 'active' ? 'success' : 'neutral'}>{student.status === 'active' ? '启用' : '停用'}</StatusPill><div className="student-row-actions"><button className="text-button" type="button" onClick={() => beginStudentEdit(student)} disabled={saving}>编辑</button><button className="text-button muted" type="button" onClick={() => void toggleStudentStatus(student)} disabled={saving}>{student.status === 'active' ? '停用' : '启用'}</button></div></div>) : <div className="empty-inline"><UsersThree size={24} aria-hidden="true" /><span>当前班级暂无学生。</span></div>}</div></> : <div className="empty-detail"><UsersThree size={30} aria-hidden="true" /><h3>{detailLoading ? '正在加载班级详情…' : '选择一个班级开始维护'}</h3><p>班级名称、状态和学生标识会通过正式 API 保存。</p></div>}</div></div></section>
}

function InfoRow({ label, value }: { label: string; value: string }) { return <div className="info-row"><span>{label}</span><strong>{value}</strong></div> }

function OcrReview({ notify, initialBatchId }: { notify: (message: string) => void; initialBatchId?: string }) {
  return <div className="content-wrap"><PageHeader eyebrow="批改流程 · 第 2/4 步" title="题干、答案与学生作业识别" description="按“上传题干与答案 → OCR 校对 → 学生作业 OCR 与批改 → 评分报告”完成当前批次。" action={<StatusPill tone="info">服务端 OCR 流程</StatusPill>} />
    <div className="workflow-strip"><WorkflowItem index="1" label="班级、批次与上传" done /><WorkflowItem index="2" label="题干/答案与 OCR 校对" current /><WorkflowItem index="3" label="学生 OCR 与批改" /><WorkflowItem index="4" label="评分与报告" /></div>
    <QuestionPaperWorkflow notify={notify} initialBatchId={initialBatchId} />
    <StudentOcrWorkflow notify={notify} initialBatchId={initialBatchId} />
    <div className="footer-note"><ShieldCheck size={16} aria-hidden="true" /><span>原始文件与原始 OCR 只读保存；学生归属、答案校对和确认操作都会生成可追溯版本。</span></div>
  </div>
}

type AnswerDraft = { present_on_page: boolean; answer_text: string; is_blank_confirmed: boolean; source_type: 'ocr' | 'manual' | 'teacher_corrected'; answer_photo_id?: string; ocr_block_ids?: string[] }

function StudentOcrWorkflow({ notify, initialBatchId }: { notify: (message: string) => void; initialBatchId?: string }) {
  const [batches, setBatches] = useState<AssignmentBatch[]>([])
  const [batchId, setBatchId] = useState('')
  const [batch, setBatch] = useState<AssignmentBatch | null>(null)
  const [students, setStudents] = useState<Array<{ id: string; student_code: string; display_name: string }>>([])
  const [pages, setPages] = useState<SourcePage[]>([])
  const [selectedPage, setSelectedPage] = useState<SourcePage | null>(null)
  const [previewImage, setPreviewImage] = useState<ImagePreviewTarget | null>(null)
  const [selectedQuestionId, setSelectedQuestionId] = useState('')
  const [answerDrafts, setAnswerDrafts] = useState<Record<string, AnswerDraft>>({})
  const [studentId, setStudentId] = useState('')
  const [pageSequence, setPageSequence] = useState('1')
  const [recognitionMode, setRecognitionMode] = useState<OcrRecognitionMode>('printed')
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [statusMessage, setStatusMessage] = useState('正在加载学生作业批次。')
  const [ocrTask, setOcrTask] = useState<UploadTask | null>(null)
  const [questionLayout, setQuestionLayout] = useState<QuestionLayout | null>(null)

  const questions = batch?.questions ?? []
  const currentSubjectLabel = displaySubject(batch?.subject)
  const currentQuestion = questions.find((item) => item.id === selectedQuestionId) ?? questions[0] ?? null
  const blocks = getOcrBlocks(selectedPage)
  const ocrTaskActive = ocrTask?.status === 'queued' || ocrTask?.status === 'running'
  const ocrProgress = ocrTask && ocrTask.progress_total > 0 ? Math.min(100, Math.round((ocrTask.progress_current / ocrTask.progress_total) * 100)) : 0
  const incompleteQuestions = questions.filter((question) => {
    const answer = answerDrafts[question.id]
    return !answer?.is_blank_confirmed && !(answer?.answer_text ?? '').trim()
  })
  const currentAnswer = currentQuestion ? answerDrafts[currentQuestion.id] ?? { present_on_page: true, answer_text: '', is_blank_confirmed: false, source_type: 'manual' as const } : null
  const groupingVersion = selectedPage?.grouping?.version ?? 1
  const selectedStudent = students.find((student) => student.id === studentId)
  const ocrStatus = selectedPage?.latest_ocr_run?.status
  const layoutIssues = questionLayoutIssues(questionLayout?.regions ?? [])
  const layoutReady = questionLayout?.status === 'confirmed' && layoutIssues.length === 0

  const explainError = (cause: unknown, fallback: string) => {
    setError(cause instanceof ApiClientError ? cause.message : fallback)
    setStatusMessage('操作未完成，请根据错误提示修正后重试。')
  }

  const hydratePage = (page: SourcePage, currentBatch: AssignmentBatch, layout = questionLayout) => {
    setSelectedPage(page)
    setStudentId(page.grouping?.student_id ?? '')
    setPageSequence(String(page.grouping?.page_sequence ?? page.page_index))
    const nextDrafts: Record<string, AnswerDraft> = {}
    const pageLayoutMatches = pageUsesQuestionLayout(page, layout)
    const pageBlocks = pageLayoutMatches ? getOcrBlocks(page) : []
    const autoAssignments = pageLayoutMatches ? inferOcrBlockAssignments(pageBlocks, currentBatch.questions) : {}
    currentBatch.questions.forEach((question) => {
      const relatedBlockIds = autoAssignments[question.id] ?? []
      const relatedBlocks = pageBlocks.filter((block) => relatedBlockIds.includes(block.id))
      const savedAnswer = page.saved_answers?.[question.id]
      const savedAnswerUsable = savedAnswer && (savedAnswer.source_type !== 'ocr' || savedAnswer.coverage_status === 'reviewed' || pageLayoutMatches) ? savedAnswer : null
      const autoText = studentAnswerFromOcrBlocks(question.question_type, relatedBlocks)
      const legacyAutoText = studentAnswerFromOcrBlocks(question.question_type, relatedBlocks, true)
      const savedText = savedAnswerUsable?.source_type === 'ocr'
        ? studentAnswerText(question.question_type, savedAnswerUsable.answer_text)
        : savedAnswerUsable?.answer_text
      const answerText = savedAnswerMatchesLegacyOcrOrder(savedAnswerUsable, legacyAutoText) ? autoText : savedText ?? autoText
      nextDrafts[question.id] = {
        present_on_page: relatedBlocks.length > 0 || Boolean(savedAnswerUsable),
        answer_text: answerText,
        is_blank_confirmed: savedAnswerUsable?.is_blank_confirmed ?? false,
        source_type: savedAnswerUsable?.source_type ?? (relatedBlocks.length > 0 ? 'ocr' : 'manual'),
        ocr_block_ids: relatedBlocks.map((block) => block.id),
      }
    })
    setAnswerDrafts(nextDrafts)
    setSelectedQuestionId((current) => currentBatch.questions.some((question) => question.id === current) ? current : currentBatch.questions[0]?.id ?? '')
  }

  const persistAutoOcrDraft = async (page: SourcePage, currentBatch: AssignmentBatch, layout = questionLayout) => {
    if (!page.grouping?.student_id || !page.grouping.assignment_group_id || page.latest_ocr_run?.status !== 'succeeded'
      || !pageUsesQuestionLayout(page, layout)) {
      return { page, savedCount: 0, error: '' }
    }
    const blocksForPage = getOcrBlocks(page)
    if (!blocksForPage.length) return { page, savedCount: 0, error: '' }
    const assignments = inferOcrBlockAssignments(blocksForPage, currentBatch.questions)
    const pendingAnswers = currentBatch.questions.filter((question) => {
      const saved = page.saved_answers?.[question.id]
      const assignedBlocks = blocksForPage.filter((block) => (assignments[question.id] ?? []).includes(block.id))
      const autoText = studentAnswerFromOcrBlocks(question.question_type, assignedBlocks).trim()
      return shouldSyncAutoOcrDraft(saved, autoText)
    })
    if (!pendingAnswers.length) return { page, savedCount: 0, error: '' }

    try {
      const response = await api.pages.saveCorrection(page.id, {
        answers: currentBatch.questions.map((question) => {
          const saved = page.saved_answers?.[question.id]
          const assignedBlocks = blocksForPage.filter((block) => (assignments[question.id] ?? []).includes(block.id))
          const autoText = studentAnswerFromOcrBlocks(question.question_type, assignedBlocks)
          if (!shouldSyncAutoOcrDraft(saved, autoText)) {
            return {
              question_id: question.id,
              // 已保存的教师修改或未变化的 OCR 保持当前版本，不被自动识别覆盖。
              present_on_page: false,
              answer_text: '',
              is_blank_confirmed: false,
              source_type: 'manual' as const,
            }
          }
          return {
            question_id: question.id,
            present_on_page: Boolean(autoText.trim()),
            answer_text: autoText,
            is_blank_confirmed: false,
            source_type: 'ocr' as const,
          }
        }),
        save_mode: 'draft',
        version: page.grouping.version,
      })
      return { page: response.data, savedCount: pendingAnswers.length, error: '' }
    } catch (cause) {
      return {
        page,
        savedCount: 0,
        error: cause instanceof ApiClientError ? cause.message : 'OCR 文本自动保存失败，可稍后手动保存校对草稿。',
      }
    }
  }

  const loadBatches = async () => {
    setLoading(true)
    try {
      const response = await api.batches.list({ page_size: 100 })
      setBatches(response.data)
      const preferredBatch = initialBatchId ? response.data.find((item) => item.id === initialBatchId) : null
      if (!batchId && (preferredBatch || response.data[0])) setBatchId((preferredBatch || response.data[0]).id)
      setError('')
      setStatusMessage(response.data.length ? '请选择要校对学生作业的批次。' : '暂无批次，请先在作业管理中创建批次并上传学生作业。')
    } catch (cause) {
      explainError(cause, '无法加载批次，请检查登录状态和后端服务。')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadBatches()
  }, [initialBatchId])

  useEffect(() => {
    setBatch(null)
    setPages([])
    setSelectedPage(null)
    setStudents([])
    setAnswerDrafts({})
    setQuestionLayout(null)
    if (!batchId) return
    let cancelled = false
    const loadBatchData = async () => {
      setLoading(true)
      try {
        const batchResponse = await api.batches.get(batchId)
        const [pageResponse, studentResponse, taskResponse, layoutResponse] = await Promise.all([
          api.batches.pages(batchId, undefined, 'student_work'),
          api.classes.students(batchResponse.data.class_id),
          api.batches.ocrTask(batchId, 'student_work'),
          api.batches.questionLayout(batchId),
        ])
        const firstPageDetail = pageResponse.data[0] ? (await api.pages.get(pageResponse.data[0].id)).data : null
        if (cancelled) return
        setBatch(batchResponse.data)
        setRecognitionMode(recommendedStudentOcrMode(batchResponse.data.subject))
        setPages(pageResponse.data)
        setStudents(studentResponse.data)
        setOcrTask(taskResponse.data)
        setQuestionLayout(layoutResponse.data)
        const firstPageResult = firstPageDetail ? await persistAutoOcrDraft(firstPageDetail, batchResponse.data, layoutResponse.data) : null
        if (cancelled) return
        if (firstPageDetail) hydratePage(firstPageResult?.page ?? firstPageDetail, batchResponse.data, layoutResponse.data)
        setStatusMessage(firstPageResult?.savedCount
          ? `OCR 已按题目版式自动归属并保存 ${firstPageResult.savedCount} 道题的校对草稿，请核对文本。`
          : firstPageResult?.error
          || (!layoutResponse.data ? '题目位置模板尚未确认；请先在上方题目 OCR 区确认模板，再开始学生作业识别。'
            : questionLayoutIssues(layoutResponse.data.regions).length ? '检测到已保存的题目区域重叠；旧 OCR 草稿已隐藏以防串题，请先在“题干与 OCR”中校准答题区域，再重新识别。'
            : firstPageDetail && firstPageDetail.latest_ocr_run?.status === 'succeeded' && !pageUsesQuestionLayout(firstPageDetail, layoutResponse.data) ? '题目模板已更新；当前页面 OCR 仍使用旧模板，答案草稿已隐藏，请重新识别学生作业。'
            : pageResponse.data.length ? '已加载学生作业页面；请先确认学生归属，再保存答案校对。' : '该批次暂无学生作业页面，请先上传 JPG、PNG 或 PDF。'))
        setError('')
      } catch (cause) {
        if (!cancelled) explainError(cause, '无法加载批次学生作业，请确认批次 ID 正确。')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void loadBatchData()
    return () => { cancelled = true }
  }, [batchId])

  const refreshPage = async () => {
    if (!batchId) return
    setWorking(true)
    try {
      const [pageResponse, taskResponse, layoutResponse] = await Promise.all([
        api.batches.pages(batchId, undefined, 'student_work'),
        api.batches.ocrTask(batchId, 'student_work'),
        api.batches.questionLayout(batchId),
      ])
      setPages(pageResponse.data)
      setOcrTask(taskResponse.data)
      setQuestionLayout(layoutResponse.data)
      const nextPage = selectedPage ? pageResponse.data.find((page) => page.id === selectedPage.id) ?? pageResponse.data[0] : pageResponse.data[0]
      const nextPageDetail = nextPage && batch ? (await api.pages.get(nextPage.id)).data : null
      const nextPageResult = nextPageDetail && batch ? await persistAutoOcrDraft(nextPageDetail, batch, layoutResponse.data) : null
      if (nextPageDetail && batch) hydratePage(nextPageResult?.page ?? nextPageDetail, batch, layoutResponse.data)
      setStatusMessage(nextPageResult?.savedCount ? `OCR 已按题目版式自动归属并保存 ${nextPageResult.savedCount} 道题的校对草稿，请核对文本。` : nextPageResult?.error || (!layoutResponse.data ? '题目位置模板尚未确认；请先完成题目 OCR。' : questionLayoutIssues(layoutResponse.data.regions).length ? '题目区域重叠，现有 OCR 草稿已隐藏；校准区域后重新识别。' : nextPageDetail?.latest_ocr_run?.status === 'succeeded' && !pageUsesQuestionLayout(nextPageDetail, layoutResponse.data) ? '当前页面的 OCR 使用旧模板，答案草稿已隐藏；请重新识别当前页面。' : (pageResponse.data.length ? '学生作业页面已刷新。若 OCR 已完成，可继续校对。' : '暂无学生作业页面。')))
      setError('')
    } catch (cause) {
      explainError(cause, '刷新学生作业页面失败，请稍后重试。')
    } finally {
      setWorking(false)
    }
  }

  const selectPage = async (page: SourcePage) => {
    if (!batch) return
    setWorking(true)
    try {
      const pageDetail = (await api.pages.get(page.id)).data
      const pageResult = await persistAutoOcrDraft(pageDetail, batch)
      hydratePage(pageResult.page, batch)
      setStatusMessage(pageResult.savedCount ? `OCR 已自动归属并保存 ${pageResult.savedCount} 道题的校对草稿，请核对文本。` : pageResult.error || '已加载当前页面详情。')
      setError('')
    } catch (cause) {
      explainError(cause, '页面详情加载失败，请重试。')
    } finally {
      setWorking(false)
    }
  }

  const startBatchOcr = async () => {
    if (!batchId || !pages.length) return
    if (!layoutReady) {
      setError('请先完成题目页 OCR，并确认题目数量和位置模板。')
      setStatusMessage('学生作业 OCR 尚未提交。题目位置模板确认后，系统才会按题目区域识别手写内容。')
      return
    }
    setWorking(true)
    try {
      const response = await api.batches.startOcr(batchId, { recognition_mode: recognitionMode }, createIdempotencyKey(), 'student_work')
      setOcrTask(response.data.task)
      setStatusMessage(response.data.reused ? '已复用学生作业 OCR 任务，请刷新页面查看状态。' : '学生作业 OCR 已入队，请 Worker 完成后刷新页面。')
      notify('学生作业 OCR 任务已提交')
      setError('')
    } catch (cause) {
      explainError(cause, '学生作业 OCR 提交失败，请先确认文件已成功上传。')
    } finally {
      setWorking(false)
    }
  }

  useEffect(() => {
    if (!batchId || !ocrTask || !ocrTaskActive) return
    let cancelled = false
    let requestInFlight = false
    const pollTask = async () => {
      if (cancelled || requestInFlight) return
      requestInFlight = true
      try {
        const response = await api.tasks.get(ocrTask.task_id)
        if (cancelled) return
        const nextTask = response.data
        setOcrTask(nextTask)
        if (nextTask.status === 'queued' || nextTask.status === 'running') {
          setStatusMessage(`学生作业 OCR ${nextTask.status === 'queued' ? '排队中' : '处理中'}…${nextTask.progress_total ? ` ${nextTask.progress_current}/${nextTask.progress_total} 页` : ''}`)
          return
        }
        await refreshPage()
        if (cancelled) return
        if (nextTask.status === 'succeeded') {
          setStatusMessage(`学生作业 OCR 已完成：${nextTask.progress_current}/${nextTask.progress_total || nextTask.progress_current} 页成功。`)
        } else if (nextTask.status === 'partial_failed') {
          setError(nextTask.error_message || '部分学生作业页面 OCR 失败，请查看失败页面并重试。')
          setStatusMessage('学生作业 OCR 部分完成，请处理失败页面。')
        } else {
          setError(nextTask.error_message || '学生作业 OCR 失败，请检查 Worker 和图片。')
          setStatusMessage('学生作业 OCR 未完成，请根据错误提示处理后重试。')
        }
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiClientError ? cause.message : '读取学生作业 OCR 进度失败，页面会继续重试。')
      } finally {
        requestInFlight = false
      }
    }
    void pollTask()
    const timer = window.setInterval(() => void pollTask(), 1200)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [batchId, ocrTask?.task_id, ocrTask?.status])

  const retryPageOcr = async () => {
    if (!selectedPage) return
    if (!layoutReady) {
      setError('请先确认题目位置模板，再重试学生作业 OCR。')
      return
    }
    setWorking(true)
    try {
      const response = await api.pages.retryOcr(selectedPage.id, { recognition_mode: recognitionMode }, createIdempotencyKey())
      setStatusMessage(response.data.reused ? '已复用当前页面 OCR 任务。' : '当前页面 OCR 已重新入队，请稍后刷新。')
      notify('当前页面 OCR 重试已提交')
      setError('')
    } catch (cause) {
      explainError(cause, '当前页面 OCR 重试失败，请检查页面状态。')
    } finally {
      setWorking(false)
    }
  }

  const saveGrouping = async () => {
    if (!selectedPage || !studentId || !pageSequence.trim()) return
    const sequence = Number(pageSequence)
    if (!Number.isInteger(sequence) || sequence < 1) {
      setError('合并页序必须是大于等于 1 的整数。')
      setStatusMessage('操作未完成，请修正合并页序后重试。')
      return
    }
    setWorking(true)
    try {
      const response = await api.pages.updateGrouping(selectedPage.id, {
        student_id: studentId,
        // 只有当前作业对象已经属于所选学生时才复用它；切换学生必须让后端
        // 按新学生查找或创建作业对象，否则会触发旧作业对象与新学生冲突。
        assignment_group_id: selectedPage.grouping?.student_id === studentId ? selectedPage.grouping.assignment_group_id ?? null : null,
        page_sequence: sequence,
        reason: 'teacher_manual_assignment',
        version: groupingVersion,
      })
      const pageResult = batch ? await persistAutoOcrDraft(response.data, batch) : { page: response.data, savedCount: 0, error: '' }
      if (batch) hydratePage(pageResult.page, batch)
      setStatusMessage(pageResult.savedCount ? `已将当前页面归属到 ${selectedStudent?.display_name ?? '所选学生'}，并自动保存 ${pageResult.savedCount} 道题的 OCR 校对草稿。` : pageResult.error || `已将当前页面归属到 ${selectedStudent?.display_name ?? '所选学生'}，页序为第 ${pageSequence} 页。`)
      setError('')
      notify('学生归属已保存')
    } catch (cause) {
      explainError(cause, '学生归属保存失败，请刷新后重试。')
    } finally {
      setWorking(false)
    }
  }

  const updateAnswer = (questionId: string, changes: Partial<AnswerDraft>) => {
    setAnswerDrafts((current) => ({ ...current, [questionId]: { ...(current[questionId] ?? { present_on_page: true, answer_text: '', is_blank_confirmed: false, source_type: 'manual' }), ...changes } }))
  }

  const saveCorrection = async (saveMode: 'draft' | 'confirm') => {
    if (!selectedPage || !batch || !selectedPage.grouping?.student_id || !selectedPage.grouping.assignment_group_id) return
    if (saveMode === 'confirm' && incompleteQuestions.length > 0) {
      setError(`还有 ${incompleteQuestions.length} 道题未完成：${incompleteQuestions.map((question) => `第${question.question_no}题`).join('、')}。请填写答案或勾选“确认本题为空题”。`)
      setStatusMessage('当前页面暂不能确认，请先完成答案覆盖。')
      return
    }
    setWorking(true)
    try {
      const response = await api.pages.saveCorrection(selectedPage.id, {
        answers: batch.questions.map((question) => {
          const answer = answerDrafts[question.id] ?? { present_on_page: false, answer_text: '', is_blank_confirmed: false, source_type: 'manual' as const }
          return {
            question_id: question.id,
            present_on_page: answer.present_on_page,
            answer_text: answer.answer_text,
            is_blank_confirmed: answer.is_blank_confirmed,
            source_type: answer.source_type,
          }
        }),
        save_mode: saveMode,
        version: groupingVersion,
      })
      // 学生端只保存自动归属后的答案文本和教师核对结果；批次共享参考答案不在这里重复上传。
      setSelectedPage(response.data)
      setStudentId(response.data.grouping?.student_id ?? studentId)
      setPageSequence(String(response.data.grouping?.page_sequence ?? pageSequence))
      setStatusMessage(saveMode === 'confirm' ? '当前页面已确认，结果可以进入后续批改流程。' : '当前页面校对草稿已保存，原始 OCR 仍保留。')
      setError('')
      notify(saveMode === 'confirm' ? '页面校对已确认' : '页面校对草稿已保存')
    } catch (cause) {
      explainError(cause, 'OCR 校对保存失败，请检查归属、页序和版本后重试。')
    } finally {
      setWorking(false)
    }
  }

  return <><section className="panel student-ocr-workflow" aria-labelledby="student-ocr-title">
    <div className="panel-heading">
      <div><div className="eyebrow">批改流程 · 第 3 步前置</div><h2 id="student-ocr-title">学生作业预处理、OCR 与答案校对</h2><p>先确认学生归属和页序，再检查 OCR 文本；每道题必须填写答案或明确确认空题后，才能进入批改。</p></div>
      <StatusPill tone={ocrTaskActive ? 'info' : ocrStatus === 'succeeded' ? 'success' : ocrStatus === 'failed' ? 'danger' : pages.length ? 'warning' : 'neutral'}>{ocrTaskActive ? `OCR ${ocrTask.status === 'queued' ? '排队中' : '处理中'}` : ocrStatus ?? (pages.length ? '待识别' : '未上传作业')}</StatusPill>
    </div>
    <div className="student-ocr-toolbar">
      <label className="field"><span>正式批次 <em>*</em></span><select value={batchId} onChange={(event) => setBatchId(event.target.value)} disabled={loading || working}><option value="">{loading ? '正在加载批次…' : batches.length ? '请选择批次' : '暂无批次'}</option>{batches.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.class_name || item.class_id}</option>)}</select></label>
      <label className="field"><span>识别模式 · {currentSubjectLabel}</span><select value={recognitionMode} onChange={(event) => setRecognitionMode(event.target.value as typeof recognitionMode)} disabled={working}><option value="printed">中英混合印刷体 · PaddleOCR</option><option value="chinese_handwriting">中文手写 · PP-OCRv5</option><option value="english_handwriting">英文手写 · TrOCR</option><option value="math_handwriting">数学混合题面 · PaddleOCR + TexTeller</option></select><small>印刷体默认同时识别中文和英文；数学页面会同时保留中文文本并识别公式，手写作业请按主要书写语言切换模型。</small></label>
      <div className="question-paper-actions"><SecondaryButton icon={ArrowsClockwise} onClick={() => void loadBatches()} disabled={loading || working}>刷新批次</SecondaryButton><SecondaryButton icon={ArrowsClockwise} onClick={() => void refreshPage()} disabled={!batchId || working}>刷新页面</SecondaryButton><PrimaryButton icon={Scan} onClick={startBatchOcr} disabled={!batchId || !pages.length || !layoutReady || working}>{working ? '处理中…' : layoutReady ? '开始批次 OCR' : '先确认题目版式'}</PrimaryButton></div>
    </div>
    <div className={`workflow-message ${layoutReady ? '' : 'warning'}`} role="status"><Info size={17} aria-hidden="true" /><span>{layoutReady ? `题目答题区域已校准（v${questionLayout?.version}），学生页将按 ${questionLayout?.regions.length ?? 0} 个区域分别识别，结果自动回填后供教师核对。` : questionLayout ? `当前题目模板存在区域重叠，系统已暂停识别并隐藏未复核的旧 OCR 草稿，避免把其他题答案写入本题。${layoutIssues.map((issue) => issue.message).join(' ')}` : '学生作业识别前置：请在“题干与 OCR”区域完成题目页识别并校准答题区域；未确认前不会提交学生 OCR。'}</span></div>
    {ocrTask && <div className={`ocr-task-progress ${ocrTask.status === 'failed' ? 'failed' : ocrTask.status === 'partial_failed' ? 'partial' : ocrTask.status === 'succeeded' ? 'complete' : ''}`} role="status" aria-live="polite"><div className="ocr-task-progress-head"><div><strong>学生作业 OCR 进度</strong><span>{ocrTask.status === 'queued' ? '等待 Worker 处理' : ocrTask.status === 'running' ? '预处理、候选区域提取与模型识别中' : ocrTask.status === 'succeeded' ? '识别完成，可进入人工核对' : ocrTask.error_message || '识别任务未完成'}</span></div><StatusPill tone={ocrTask.status === 'succeeded' ? 'success' : ocrTask.status === 'failed' ? 'danger' : ocrTask.status === 'partial_failed' ? 'warning' : 'info'}>{ocrTask.status === 'queued' ? '排队中' : ocrTask.status === 'running' ? '处理中' : ocrTask.status === 'succeeded' ? '已完成' : ocrTask.status === 'partial_failed' ? '部分失败' : '失败'}</StatusPill></div><div className="ocr-task-progress-track" aria-label={`学生作业 OCR 进度 ${ocrProgress}%`}><span style={{ width: `${ocrProgress}%` }} /></div><div className="ocr-task-progress-meta"><span>{ocrTask.progress_current} / {ocrTask.progress_total || '—'} 页</span><strong>{ocrTask.progress_total ? `${ocrProgress}%` : '等待统计'}</strong></div>{ocrTask.error_message && <small className="ocr-task-error">{ocrTask.error_message}</small>}</div>}
    {error && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{error}</span><button className="text-button" type="button" onClick={() => void refreshPage()} disabled={!batchId || working}>重试</button></div>}
     <div className="workflow-message" role="status" aria-live="polite"><Info size={17} aria-hidden="true" /><span>{statusMessage}</span></div>
     {questionLayout && layoutReady && <div className="workflow-message layout-ready" role="status"><CheckCircle size={17} weight="fill" aria-hidden="true" /><span>题目答题模板可用（v{questionLayout.version}）：{questionLayout.regions.length} 个区域已保存，后续学生作业共用本模板。</span></div>}
    <div className="student-ocr-layout">
      <div className="student-paper-column"><div className="student-paper-preview">{selectedPage ? selectedPage.original_name.toLowerCase().endsWith('.pdf') ? <iframe title="学生作业 PDF 原图" src={api.pages.originalUrl(selectedPage.id)} /> : <ZoomableImage src={api.pages.originalUrl(selectedPage.id)} alt={`${selectedPage.original_name} 第 ${selectedPage.page_index} 页原图`} onOpen={() => setPreviewImage({ src: api.pages.originalUrl(selectedPage.id), alt: `${selectedPage.original_name} 第 ${selectedPage.page_index} 页原图`, title: `学生作业原图 · 第 ${selectedPage.page_index} 页` })} /> : <div className="empty-inline"><FileText size={27} aria-hidden="true" /><span>选择批次和页面后显示原始作业。</span></div>}</div><div className="student-page-list" aria-label="学生作业页面列表">{pages.map((page) => <button key={page.id} className={`paper-page-item ${selectedPage?.id === page.id ? 'active' : ''}`} type="button" onClick={() => void selectPage(page)} disabled={working}><span>第 {page.page_index} 页 · {page.original_name}</span><StatusPill tone={page.latest_ocr_run?.status === 'succeeded' ? 'success' : page.latest_ocr_run?.status === 'failed' ? 'danger' : 'warning'}>{page.latest_ocr_run?.status ?? page.status}</StatusPill></button>)}</div></div>
      <div className="student-ocr-editor">
        <div className="student-grouping-fields"><label className="field"><span>学生归属 <em>*</em></span><select value={studentId} onChange={(event) => setStudentId(event.target.value)} disabled={!selectedPage || working}><option value="">请选择学生</option>{students.map((student) => <option key={student.id} value={student.id}>{student.display_name} · {student.student_code}</option>)}</select></label><label className="field"><span>合并页序 <em>*</em></span><input type="number" min="1" value={pageSequence} onChange={(event) => setPageSequence(event.target.value)} disabled={!selectedPage || working} /><small>{selectedPage?.grouping ? `归属状态：${selectedPage.grouping.grouping_status} · 版本 v${groupingVersion}` : '当前页面尚未建立学生作业对象'}</small></label><SecondaryButton onClick={saveGrouping} disabled={!selectedPage || !studentId || !pageSequence || working}>保存归属</SecondaryButton></div>
        <div className="question-tabs" role="tablist" aria-label="学生作业题目列表">{questions.map((question) => { const answer = answerDrafts[question.id]; const complete = Boolean(answer?.is_blank_confirmed || (answer?.answer_text ?? '').trim()); return <button key={question.id} className={`question-tab ${currentQuestion?.id === question.id ? 'active' : ''}`} type="button" role="tab" aria-selected={currentQuestion?.id === question.id} onClick={() => setSelectedQuestionId(question.id)}><span>第 {question.question_no} 题</span><small>{complete ? '已确认' : '待填写'}</small></button> })}</div>
        {questions.length > 0 && <div className="workflow-message" role="status"><Info size={17} aria-hidden="true" /><span>{incompleteQuestions.length ? `答案覆盖：${questions.length - incompleteQuestions.length}/${questions.length} 题已完成；还需处理 ${incompleteQuestions.map((question) => `第${question.question_no}题`).join('、')}` : '答案覆盖已完整，可以确认当前页面并进入批改。'}</span></div>}
         {currentQuestion && <section className="student-ocr-candidates" aria-labelledby="student-ocr-candidates-title">
           <div className="student-ocr-candidates-head">
             <div><strong id="student-ocr-candidates-title">系统自动归属的 OCR 文本块</strong><span>系统根据题号、版面顺序和当前批次题目自动归属；教师只需核对下方完整文本，缺失时直接补录。</span></div>
             <StatusPill tone={currentAnswer?.ocr_block_ids?.length ? 'info' : 'warning'}>{currentAnswer?.ocr_block_ids?.length ? `自动归属 ${currentAnswer.ocr_block_ids.length} 个` : '未找到自动候选'}</StatusPill>
           </div>
           <div className="student-ocr-candidate-list">
             {(currentAnswer?.ocr_block_ids ?? []).map((blockId) => blocks.find((block) => block.id === blockId)).filter((block): block is OcrBlock => Boolean(block)).map((block) => <div key={block.id} className="ocr-block-option selected system-assigned">
               <span><strong>文本块 {block.block_index} · 系统归属</strong><small>{block.text_raw?.trim() || '无可见文本'}</small></span>
               <em>{block.confidence != null && Number.isFinite(Number(block.confidence)) ? `${Math.round(Number(block.confidence) * 100)}%` : '—'}</em>
             </div>)}
             {!currentAnswer?.ocr_block_ids?.length && <div className="empty-inline"><Scan size={24} aria-hidden="true" /><span>未识别到可自动归属的文本块，请在下方补录或确认本题为空题。</span></div>}
           </div>
         </section>}
          {currentQuestion && currentAnswer ? <div className="student-answer-editor"><div className="panel-heading"><div><h3>第 {currentQuestion.question_no} 题 · {currentQuestion.question_type === 'objective' ? '客观题' : '主观题'}</h3><p>{blocks.length ? `当前页面已完成预处理、自动归属和 OCR，共 ${blocks.length} 个文本块；请人工核对合并文本。` : '当前页面尚无 OCR 文本块，可手动录入并保存。'}</p></div><StatusPill tone={currentAnswer.source_type === 'ocr' ? 'info' : 'warning'}>{currentAnswer.source_type === 'ocr' ? '自动 OCR' : '教师编辑'}</StatusPill></div><div className="workflow-message compact"><Info size={16} aria-hidden="true" /><span>本批次参考答案已在题目端一次上传并绑定，所有学生共用；这里仅核对学生作答文本。</span></div><label className="field"><span>学生作答文本</span><textarea value={currentAnswer.answer_text} onChange={(event) => updateAnswer(currentQuestion.id, { answer_text: event.target.value, source_type: 'teacher_corrected', is_blank_confirmed: false, ocr_block_ids: [], present_on_page: true })} disabled={!selectedPage || working} placeholder={currentQuestion.question_type === 'objective' ? '只自动回填唯一识别出的选项字母（如 A、D）；无法确定时请核对原图后填写' : '核对系统自动合并的文本；缺失内容可直接补录'} /></label>{currentQuestion.question_type === 'objective' && <small className="field-hint">客观题不会把印刷选项正文当作学生答案；区域里出现多个选项或整段文字时会留空，请按原图核对。</small>}<label className="blank-answer-option"><input type="checkbox" checked={currentAnswer.is_blank_confirmed} onChange={(event) => updateAnswer(currentQuestion.id, { is_blank_confirmed: event.target.checked, answer_text: event.target.checked ? '' : currentAnswer.answer_text, source_type: 'teacher_corrected', ocr_block_ids: [], present_on_page: true })} disabled={!selectedPage || working} /><span>确认本题为空题</span></label><div className="student-editor-actions"><SecondaryButton onClick={() => void saveCorrection('draft')} disabled={!selectedPage?.grouping?.student_id || working}>保存校对草稿</SecondaryButton><PrimaryButton icon={Check} onClick={() => void saveCorrection('confirm')} disabled={!selectedPage?.grouping?.student_id || !selectedPage?.grouping?.assignment_group_id || incompleteQuestions.length > 0 || working}>确认本页并进入批改</PrimaryButton><SecondaryButton icon={ArrowsClockwise} onClick={() => void retryPageOcr()} disabled={!selectedPage || !layoutReady || working}>重试当前页 OCR</SecondaryButton></div></div> : <div className="empty-inline"><Scan size={27} aria-hidden="true" /><span>{selectedPage ? '当前批次暂无题目配置。' : '选择学生作业页面后开始校对。'}</span></div>}
      </div>
    </div>
  </section>{previewImage && <ImageLightbox target={previewImage} onClose={() => setPreviewImage(null)} />}</>
}

type PaperWorkflowState = 'idle' | 'loading' | 'uploading' | 'starting-ocr' | 'analyzing-structure' | 'confirming-structure' | 'saving'

const recognitionModeLabels: Record<OcrRecognitionMode, string> = {
  printed: '中英混合印刷体 · PaddleOCR',
  chinese_handwriting: '中文手写 · PP-OCRv5',
  english_handwriting: '英文手写 · TrOCR',
  math_handwriting: '数学混合题面 · PaddleOCR + TexTeller',
}

const ocrTaskStatusLabels: Record<string, string> = {
  queued: '排队中',
  running: '处理中',
  succeeded: '已完成',
  partial_failed: '部分失败',
  failed: '失败',
}

const ocrTaskStageLabels: Record<string, string> = {
  queued: '等待 OCR Worker 领取任务',
  preprocessing: '正在预处理试卷页面',
  extracting_regions: '正在提取文字区域',
  recognizing: '正在识别文字内容',
  saving: '正在保存识别结果',
}

function ocrTaskStatusLabel(status: string) {
  return ocrTaskStatusLabels[status] ?? status
}

function ocrTaskStageLabel(task: UploadTask) {
  return ocrTaskStageLabels[task.stage] ?? (task.status === 'queued' ? '等待 OCR Worker 领取任务' : '正在处理试卷')
}

function QuestionPaperWorkflow({ notify, initialBatchId }: { notify: (message: string) => void; initialBatchId?: string }) {
  const [batches, setBatches] = useState<Awaited<ReturnType<typeof api.batches.list>>['data']>([])
  const [batchId, setBatchId] = useState('')
  const [batchIdInput, setBatchIdInput] = useState('')
  const [batch, setBatch] = useState<Awaited<ReturnType<typeof api.batches.get>>['data'] | null>(null)
  const [pages, setPages] = useState<SourcePage[]>([])
  const [questionPaperFiles, setQuestionPaperFiles] = useState<SourceFile[]>([])
  const [hasStudentWorkFiles, setHasStudentWorkFiles] = useState(false)
  const [questionPaperMatchesStudentWork, setQuestionPaperMatchesStudentWork] = useState(false)
  const [ocrTask, setOcrTask] = useState<UploadTask | null>(null)
  const [questionPaperDeleteCandidate, setQuestionPaperDeleteCandidate] = useState<SourceFile | null>(null)
  const [questionPaperDeletingFileId, setQuestionPaperDeletingFileId] = useState('')
  const [selectedPage, setSelectedPage] = useState<SourcePage | null>(null)
  const [selectedQuestionId, setSelectedQuestionId] = useState('')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [previewImage, setPreviewImage] = useState<ImagePreviewTarget | null>(null)
  const [recognitionMode, setRecognitionMode] = useState<OcrRecognitionMode>('printed')
  const [selectedBlockIds, setSelectedBlockIds] = useState<string[]>([])
  const [promptDraft, setPromptDraft] = useState('')
  const [referenceDraft, setReferenceDraft] = useState('')
  const [structureResult, setStructureResult] = useState<QuestionStructureResult | null>(null)
  const [questionLayout, setQuestionLayout] = useState<QuestionLayout | null>(null)
  const [layoutDraft, setLayoutDraft] = useState<QuestionLayoutDraft | null>(null)
  const [layoutSaving, setLayoutSaving] = useState(false)
  const [state, setState] = useState<PaperWorkflowState>('idle')
  const [loadingBatches, setLoadingBatches] = useState(true)
  const [error, setError] = useState('')
  const [statusMessage, setStatusMessage] = useState('尚未加载试卷。请先选择正式批次并上传题目文件。')

  const configuredQuestions = useMemo(() => batch?.questions ?? [], [batch])
  const currentSubjectLabel = displaySubject(batch?.subject)
  const blocks: OcrBlock[] = getOcrBlocks(selectedPage)
  const selectedQuestion = configuredQuestions.find((item) => item.id === selectedQuestionId) ?? null
  const hasOcr = selectedPage?.latest_ocr_run?.status === 'succeeded' && blocks.length > 0
  const ocrTaskActive = ocrTask?.status === 'queued' || ocrTask?.status === 'running'
  const ocrProgress = ocrTask && ocrTask.progress_total > 0 ? Math.min(100, Math.round((ocrTask.progress_current / ocrTask.progress_total) * 100)) : 0
  const layoutDraftIssues = layoutDraft ? questionLayoutIssues(layoutDraft.regions) : []
  const layoutImageUrls = layoutDraft ? Object.fromEntries(layoutDraft.source_pages.map((page) => [page.source_page_id, api.pages.originalUrl(page.source_page_id)])) : {}

  const showError = (value: unknown, fallback: string) => {
    const message = value instanceof ApiClientError ? `${value.message}${value.requestId ? `（请求号：${value.requestId}）` : ''}` : fallback
    setError(message)
    setStatusMessage('操作未完成，请根据错误提示修正后重试。')
  }

  const loadBatches = async () => {
    setLoadingBatches(true)
    try {
      const response = await api.batches.list({ page_size: 100 })
      setBatches(response.data)
      setError('')
      const preferredBatch = initialBatchId ? response.data.find((item) => item.id === initialBatchId) : null
      if (!batchId && (preferredBatch || response.data[0])) setBatchId((preferredBatch || response.data[0]).id)
      setStatusMessage(response.data.length ? '请选择要维护题干的正式批次。' : '暂无可用批次，请先在作业管理中创建批次。')
    } catch (cause) {
      showError(cause, '无法加载批次。请先登录后端账号，或检查 API 服务是否启动。')
    } finally {
      setLoadingBatches(false)
    }
  }

  useEffect(() => {
    void loadBatches()
  }, [initialBatchId])

  useEffect(() => {
    setBatchIdInput(batchId)
  }, [batchId])

  useEffect(() => {
    setBatch(null)
    setPages([])
    setQuestionPaperFiles([])
    setHasStudentWorkFiles(false)
    setQuestionPaperMatchesStudentWork(false)
    setOcrTask(null)
    setQuestionPaperDeleteCandidate(null)
    setSelectedFile(null)
    setSelectedPage(null)
    setSelectedQuestionId('')
    setSelectedBlockIds([])
    setPromptDraft('')
    setReferenceDraft('')
    setStructureResult(null)
    setQuestionLayout(null)
    setLayoutDraft(null)
    if (!batchId) return
    let cancelled = false
    const loadBatchData = async () => {
      setState('loading')
      try {
        const [batchResponse, pageResponse, fileResponse, studentFileResponse, taskResponse, layoutResponse] = await Promise.all([
          api.batches.get(batchId),
          api.batches.pages(batchId, undefined, 'question_paper'),
          api.batches.files(batchId, undefined, 'question_paper'),
          api.batches.files(batchId, undefined, 'student_work'),
          api.batches.ocrTask(batchId, 'question_paper'),
          api.batches.questionLayout(batchId),
        ])
        if (cancelled) return
        setBatch(batchResponse.data)
        const questionPaperRecognitionMode = recommendedQuestionPaperOcrMode(batchResponse.data.subject)
        setRecognitionMode(questionPaperRecognitionMode)
        setPages(pageResponse.data)
        setQuestionPaperFiles(fileResponse.data)
        setHasStudentWorkFiles(studentFileResponse.data.length > 0)
        setQuestionPaperMatchesStudentWork(hasMatchingFileContent(fileResponse.data, studentFileResponse.data))
        setOcrTask(taskResponse.data)
        setQuestionLayout(layoutResponse.data)
        if (layoutResponse.data && questionLayoutIssues(layoutResponse.data.regions).length) {
          setLayoutDraft(questionLayoutDraftFromSaved(layoutResponse.data, batchResponse.data.questions))
        }
        let initialOcrTask = taskResponse.data
        let autoStartedOcr = false
        if (!initialOcrTask && pageResponse.data.some((page) => page.status === 'pending') && fileResponse.data.some((file) => file.status === 'success')) {
          try {
            const autoStartResponse = await api.batches.startOcr(batchId, { recognition_mode: questionPaperRecognitionMode }, createIdempotencyKey(), 'question_paper')
            initialOcrTask = autoStartResponse.data.task
            autoStartedOcr = true
            if (!cancelled) {
              setOcrTask(initialOcrTask)
              setStatusMessage('检测到尚未识别的题目页面，题目 OCR 已自动启动；完成后会自动生成题目区域。')
            }
          } catch {
            // 自动启动失败时保留页面，让教师仍可点击“开始试卷 OCR”查看明确错误。
          }
        }
        const firstQuestion = batchResponse.data.questions[0]
        setSelectedQuestionId(firstQuestion?.id ?? '')
        const pageDetails = await Promise.all(pageResponse.data.map((page) => api.pages.get(page.id).then((response) => response.data)))
        const firstPageDetail = pageDetails[0] ?? null
        if (cancelled) return
        setPages(pageDetails)
        setSelectedPage(firstPageDetail)
        const firstPageGroup = firstPageDetail ? pageDetails.filter((page) => page.source_file_id === firstPageDetail.source_file_id) : []
        const firstPageBlocks = getOrderedOcrBlocks(firstPageGroup)
        const firstAssignments = inferOcrBlockAssignments(firstPageBlocks, batchResponse.data.questions, { mode: 'question_paper' })
        const firstPageBlockIds = new Set(firstPageDetail ? getOcrBlocks(firstPageDetail).map((block) => block.id) : [])
        const firstBlockIds = firstQuestion ? (firstAssignments[firstQuestion.id] ?? []).filter((blockId) => firstPageBlockIds.has(blockId)) : []
        const firstAutoText = textForAssignedBlocks(firstPageBlocks, firstBlockIds)
        const initialStructure = analyzeQuestionStructure(firstPageBlocks, { subject: batchResponse.data.subject as QuestionStructureSubject })
        const firstCandidate = initialStructure.questions.length === batchResponse.data.questions.length ? initialStructure.questions[0] : null
        const firstPromptIncomplete = Boolean(firstCandidate?.requiresPromptReview)
        setPromptDraft(questionPromptDraftFromSavedOrOcr(firstQuestion?.question_prompt, firstAutoText, firstPromptIncomplete))
        setReferenceDraft(firstQuestion?.reference_answer?.trim() || (!firstPromptIncomplete && firstQuestion ? answerTextFromOcr(firstAutoText) : ''))
        setSelectedBlockIds(firstBlockIds)
        if (!layoutResponse.data && pageResponse.data.length && batchResponse.data.questions.length) {
          const detected = getOrderedOcrBlocks(pageDetails)
          const restoredStructure = detected.length
            ? addOcrTextToStructure(analyzeQuestionStructure(detected, { subject: batchResponse.data.subject as QuestionStructureSubject }), detected)
            : null
          if (!cancelled) {
            if (restoredStructure?.questions.length) {
              setStructureResult(restoredStructure)
              setLayoutDraft(null)
            } else {
              setLayoutDraft(buildQuestionLayoutDraft(pageDetails, batchResponse.data.questions))
            }
          }
        }
        setError('')
        if (!autoStartedOcr) setStatusMessage(pageResponse.data.length ? '已加载试卷页面；可选择页面并查看 OCR 文本块。' : fileResponse.data.length ? '试卷文件已上传，页面正在生成或等待 OCR；可在上方文件记录中维护。' : '该批次还没有试卷文件，请先上传题目文件。')
      } catch (cause) {
        if (!cancelled) showError(cause, '无法加载批次或试卷页面。请确认批次 ID 正确。')
      } finally {
        if (!cancelled) setState('idle')
      }
    }
    void loadBatchData()
    return () => { cancelled = true }
  }, [batchId])

  useEffect(() => {
    const currentQuestion = configuredQuestions.find((item) => item.id === selectedQuestionId)
    const currentBlocks = getOcrBlocks(selectedPage)
    const paperPages = selectedPage ? pages.filter((page) => page.source_file_id === selectedPage.source_file_id) : []
    const paperBlocks = paperPages.length ? getOrderedOcrBlocks(paperPages) : currentBlocks
    const assignments = inferOcrBlockAssignments(paperBlocks, configuredQuestions, { mode: 'question_paper' })
    const currentPageBlockIds = new Set(currentBlocks.map((block) => block.id))
    const currentBlockIds = currentQuestion ? (assignments[currentQuestion.id] ?? []).filter((blockId) => currentPageBlockIds.has(blockId)) : []
    const autoText = textForAssignedBlocks(currentBlocks, currentBlockIds)
    const questionIndex = currentQuestion ? configuredQuestions.findIndex((question) => question.id === currentQuestion.id) : -1
    const structure = analyzeQuestionStructure(paperBlocks, { subject: batch?.subject as QuestionStructureSubject | undefined })
    const alignedCandidate = structure.questions.length === configuredQuestions.length && questionIndex >= 0 ? structure.questions[questionIndex] : null
    const promptIncomplete = Boolean(alignedCandidate?.requiresPromptReview)
    setPromptDraft(questionPromptDraftFromSavedOrOcr(currentQuestion?.question_prompt, autoText, promptIncomplete))
    setReferenceDraft(currentQuestion?.reference_answer?.trim() || (!promptIncomplete ? answerTextFromOcr(autoText) : ''))
    setSelectedBlockIds(currentBlockIds)
  }, [selectedQuestionId, selectedPage, configuredQuestions, pages, batch?.subject])

  const refreshPaper = async () => {
    if (!batchId) return
    setState('loading')
    try {
      const [batchResponse, pageResponse, fileResponse, studentFileResponse, taskResponse, layoutResponse] = await Promise.all([
        api.batches.get(batchId),
        api.batches.pages(batchId, undefined, 'question_paper'),
        api.batches.files(batchId, undefined, 'question_paper'),
        api.batches.files(batchId, undefined, 'student_work'),
        api.batches.ocrTask(batchId, 'question_paper'),
        api.batches.questionLayout(batchId),
      ])
      setBatch(batchResponse.data)
      const pageDetails = await Promise.all(pageResponse.data.map((page) => api.pages.get(page.id).then((response) => response.data)))
      setPages(pageDetails)
      setQuestionPaperFiles(fileResponse.data)
      setHasStudentWorkFiles(studentFileResponse.data.length > 0)
      setQuestionPaperMatchesStudentWork(hasMatchingFileContent(fileResponse.data, studentFileResponse.data))
      setOcrTask(taskResponse.data)
      setQuestionLayout(layoutResponse.data)
      if (layoutResponse.data && questionLayoutIssues(layoutResponse.data.regions).length) {
        setLayoutDraft(questionLayoutDraftFromSaved(layoutResponse.data, batchResponse.data.questions))
      } else {
        setLayoutDraft(null)
      }
      const nextPage = selectedPage ? pageDetails.find((page) => page.id === selectedPage.id) ?? pageDetails[0] : pageDetails[0]
      const nextPageDetail = nextPage ?? null
      setSelectedPage(nextPageDetail)
      if (!layoutResponse.data && pageResponse.data.length && batchResponse.data.questions.length) {
        setLayoutDraft(buildQuestionLayoutDraft(pageDetails, batchResponse.data.questions))
      }
      setStatusMessage(pageResponse.data.length ? '试卷页面已刷新。OCR 文本块会按题号和版面顺序自动归属。' : '暂无试卷页面，请检查上传任务状态。')
      setError('')
    } catch (cause) {
      showError(cause, '刷新试卷页面失败，请稍后重试。')
    } finally {
      setState('idle')
    }
  }

  useEffect(() => {
    if (!batchId || !ocrTask || !ocrTaskActive) return
    let cancelled = false
    let requestInFlight = false
    const pollTask = async () => {
      if (cancelled || requestInFlight) return
      requestInFlight = true
      try {
        const response = await api.tasks.get(ocrTask.task_id)
        if (cancelled) return
        const nextTask = response.data
        setOcrTask(nextTask)
        if (nextTask.status === 'queued' || nextTask.status === 'running') {
          const progress = nextTask.progress_total > 0 ? ` ${nextTask.progress_current}/${nextTask.progress_total} 页` : ''
          setStatusMessage(`${ocrTaskStageLabel(nextTask)}${progress}。页面会自动更新。`)
          return
        }
        await refreshPaper()
        if (cancelled) return
        if (nextTask.status === 'succeeded') {
          setStatusMessage(`试卷 OCR 已完成：${nextTask.progress_current}/${nextTask.progress_total || nextTask.progress_current} 页成功。`)
        } else if (nextTask.status === 'partial_failed') {
          setError(nextTask.error_message || '部分试卷页面 OCR 失败，请查看页面状态并重试失败页。')
          setStatusMessage('试卷 OCR 部分完成，请处理失败页面。')
        } else {
          setError(nextTask.error_message || '试卷 OCR 失败，请检查 Worker 和试卷文件。')
          setStatusMessage('试卷 OCR 未完成，请根据错误提示处理后重试。')
        }
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiClientError ? cause.message : '读取 OCR 进度失败，页面会继续重试。')
      } finally {
        requestInFlight = false
      }
    }
    void pollTask()
    const timer = window.setInterval(() => void pollTask(), 1200)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [batchId, ocrTask?.task_id, ocrTask?.status])

  const handleUpload = async () => {
    if (!batchId || !selectedFile) return
    setState('uploading')
    setError('')
    try {
      await api.batches.uploadQuestionPaper(batchId, selectedFile, createIdempotencyKey())
      clearSelectedFile()
      setStatusMessage('试卷已上传，正在自动提交题目 OCR。')
      await refreshPaper()
      setState('starting-ocr')
      const response = await api.batches.startOcr(batchId, { recognition_mode: recognitionMode }, createIdempotencyKey(), 'question_paper')
      setOcrTask(response.data.task)
      setStatusMessage(response.data.reused ? '试卷已上传，已复用现有题目 OCR 任务并读取进度。' : '试卷已上传，题目 OCR 已自动入队；完成后会自动生成题目区域。')
      notify('试卷已上传，题目 OCR 已自动开始')
    } catch (cause) {
      if (cause instanceof ApiClientError && cause.code === 'OCR_TASK_ALREADY_RUNNING') {
        await refreshPaper()
        setStatusMessage('试卷已上传；当前批次已有题目 OCR 任务运行中，页面将继续读取该任务进度。')
        notify('试卷已上传，正在复用已有题目 OCR 任务')
      } else {
        showError(cause, '试卷上传或题目 OCR 启动失败，请检查文件格式、批次状态、登录状态和 OCR Worker。')
      }
    } finally {
      setState('idle')
    }
  }

  const handleStartOcr = async () => {
    if (!batchId) return
    setState('starting-ocr')
    setError('')
    try {
      const response = await api.batches.startOcr(batchId, { recognition_mode: recognitionMode }, createIdempotencyKey(), 'question_paper')
      setOcrTask(response.data.task)
      setStatusMessage(response.data.reused ? '已复用现有试卷 OCR 任务，正在读取实时进度。' : '试卷 OCR 已入队，正在读取实时进度。')
      notify('试卷 OCR 任务已提交')
    } catch (cause) {
      showError(cause, '试卷 OCR 提交失败，请先确认试卷文件已上传。')
    } finally {
      setState('idle')
    }
  }

  const handleStructureRecognition = async () => {
    if (!batchId || !selectedFile) return
    if (!['image/jpeg', 'image/png'].includes(selectedFile.type) && !/\.(jpe?g|png)$/i.test(selectedFile.name)) {
      setError('一键题型识别目前只接受 JPG、JPEG 或 PNG 图片；PDF 请先使用普通试卷 OCR。')
      setStatusMessage('未提交题型识别，请选择图片文件后重试。')
      return
    }
    setState('analyzing-structure')
    setStructureResult(null)
    setError('')
    try {
      const uploadResponse = await api.batches.uploadQuestionPaper(batchId, selectedFile, createIdempotencyKey())
      const uploadedFileId = uploadResponse.data.file_id
      const [questionPaperFileResponse, studentFileResponse] = await Promise.all([
        api.batches.files(batchId, undefined, 'question_paper'),
        api.batches.files(batchId, undefined, 'student_work'),
      ])
      setQuestionPaperFiles(questionPaperFileResponse.data)
      setHasStudentWorkFiles(studentFileResponse.data.length > 0)
      setQuestionPaperMatchesStudentWork(hasMatchingFileContent(questionPaperFileResponse.data, studentFileResponse.data))
      setStatusMessage('题目与参考答案图片已上传，正在进入 OCR 队列；完成后系统会按题号和版面顺序自动归属文本，教师只需核对补漏。')
      const ocrResponse = await api.batches.startOcr(batchId, { recognition_mode: recognitionMode }, createIdempotencyKey(), 'question_paper')
      let task = ocrResponse.data.task
      for (let attempt = 0; attempt < 120; attempt += 1) {
        if (task.status === 'succeeded' || task.status === 'partial_failed' || task.status === 'failed') break
        setStatusMessage(`图片 OCR 处理中…${task.progress_total ? ` ${task.progress_current}/${task.progress_total}` : ''}`)
        await new Promise((resolve) => window.setTimeout(resolve, 1200))
        task = (await api.tasks.get(task.task_id)).data
      }
      if (task.status === 'failed') throw new Error(task.error_message || '图片 OCR 失败，请检查 Worker、图片清晰度和识别模式。')
      if (!['succeeded', 'partial_failed'].includes(task.status)) throw new Error('图片 OCR 等待超时，请刷新页面查看任务状态。')

      const pageResponse = await api.batches.pages(batchId, undefined, 'question_paper')
      const pagesForFile = pageResponse.data.filter((page) => page.source_file_id === uploadedFileId)
      const targetPages = pagesForFile.length ? pagesForFile : pageResponse.data
      const allPageDetails = await Promise.all(pageResponse.data.map((page) => api.pages.get(page.id).then((response) => response.data)))
      const targetPageIds = new Set(targetPages.map((page) => page.id))
      const pageDetails = allPageDetails.filter((page) => targetPageIds.has(page.id))
      const blocksForAnalysis = getOrderedOcrBlocks(pageDetails)
      if (!blocksForAnalysis.length) {
        if (batch?.questions.length === 1) {
          setPages(allPageDetails)
          setSelectedPage(pageDetails[0] ?? null)
          setLayoutDraft(buildQuestionLayoutDraft(pageDetails, batch.questions))
          clearSelectedFile()
          setStatusMessage('OCR 未返回可用文本块；已按当前单题自动生成整页候选区域。请在下方核对或录入题干后确认模板。')
          notify('已生成单题整页候选区域，请核对题干')
          return
        }
        throw new Error('OCR 已完成但没有可分析的文本块，请确认图片清晰、题号可见，或先在批次中录入题目文本。')
      }
      const result = addOcrTextToStructure(
        analyzeQuestionStructure(blocksForAnalysis, { fallbackQuestionNo: batch?.questions.length === 1 ? batch.questions[0].question_no : undefined, subject: batch?.subject as QuestionStructureSubject | undefined }),
        blocksForAnalysis,
      )
      setStructureResult(result)
      setPages(allPageDetails)
      setSelectedPage(pageDetails[0] ?? null)
      clearSelectedFile()
      setStatusMessage(task.status === 'partial_failed' ? '题目结构已分析，但同批次存在其他页面 OCR 失败；下方结果只对应本次图片。' : `题目结构识别完成：共 ${result.questionCount} 道题。`)
      notify('题目数量与题型识别完成')
    } catch (cause) {
      showError(cause, '题目结构识别失败，请确认图片、批次和 OCR Worker 状态。')
    } finally {
      setState('idle')
    }
  }

  const updateStructureQuestion = (index: number, patch: Partial<QuestionStructureResult['questions'][number]>) => {
    setStructureResult((current) => {
      if (!current) return current
      const previousQuestion = current.questions[index]
      const questions = current.questions.map((question, questionIndex) => questionIndex === index ? { ...question, ...patch } : question)
      let nextResult = current
      if (patch.questionNo !== undefined && previousQuestion && patch.questionNo !== previousQuestion.questionNo && current.ocrTextByQuestionNo?.[previousQuestion.questionNo] && !current.ocrTextByQuestionNo[patch.questionNo]) {
        const ocrTextByQuestionNo = { ...current.ocrTextByQuestionNo, [patch.questionNo]: current.ocrTextByQuestionNo[previousQuestion.questionNo] }
        delete ocrTextByQuestionNo[previousQuestion.questionNo]
        nextResult = { ...current, ocrTextByQuestionNo }
      }
      return summarizeQuestionStructure(nextResult, questions)
    })
  }

  const addStructureQuestion = () => {
    setStructureResult((current) => {
      if (!current) return current
      const questionNo = nextQuestionNo(current.questions)
      const questions = [...current.questions, {
        questionNo,
        questionType: 'needs_review' as const,
        questionSubtype: 'unknown' as const,
        confidence: 0,
        evidence: '教师手动增加，等待题干 OCR 文本归属',
      }]
      return summarizeQuestionStructure(current, questions)
    })
  }

  const removeStructureQuestion = (index: number) => {
    setStructureResult((current) => {
      if (!current) return current
      return summarizeQuestionStructure(current, current.questions.filter((_question, questionIndex) => questionIndex !== index))
    })
  }

  const confirmQuestionStructure = async () => {
    if (!batchId || !batch || !structureResult) return
    if (ocrTaskActive) {
      setError('当前题目 OCR 仍在运行，请等待任务完成后再确认题目结构。')
      return
    }
    const normalizedQuestions = structureResult.questions.map((question, index) => ({ ...question, questionNo: question.questionNo.trim() || String(index + 1) }))
    const questionNos = normalizedQuestions.map((question) => question.questionNo)
    if (!normalizedQuestions.length) {
      setError('至少保留一道题目后才能确认。')
      return
    }
    if (new Set(questionNos).size !== questionNos.length || questionNos.some((questionNo) => !/^\d+$/.test(questionNo) || Number(questionNo) <= 0)) {
      setError('题号必须是大于 0 的数字且不能重复，请修正后再确认。')
      return
    }

    if (hasStudentWorkFiles && normalizedQuestions.length !== batch.questions.length) {
      setError(`当前批次已有学生作业，必须保留现有 ${batch.questions.length} 道题的结构。请补齐或删除候选题，使题数一致后再按顺序回填；如确需增删题目，请新建批次。`)
      return
    }
    if (hasStudentWorkFiles && normalizedQuestions.some((question, index) => !isQuestionStructureNeedsReview(question) && question.questionType !== batch.questions[index]?.question_type)) {
      setError('当前批次已有学生作业，为保护已关联答案，候选题型顺序必须与现有题目一致。请核对题型后再确认；如确需更改题型，请新建批次。')
      return
    }

    setState('confirming-structure')
    setError('')
    setLayoutDraft(null)
    try {
      const normalizedResult = { ...structureResult, questions: normalizedQuestions }
      const drafts = makeQuestionDraftsFromStructure(normalizedResult, batch.questions, batch.total_score, hasStudentWorkFiles)
      const replaceResponse = await api.batches.replaceQuestions(batchId, {
        questions: drafts,
        version: batch.version,
        ...(hasStudentWorkFiles ? { preserve_existing_structure: true } : {}),
      })
      setBatch(replaceResponse.data)
      setSelectedQuestionId(replaceResponse.data.questions[0]?.id ?? '')
      setStructureResult(summarizeQuestionStructure({ ...structureResult, questions: normalizedQuestions }, normalizedQuestions))
      await refreshPaper()
      const incompletePromptIndexes = normalizedQuestions.flatMap((question, index) => question.requiresPromptReview ? [index] : [])
      setStatusMessage(hasStudentWorkFiles
        ? incompletePromptIndexes.length
          ? `已按顺序回填 ${normalizedQuestions.length - incompletePromptIndexes.length} 道题的 OCR 文本；第 ${incompletePromptIndexes.map((index) => batch.questions[index]?.question_no ?? index + 1).join('、')} 题 OCR 题干不完整，已保留原题干，请对照原卷补全。`
          : `已按试卷题目顺序将 ${normalizedQuestions.length} 道题的 OCR 文本回填到现有批次；题号、题型、分值和评分标准保持不变。`
        : `已确认 ${replaceResponse.data.questions.length} 道题，已使用现有 OCR 文本块按题号自动归属题干。`)
      notify(hasStudentWorkFiles
        ? incompletePromptIndexes.length ? '已回填可识别题干；OCR 不完整的题目已保留原题干' : 'OCR 文本已按顺序回填，现有学生作业关联已保留'
        : `已确认 ${replaceResponse.data.questions.length} 道题，题目配置已保存`)
    } catch (cause) {
      showError(cause, '题目结构确认失败，请检查批次版本、页面状态和题目配置。')
    } finally {
      setState('idle')
    }
  }

  const deleteQuestionPaperFile = async (file: SourceFile) => {
    if (questionPaperDeletingFileId) return
    setQuestionPaperDeletingFileId(file.file_id)
    setQuestionPaperDeleteCandidate(null)
    setError('')
    try {
      await api.files.purge(file.file_id, file.original_name)
      setQuestionPaperFiles((current) => current.filter((item) => item.file_id !== file.file_id))
      clearSelectedFile()
      setStructureResult(null)
      setStatusMessage('误上传的试卷文件已删除；如需继续，请重新选择正确的题目文件。')
      await refreshPaper()
      notify(`${file.original_name} 已删除`)
    } catch (cause) {
      showError(cause, '试卷文件删除失败，请刷新后重试。')
    } finally {
      setQuestionPaperDeletingFileId('')
    }
  }

  const clearSelectedFile = () => {
    setSelectedFile(null)
    const input = document.getElementById('question-paper-file') as HTMLInputElement | null
    if (input) input.value = ''
  }

  const handlePageSelect = async (page: SourcePage) => {
    setSelectedPage(page)
    setState('loading')
    try {
      const response = await api.pages.get(page.id)
      setSelectedPage(response.data)
      setError('')
      setStatusMessage('已加载当前页面的最新 OCR 文本块。')
    } catch (cause) {
      showError(cause, '无法加载页面详情，请刷新后重试。')
    } finally {
      setState('idle')
    }
  }

  const savePrompt = async () => {
    if (!batchId || !selectedQuestion || (!promptDraft.trim() && !referenceDraft.trim())) return
    setState('saving')
    setError('')
    try {
      const canBindOcrBlocks = selectedPage?.status === 'ocr_ready' && selectedBlockIds.length > 0
      const response = await api.batches.updateQuestionPrompt(batchId, selectedQuestion.id, {
        question_prompt: promptDraft.trim() || undefined,
        reference_answer: referenceDraft.trim() || null,
        ...(canBindOcrBlocks ? { source_page_id: selectedPage.id, ocr_block_ids: selectedBlockIds } : {}),
        version: selectedQuestion.version,
      })
      setBatch(response.data)
      const savedQuestion = response.data.questions.find((item) => item.id === selectedQuestion.id)
      setStatusMessage(`第 ${selectedQuestion.question_no} 题题目文本与批次共享参考答案已保存为新版本 v${savedQuestion?.version ?? selectedQuestion.version + 1}。`)
      notify(canBindOcrBlocks ? '题目文本与批次共享参考答案已保存并绑定 OCR' : '题目文本与批次共享参考答案已保存')
    } catch (cause) {
      showError(cause, '题目或批次共享参考答案保存失败，可能是版本已变化或 OCR 文本块已被其他题目占用。请刷新后重试。')
    } finally {
      setState('idle')
    }
  }

  const confirmQuestionLayout = async () => {
    if (!batchId || !layoutDraft || layoutDraft.missing_question_nos.length || !layoutDraft.regions.length) {
      setError(layoutDraft?.warnings.join(' ') || '题目位置模板尚未形成，请先完成题目页 OCR。')
      return
    }
    const issues = questionLayoutIssues(layoutDraft.regions)
    if (issues.length) {
      setError(issues.map((issue) => issue.message).join(' '))
      setStatusMessage('题目区域存在重叠或坐标错误，系统不会保存可能串题的模板。')
      return
    }
    setLayoutSaving(true)
    setError('')
    try {
      const response = await api.batches.saveQuestionLayout(batchId, {
        source_page_id: layoutDraft.source_page_id,
        source_pages: layoutDraft.source_pages,
        regions: layoutDraft.regions,
        version: questionLayout?.version ?? 0,
      })
      setQuestionLayout(response.data)
      setLayoutDraft(null)
      setStatusMessage(`题目版式模板已确认：${response.data.regions.length} 个题目区域。现在学生作业会先按模板定位，再逐题进行手写 OCR。`)
      notify('题目数量和位置模板已确认')
    } catch (cause) {
      showError(cause, '题目位置模板保存失败，请刷新后重试。')
    } finally {
      setLayoutSaving(false)
    }
  }

  const disabled = state !== 'idle'
  return <><section className="panel question-paper-workflow" aria-labelledby="question-paper-title">
    <div className="panel-heading"><div><div className="eyebrow">正式接口 · question_paper · {currentSubjectLabel}</div><h2 id="question-paper-title">题目与批次共享参考答案准备</h2><p>上传一次题目与参考答案图片后，系统按题号和版面顺序自动归属 OCR 文本；语文、数学、英语均支持，教师只需核对并补录，保存后所有学生共用。</p></div><StatusPill tone={ocrTaskActive ? 'info' : ocrTask?.status === 'failed' ? 'danger' : ocrTask?.status === 'partial_failed' ? 'warning' : hasOcr ? 'success' : pages.length ? 'warning' : 'neutral'}>{ocrTaskActive ? `OCR ${ocrTaskStatusLabel(ocrTask?.status ?? '')}` : ocrTask ? `OCR ${ocrTaskStatusLabel(ocrTask.status)}` : hasOcr ? 'OCR 可用' : pages.length ? '等待 OCR' : '未上传试卷'}</StatusPill></div>
    <div className="question-paper-toolbar">
      <label className="field"><span>正式批次 ID <em>*</em></span><input value={batchIdInput} onChange={(event) => setBatchIdInput(event.target.value)} onBlur={() => setBatchId(batchIdInput.trim())} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); setBatchId(batchIdInput.trim()) } }} placeholder="输入后端批次 ID，例如 batch UUID" aria-describedby="question-paper-batch-help" /></label>
      <label className="field"><span>选择已有批次</span><select value={batches.some((item) => item.id === batchId) ? batchId : ''} onChange={(event) => { setBatchId(event.target.value); setBatchIdInput(event.target.value) }} disabled={loadingBatches}><option value="">{loadingBatches ? '正在加载批次…' : '从批次列表选择'}</option>{batches.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.subject}</option>)}</select></label>
      <div className="question-paper-actions"><SecondaryButton icon={ArrowsClockwise} onClick={loadBatches} disabled={disabled || loadingBatches}>刷新批次</SecondaryButton><SecondaryButton icon={ArrowsClockwise} onClick={refreshPaper} disabled={disabled || !batchId}>刷新试卷</SecondaryButton></div>
    </div>
    <p className="workflow-helper" id="question-paper-batch-help">这里的上传、OCR 和题干保存均使用真实后端接口；后端未连接或任务失败时会明确显示原因，不会伪造成功结果。</p>
    {error && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{error}</span><button className="text-button" type="button" onClick={refreshPaper} disabled={!batchId}>重试</button></div>}
    <div className="workflow-message" role="status" aria-live="polite"><Info size={17} aria-hidden="true" /><span>{statusMessage}</span></div>
    {ocrTask && <div className={`ocr-task-progress ${ocrTask.status === 'failed' ? 'failed' : ocrTask.status === 'partial_failed' ? 'partial' : ocrTask.status === 'succeeded' ? 'complete' : ''}`} role="status" aria-live="polite"><div className="ocr-task-progress-head"><div><strong>试卷 OCR 进度</strong><span>{ocrTaskStageLabel(ocrTask)}</span></div><StatusPill tone={ocrTask.status === 'succeeded' ? 'success' : ocrTask.status === 'failed' ? 'danger' : ocrTask.status === 'partial_failed' ? 'warning' : 'info'}>{ocrTaskStatusLabel(ocrTask.status)}</StatusPill></div><div className="ocr-task-progress-track" aria-label={`OCR 进度 ${ocrProgress}%`}><span style={{ width: `${ocrProgress}%` }} /></div><div className="ocr-task-progress-meta"><span>{ocrTask.progress_current} / {ocrTask.progress_total || '—'} 页</span><strong>{ocrTask.progress_total ? `${ocrProgress}%` : '等待统计'}</strong></div>{ocrTask.error_message && <small className="ocr-task-error">{ocrTask.error_message}</small>}</div>}
    <div className="question-paper-grid">
      <div className="question-paper-upload">
        <div className="section-label">2 · 上传试卷</div>
        <label className="file-dropzone" htmlFor="question-paper-file"><CloudArrowUp size={25} weight="duotone" aria-hidden="true" /><strong>{selectedFile ? selectedFile.name : '选择题目文件'}</strong><span>支持 JPG、PNG、PDF；文件会保存为 question_paper</span><input id="question-paper-file" type="file" accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf" onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)} /></label>
        <div className="question-paper-button-row"><PrimaryButton icon={UploadSimple} onClick={handleUpload} disabled={disabled || !batchId || !selectedFile}>{state === 'uploading' ? '上传中…' : '上传试卷'}</PrimaryButton><SecondaryButton icon={Scan} onClick={() => void handleStructureRecognition()} disabled={disabled || !batchId || !selectedFile}>{state === 'analyzing-structure' ? '识别中…' : '一键识别题型'}</SecondaryButton><span>{selectedFile ? `${Math.ceil(selectedFile.size / 1024)} KB` : '尚未选择文件'}</span></div>
        {selectedFile && <button className="text-button question-paper-clear-file" type="button" onClick={clearSelectedFile} disabled={disabled}>清除当前待上传文件</button>}
        <div className="question-paper-file-history" aria-live="polite"><div className="question-paper-file-history-head"><div><strong>已上传试卷</strong><span>{questionPaperFiles.length ? `${questionPaperFiles.length} 个文件` : '当前批次暂无已上传试卷'}</span></div><button className="text-button" type="button" onClick={() => void refreshPaper()} disabled={disabled || !batchId}>刷新</button></div>{questionPaperFiles.length > 0 && <div className="upload-file-list" aria-label="已上传试卷列表">{questionPaperFiles.map((file) => <div className="upload-file-row" key={file.file_id}><FileText size={18} weight="duotone" aria-hidden="true" /><div><strong>{file.original_name}</strong><span>{formatFileSize(file.size_bytes)} · {file.page_count ?? '—'} 页 · {file.status === 'success' ? '已保存' : file.failure_code || file.status}</span></div>{file.status !== 'pending' && file.status !== 'validating' ? <button className="text-button file-delete-button" type="button" onClick={() => { setError(''); setQuestionPaperDeleteCandidate(file) }} disabled={Boolean(questionPaperDeletingFileId)}>{questionPaperDeletingFileId === file.file_id ? '删除中…' : '删除文件'}</button> : <span className="question-paper-file-processing">保存处理中，完成后可删除</span>}<StatusPill tone={file.status === 'success' ? 'success' : file.status === 'failed' ? 'danger' : 'info'}>{file.status === 'success' ? '已保存' : file.status === 'failed' ? '失败' : '处理中'}</StatusPill></div>)}</div>}</div>
      </div>
      <div className="question-paper-upload">
        <div className="section-label">3 · OCR 识别设置</div>
        <label className="field"><span>识别模式 · {currentSubjectLabel}</span><select value={recognitionMode} onChange={(event) => setRecognitionMode(event.target.value as typeof recognitionMode)}><option value="printed">{recognitionModeLabels.printed}</option><option value="chinese_handwriting">{recognitionModeLabels.chinese_handwriting}</option><option value="english_handwriting">{recognitionModeLabels.english_handwriting}</option><option value="math_handwriting">{recognitionModeLabels.math_handwriting}</option></select><small>题目页默认同时识别中文和英文；数学题面会同时保留中文文本并识别公式。</small></label>
        <div className="question-paper-button-row"><PrimaryButton icon={Scan} onClick={handleStartOcr} disabled={disabled || !batchId || pages.length === 0}>{state === 'starting-ocr' ? '提交中…' : '开始试卷 OCR'}</PrimaryButton><span>{recognitionModeLabels[recognitionMode]}</span></div>
      </div>
      <div className="question-paper-help">
        <div className="section-label">识别说明</div>
        <ul>
          <li><CheckCircle size={17} weight="fill" aria-hidden="true" /><span>请确保上传的试卷图片清晰、完整。</span></li>
          <li><CheckCircle size={17} weight="fill" aria-hidden="true" /><span>系统将按 OCR HANDOFF 完成预处理、区域提取和模型识别。</span></li>
          <li><CheckCircle size={17} weight="fill" aria-hidden="true" /><span>识别完成后系统自动归属文本，教师只需核对题目文本和批次共享参考答案。</span></li>
        </ul>
      </div>
    </div>
    {structureResult && <section className="question-structure-panel" aria-labelledby="question-structure-title"><div className="question-structure-head"><div><div className="section-label">4 · 题目结构识别结果</div><h3 id="question-structure-title">{structureResult.questionCount ? `共识别 ${structureResult.questionCount} 道题目` : '未识别到明确题号'}</h3><p>{hasStudentWorkFiles ? '此批次已有学生作业。确认时会按顺序回填题干，并保留现有题号、题型、分值和评分标准；题数与题型顺序需和当前配置一致。' : '识别结果先作为候选草稿，老师可以调整题号、题型、数量后再确认。'}</p></div><StatusPill tone={structureResult.status === 'confirmed' ? 'success' : 'warning'}>{structureResult.status === 'confirmed' ? '可确认' : '需要教师确认'}</StatusPill></div>{questionPaperMatchesStudentWork && <div className="demo-notice soft" role="note"><Info size={16} aria-hidden="true" /><span>检测到题目试卷与学生作业文件内容一致，OCR 可能同时识别出手写作答。请优先使用空白试卷；继续使用时，请核对题干预览并删除混入的答案。</span></div>}<div className="question-structure-summary"><div><strong>{structureResult.objectiveCount}</strong><span>客观题</span></div><div><strong>{structureResult.subjectiveCount}</strong><span>主观题</span></div><div><strong>{structureResult.needsReviewCount}</strong><span>待确认</span></div><div><strong>{Math.round(structureResult.confidence * 100)}%</strong><span>平均置信度</span></div></div><div className="question-structure-list editable">{structureResult.questions.map((question, index) => <div key={`${question.questionNo}-${index}`} className="question-structure-row editable"><label className="field compact"><span>题号</span><input value={question.questionNo} onChange={(event) => updateStructureQuestion(index, { questionNo: event.target.value })} inputMode="numeric" aria-label={`第 ${index + 1} 个候选题号`} disabled={disabled || ocrTaskActive} /></label><label className="field compact"><span>题型</span><select value={question.questionType === 'objective' ? 'objective' : 'subjective'} onChange={(event) => updateStructureQuestion(index, { questionType: event.target.value as 'objective' | 'subjective', questionSubtype: event.target.value === 'objective' ? 'single_choice' : 'short_answer' })} aria-label={`第 ${question.questionNo || index + 1} 题题型`} disabled={disabled || ocrTaskActive}><option value="objective">客观题</option><option value="subjective">主观题</option></select></label><div className="question-structure-evidence"><div><StatusPill tone={question.questionType === 'objective' ? 'info' : question.questionType === 'subjective' ? 'success' : 'warning'}>{questionSubtypeLabels[question.questionSubtype] || (question.questionType === 'objective' ? '客观题' : '主观题')}</StatusPill><span>{question.evidence} · 置信度 {Math.round(question.confidence * 100)}%</span></div>{structureResult.ocrTextByQuestionNo?.[question.questionNo] && <small>题干预览：{structureResult.ocrTextByQuestionNo[question.questionNo]}</small>}</div><button className="icon-button question-structure-remove" type="button" onClick={() => removeStructureQuestion(index)} aria-label={`删除第 ${question.questionNo || index + 1} 题`} title="删除题目" disabled={disabled || ocrTaskActive || structureResult.questions.length <= 1}><TrashSimple size={17} aria-hidden="true" /></button></div>)}</div><div className="question-structure-actions"><SecondaryButton icon={Plus} onClick={addStructureQuestion} disabled={disabled || ocrTaskActive}>增加题目</SecondaryButton><span>确认后会更新图二的题目下拉选项，并使用当前 OCR 文本块自动归属题干。</span><PrimaryButton icon={Check} onClick={() => void confirmQuestionStructure()} disabled={disabled || ocrTaskActive || !structureResult.questions.length}>{state === 'confirming-structure' ? '确认中…' : `确认 ${structureResult.questions.length} 道题`}</PrimaryButton></div><ul className="question-structure-notes">{structureResult.notes.map((note) => <li key={note}>{note}</li>)}</ul></section>}
    {questionLayout && !layoutDraft && <section className="question-layout-confirmation saved" aria-label="已保存的题目位置模板"><div className="question-layout-saved-head"><div><div className="section-label">5 · 题目位置模板</div><strong>{questionLayout.regions.length} 个区域已保存 · v{questionLayout.version}</strong><span>请确认蓝框只覆盖该题学生填写的答案位置；同一题可添加多个分散区域。</span></div><SecondaryButton onClick={() => setLayoutDraft(questionLayoutDraftFromSaved(questionLayout, configuredQuestions))}>校准答题区域</SecondaryButton></div></section>}
    {layoutDraft && <section className="question-layout-confirmation" aria-labelledby="question-layout-title"><div className="question-structure-head"><div><div className="section-label">5 · 题目位置模板</div><h3 id="question-layout-title">校准学生答题区域</h3><p>系统的矩形建议可能包含题干、选项或邻题内容。请在原图上拖拽，只框住学生填写答案的位置；跨页或分散作答可为同一题添加多个区域。</p></div><StatusPill tone={layoutDraftIssues.length || layoutDraft.missing_question_nos.length ? 'warning' : 'info'}>{layoutDraftIssues.length ? '区域需修正' : layoutDraft.missing_question_nos.length ? '需要补齐' : '待确认'}</StatusPill></div><div className="question-layout-summary"><span>{layoutDraft.source_pages.length} 个题目页</span><span>{layoutDraft.covered_question_ids.length}/{configuredQuestions.length || layoutDraft.covered_question_ids.length} 道题已覆盖</span><span>学生作业 OCR 前置模板</span></div>{layoutDraftIssues.map((issue) => <p className="workflow-inline-error" role="alert" key={`${issue.code}-${issue.region_indexes.join('-')}`}>{issue.message}</p>)}<QuestionLayoutEditor questions={configuredQuestions} sourcePages={layoutDraft.source_pages} imageUrls={layoutImageUrls} regions={layoutDraft.regions} onChange={(regions) => { const covered = [...new Set(regions.map((region) => region.question_id))]; const missing = configuredQuestions.filter((question) => !covered.includes(question.id)); setLayoutDraft((current) => current ? { ...current, regions, covered_question_ids: covered, missing_question_nos: missing.map((question) => question.question_no) } : current) }} /><div className="question-paper-save-row"><span>保存后，学生页会按这些答题区域分别识别；客观题只回填唯一选项字母。</span><PrimaryButton icon={Check} onClick={() => void confirmQuestionLayout()} disabled={layoutSaving || Boolean(layoutDraft.missing_question_nos.length) || Boolean(layoutDraftIssues.length)}>{layoutSaving ? '保存中…' : questionLayout ? '保存校准后的模板' : '确认题目位置模板'}</PrimaryButton></div></section>}
    <div className="question-paper-review">
      <div className="section-label">4 · OCR 文本校对与题目归类</div>
      <div className="question-paper-review-grid">
        <div className="paper-pages-column"><label className="field"><span>试卷页面</span><select value={selectedPage?.id ?? ''} onChange={(event) => { const page = pages.find((item) => item.id === event.target.value); if (page) void handlePageSelect(page) }} disabled={!pages.length || disabled}><option value="">{pages.length ? '选择页面' : '暂无试卷页面'}</option>{pages.map((page) => <option key={page.id} value={page.id}>第 {page.page_index} 页 · {page.status}</option>)}</select></label><div className="paper-page-list" aria-label="试卷页面列表">{pages.map((page) => <button key={page.id} className={`paper-page-item ${selectedPage?.id === page.id ? 'active' : ''}`} type="button" onClick={() => void handlePageSelect(page)} disabled={disabled}><span>第 {page.page_index} 页</span><StatusPill tone={page.latest_ocr_run?.status === 'succeeded' ? 'success' : page.latest_ocr_run?.status === 'failed' ? 'danger' : 'warning'}>{page.latest_ocr_run?.status ?? page.status}</StatusPill></button>)}</div>{selectedPage && <div className="question-paper-page-preview"><div className="question-paper-page-preview-head"><strong>当前试卷原图</strong><span>点击图片放大</span></div>{selectedPage.original_name.toLowerCase().endsWith('.pdf') ? <iframe title="试卷 PDF 原图" src={api.pages.originalUrl(selectedPage.id)} /> : <ZoomableImage src={api.pages.originalUrl(selectedPage.id)} alt={`${selectedPage.original_name} 第 ${selectedPage.page_index} 页原图`} onOpen={() => setPreviewImage({ src: api.pages.originalUrl(selectedPage.id), alt: `${selectedPage.original_name} 第 ${selectedPage.page_index} 页原图`, title: `题目与参考答案原图 · 第 ${selectedPage.page_index} 页` })} />}</div>}</div>
        <div className="ocr-blocks-column"><div className="paper-review-head"><div><strong>系统自动归属的 OCR 文本块</strong><span>{blocks.length ? `${blocks.length} 个文本块 · 当前题目自动归属 ${selectedBlockIds.length} 个` : '页面 OCR 完成后自动归属文本块'}</span></div><StatusPill tone={selectedBlockIds.length ? 'info' : 'warning'}>{selectedBlockIds.length ? '自动归属' : '待补录'}</StatusPill></div><div className="ocr-block-list">{blocks.length ? sortOcrBlocksReadingOrder(blocks.filter((block) => selectedBlockIds.includes(block.id))).map((block, readingIndex) => <div key={block.id} className="ocr-block-option selected system-assigned"><span><strong>阅读顺序 {readingIndex + 1} · 原文本块 {block.block_index}</strong><small>{block.text_raw || '（空文本块）'}</small></span><em>{block.confidence ? `${Math.round(Number(block.confidence) * 100)}%` : '—'}</em></div>) : <div className="empty-inline"><Scan size={24} aria-hidden="true" /><span>{selectedPage ? '当前页面还没有可用 OCR 文本块。请确认 OCR 任务已完成并刷新试卷。' : '请选择试卷页面。'}</span></div>}{blocks.length > 0 && !selectedBlockIds.length && <div className="empty-inline"><Info size={22} aria-hidden="true" /><span>系统暂未找到属于当前题目的候选块，请在下方题目文本或参考答案中补录。</span></div>}</div></div>
      </div>
      <div className="question-prompt-editor"><label className="field"><span>题目与参考答案（已确认 {configuredQuestions.length} 道题）</span><select value={selectedQuestionId} onChange={(event) => setSelectedQuestionId(event.target.value)} disabled={!configuredQuestions.length || disabled}><option value="">{configuredQuestions.length ? '选择题目' : '当前批次暂无题目'}</option>{configuredQuestions.map((item) => <option key={item.id} value={item.id}>第 {item.question_no} 题 · {item.question_type === 'objective' ? '客观题' : '主观题'}</option>)}</select></label><label className="field"><span>题目文本</span><textarea value={promptDraft} onChange={(event) => setPromptDraft(event.target.value)} placeholder={selectedPage ? '系统自动归属 OCR 文本后，在这里核对并补录题目文本' : '没有题目图片时，可直接在这里录入题目文本'} disabled={!selectedQuestion || disabled} aria-describedby="question-prompt-helper" /><small id="question-prompt-helper">{selectedPage ? `已按当前题目自动归属 ${selectedBlockIds.length} 个 OCR 文本块，并按阅读顺序拼接；教师只需查缺补漏。原始 OCR 不会被覆盖。${questionPaperMatchesStudentWork ? '题目试卷与学生作业文件内容相同，OCR 可能混入手写作答，请核对后再保存。' : ''}` : '当前批次没有试卷 OCR 页面；这里的文本会直接作为题目内容保存。'}</small></label><label className="field"><span>参考答案（批次共享）</span><textarea value={referenceDraft} onChange={(event) => setReferenceDraft(event.target.value)} placeholder="核对或补录参考答案；本批次所有学生共用这一份答案" disabled={!selectedQuestion || disabled} /><small>参考答案只在题目端上传/识别一次，保存后复用于本批次全部学生，不在学生端重复上传。</small></label><div className="question-paper-save-row"><span>{selectedQuestion ? `当前题目版本 v${selectedQuestion.version} · 批次共享答案` : '请选择题目后编辑题干和参考答案'}</span><PrimaryButton icon={Check} onClick={savePrompt} disabled={disabled || !selectedQuestion || (!promptDraft.trim() && !referenceDraft.trim())}>{state === 'saving' ? '保存中…' : '保存题目与答案'}</PrimaryButton></div></div>
    </div>
  </section>{previewImage && <ImageLightbox target={previewImage} onClose={() => setPreviewImage(null)} />}{questionPaperDeleteCandidate && <QuestionPaperDeleteModal file={questionPaperDeleteCandidate} submitting={Boolean(questionPaperDeletingFileId)} error={error} onClose={() => setQuestionPaperDeleteCandidate(null)} onConfirm={() => void deleteQuestionPaperFile(questionPaperDeleteCandidate)} />}</>
}

function createIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `web-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function isQuestionPaperImage(file: File) {
  return ['image/jpeg', 'image/png'].includes(file.type) || /\.(jpe?g|png)$/i.test(file.name)
}

function summarizeQuestionStructure(result: QuestionStructureResult, questions: QuestionStructureResult['questions']): QuestionStructureResult {
  const objectiveCount = questions.filter((item) => item.questionType === 'objective').length
  const subjectiveCount = questions.filter((item) => item.questionType === 'subjective').length
  const needsReviewCount = questions.filter(isQuestionStructureNeedsReview).length
  const hasIncompleteInferredQuestion = questions.some((question) => question.requiresPromptReview)
  const confidence = questions.length ? questions.reduce((sum, item) => sum + item.confidence, 0) / questions.length : 0
  return {
    ...result,
    questionCount: questions.length,
    objectiveCount,
    subjectiveCount,
    needsReviewCount,
    confidence,
    status: needsReviewCount || !questions.length || hasIncompleteInferredQuestion ? 'needs_review' : 'confirmed',
    questions,
    notes: questions.length
      ? [`教师可继续调整题号、题型和数量；确认后将按 ${questions.length} 道题保存题目配置，并使用现有 OCR 文本块自动归属题干。`, ...result.notes.filter((note) => !/道题的题号、题干或题型置信度较低/.test(note))]
      : ['当前没有待确认题目，请先增加题目或重新上传试卷 OCR。'],
  }
}

function nextQuestionNo(questions: QuestionStructureResult['questions']): string {
  const used = new Set(questions.map((item) => item.questionNo))
  let candidate = 1
  while (used.has(String(candidate))) candidate += 1
  return String(candidate)
}

function addOcrTextToStructure(result: QuestionStructureResult, blocks: OcrBlock[]): QuestionStructureResult {
  const assignments = inferOcrBlockAssignments(blocks, result.questions.map((question) => ({ id: question.questionNo, question_no: question.questionNo })), { mode: 'question_paper' })
  const ocrTextByQuestionNo = Object.fromEntries(result.questions.map((question) => [question.questionNo, textForAssignedBlocks(blocks, assignments[question.questionNo] ?? [])]))
  return { ...result, ocrTextByQuestionNo }
}

async function recognizeQuestionPaperStructure(batchId: string, fallbackQuestionNo?: string, subject?: QuestionStructureSubject): Promise<QuestionStructureResult | null> {
  const ocrResponse = await api.batches.startOcr(batchId, { recognition_mode: recommendedQuestionPaperOcrMode(subject) }, createIdempotencyKey(), 'question_paper')
  let task = ocrResponse.data.task
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (['succeeded', 'partial_failed', 'failed'].includes(task.status)) break
    await new Promise((resolve) => window.setTimeout(resolve, 1000))
    task = (await api.tasks.get(task.task_id)).data
  }
  if (task.status === 'failed') throw new Error(task.error_message || '题干 OCR 失败。')
  if (!['succeeded', 'partial_failed'].includes(task.status)) throw new Error('题干 OCR 等待超时，请进入 OCR 校对页查看进度。')
  const pages = (await api.batches.pages(batchId, undefined, 'question_paper')).data
  const pageDetails = await Promise.all(pages.map((page) => api.pages.get(page.id).then((response) => response.data)))
  const blocks = getOrderedOcrBlocks(pageDetails)
  if (!blocks.length) return null
  const result = analyzeQuestionStructure(blocks, { fallbackQuestionNo, subject })
  const assignments = inferOcrBlockAssignments(blocks, result.questions.map((question) => ({ id: question.questionNo, question_no: question.questionNo })), { mode: 'question_paper' })
  const ocrTextByQuestionNo = Object.fromEntries(result.questions.map((question) => [question.questionNo, textForAssignedBlocks(blocks, assignments[question.questionNo] ?? [])]))
  return { ...result, ocrTextByQuestionNo }
}

function makeQuestionDraftsFromStructure(result: QuestionStructureResult, existing: Array<{ question_no: string; question_type: string; max_score: string | number; question_prompt: string | null; reference_answer: string | null; rubric_version_id: string | null }>, totalScore: string, preserveExistingStructure = false): BatchQuestionInput[] {
  if (preserveExistingStructure) {
    return result.questions.map((detected, index) => {
      const previous = existing[index]
      const autoText = result.ocrTextByQuestionNo?.[detected.questionNo] ?? ''
      const incompletePrompt = detected.evidence.includes('题干未完整识别')
      const autoAnswer = incompletePrompt ? '' : answerTextFromOcr(autoText)
      return {
        question_no: previous.question_no,
        question_type: previous.question_type === 'objective' ? 'objective' : 'subjective',
        max_score: String(previous.max_score),
        question_prompt: questionPromptFromOcr(detected, autoText, previous.question_prompt),
        reference_answer: previous.reference_answer ?? (autoAnswer || null),
        rubric_version_id: previous.rubric_version_id,
      }
    })
  }
  const total = Number(totalScore)
  const isInitialPlaceholder = existing.length === 1 && existing[0].question_no === '1' && Math.abs(Number(existing[0].max_score) - total) < 0.0001 && !existing[0].question_prompt && !existing[0].reference_answer?.trim()
  const reusableExisting = isInitialPlaceholder ? [] : existing
  const existingByNo = new Map(reusableExisting.map((question) => [question.question_no, question]))
  const canReuseScores = result.questions.length === existing.length && existing.every((question) => Number.isFinite(Number(question.max_score)))
  const existingScoreTotal = existing.reduce((sum, question) => sum + Number(question.max_score), 0)
  const evenlyDistributed = result.questions.length > 0 ? Math.floor((total / result.questions.length) * 100) / 100 : 0
  const scores = result.questions.map((question, index) => {
    if (canReuseScores && Math.abs(existingScoreTotal - total) < 0.0001) return String(existingByNo.get(question.questionNo)?.max_score ?? existing[index]?.max_score ?? evenlyDistributed)
    if (index === result.questions.length - 1) return String(Math.round((total - evenlyDistributed * (result.questions.length - 1)) * 100) / 100)
    return String(evenlyDistributed)
  })
  return result.questions.map((detected, index) => {
    const previous = existingByNo.get(detected.questionNo) ?? (reusableExisting.length === result.questions.length ? reusableExisting[index] : undefined)
    // 未能从 OCR 确认题型时先生成主观题草稿：主观题允许题干和答案后续人工补录，
    // 客观题则必须先有参考答案，默认成客观题会让刚创建的批次无法进入后续流程。
    const questionType: 'objective' | 'subjective' = detected.questionType === 'objective' ? 'objective' : detected.questionType === 'subjective' ? 'subjective' : previous?.question_type === 'objective' ? 'objective' : 'subjective'
    const autoText = result.ocrTextByQuestionNo?.[detected.questionNo] ?? ''
    return {
      question_no: detected.questionNo,
      question_type: questionType,
      max_score: scores[index],
      question_prompt: questionPromptFromOcr(detected, autoText, previous?.question_prompt),
      reference_answer: previous?.reference_answer ?? answerTextFromOcr(autoText),
      rubric_version_id: questionType === 'subjective' ? previous?.rubric_version_id ?? null : null,
    }
  })
}

function QuestionPaperDeleteModal({ file, error, submitting, onClose, onConfirm }: { file: SourceFile; error: string; submitting: boolean; onClose: () => void; onConfirm: () => void }) {
  const modalRef = useModalAccessibility(true, submitting, onClose)
  const [confirmText, setConfirmText] = useState('')
  const inputId = `question-paper-delete-confirm-${file.file_id}`
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !submitting) onClose() }}><section ref={modalRef} className="modal question-paper-delete-modal" role="dialog" aria-modal="true" aria-labelledby="question-paper-delete-title"><div className="modal-header"><div><span className="eyebrow">OCR 校对 · 文件维护</span><h2 id="question-paper-delete-title">删除误上传的试卷</h2><p>这只会删除当前试卷文件及其页面、OCR 缓存，不会删除学生作业。</p></div><button className="icon-button" type="button" aria-label="关闭试卷删除确认" onClick={onClose} disabled={submitting}><X size={20} /></button></div><form className="modal-form" onSubmit={(event) => { event.preventDefault(); if (confirmText.trim() === '删除') onConfirm() }}>{error && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{error}</span></div>}<div className="batch-purge-warning"><WarningCircle size={22} weight="fill" aria-hidden="true" /><div><strong>确认删除“{file.original_name}”？</strong><span>删除后无法恢复；如果 OCR 文本块已经绑定题干，系统会拒绝删除并保留校对记录。</span></div></div><label className="field" htmlFor={inputId}><span>输入“删除”确认 <em>*</em></span><input id={inputId} value={confirmText} onChange={(event) => setConfirmText(event.target.value)} placeholder="删除" autoComplete="off" disabled={submitting} /></label><div className="modal-actions"><SecondaryButton onClick={onClose} disabled={submitting}>取消</SecondaryButton><button className="button batch-purge-confirm" type="submit" disabled={submitting || confirmText.trim() !== '删除'}>{submitting ? '删除中…' : '确认删除'}<TrashSimple size={17} aria-hidden="true" /></button></div></form></section></div>
}

function WorkflowItem({ index, label, done = false, current = false }: { index: string; label: string; done?: boolean; current?: boolean }) { return <div className={`workflow-item ${done ? 'done' : ''} ${current ? 'current' : ''}`}><span>{done ? <Check size={14} weight="bold" aria-hidden="true" /> : index}</span><strong>{label}</strong></div> }
function gradingRunStatusLabel(status: string | undefined, executionScope?: 'run' | 'single_task', taskStatus?: string | null): string {
  if (executionScope === 'single_task') {
    if (taskStatus === 'queued') return '单题重跑已排队，等待 Worker 领取'
    if (taskStatus === 'running') return '正在重跑当前题目'
    if (taskStatus === 'failed') return '本次单题重跑失败'
    if (taskStatus === 'succeeded') return '本次单题重跑完成'
    if (taskStatus === 'partial_failed') return '本次单题重跑未完全完成'
  }
  if (status === 'queued') return '排队中，等待 Worker 领取'
  if (status === 'running') return '正在批改'
  if (status === 'succeeded') return '批改完成，等待教师复核'
  if (status === 'partial_failed') return '部分完成，存在失败任务'
  if (status === 'failed') return '批改失败'
  return '尚未发起'
}

function GradingReview({ notify, initialBatchId }: { notify: (message: string) => void; initialBatchId?: string }) {
  const [batches, setBatches] = useState<AssignmentBatch[]>([])
  const [batchId, setBatchId] = useState('')
  const [queueStatus, setQueueStatus] = useState<'pending_review' | 'reviewed' | 'all'>('all')
  const [queueQuery, setQueueQuery] = useState('')
  const [onlyAnomalies, setOnlyAnomalies] = useState(false)
  const [queueSort, setQueueSort] = useState<'priority' | 'score' | 'student'>('priority')
  const [queue, setQueue] = useState<ReviewItem[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [item, setItem] = useState<ReviewItem | null>(null)
  const [previewImage, setPreviewImage] = useState<ImagePreviewTarget | null>(null)
  const [history, setHistory] = useState<ReviewHistory | null>(null)
  const [taskCount, setTaskCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [statusMessage, setStatusMessage] = useState('请选择批次查看真实复核结果。')
  const [gradingRun, setGradingRun] = useState<GradingRun | null>(null)
  const [scoreDraft, setScoreDraft] = useState('')
  const [commentDraft, setCommentDraft] = useState('')
  const [reasonDraft, setReasonDraft] = useState('')
  const [reasonValidationError, setReasonValidationError] = useState('')
  const reasonInputRef = useRef<HTMLInputElement>(null)
  const [anomalyResolution, setAnomalyResolution] = useState<'confirmed' | 'ignored' | 'resolved' | ''>('')
  const [gradingPreflight, setGradingPreflight] = useState<Awaited<ReturnType<typeof api.batches.validate>>['data'] | null>(null)

  const selectedBatch = useMemo(() => batches.find((batch) => batch.id === batchId) ?? null, [batches, batchId])
  const visibleQueue = useMemo(() => {
    const query = queueQuery.trim().toLowerCase()
    const filtered = queue.filter((entry) => {
      const student = `${entry.student.display_name || ''}${entry.student.student_code || ''}`.toLowerCase()
      const questionType = entry.question.question_type === 'objective' ? '客观题' : '主观题'
      const matchesQuery = !query || student.includes(query) || (entry.question.question_no || '').toLowerCase().includes(query) || questionType.includes(query)
      return matchesQuery && (!onlyAnomalies || entry.anomalies.length > 0)
    })
    return [...filtered].sort((left, right) => {
      if (queueSort === 'student') return (left.student.display_name || left.student.student_code || '').localeCompare(right.student.display_name || right.student.student_code || '')
      if (queueSort === 'score') return Number(right.teacher_score ?? right.suggested_score ?? -1) - Number(left.teacher_score ?? left.suggested_score ?? -1)
      const anomalyWeight = (entry: ReviewItem) => entry.anomalies.some((anomaly) => anomaly.severity === 'blocking') ? 0 : entry.anomalies.length ? 1 : entry.result_status === 'reviewed' ? 3 : 2
      return anomalyWeight(left) - anomalyWeight(right)
    })
  }, [queue, queueQuery, onlyAnomalies, queueSort])

  const moveSelection = (direction: -1 | 1) => {
    if (!visibleQueue.length) return
    const currentIndex = visibleQueue.findIndex((entry) => entry.id === selectedId)
    const nextIndex = currentIndex < 0 ? 0 : (currentIndex + direction + visibleQueue.length) % visibleQueue.length
    setSelectedId(visibleQueue[nextIndex].id)
  }

  const loadBatches = async () => {
    setLoading(true)
    setError('')
    try {
      const response = await api.batches.list({ page_size: 100 })
      setBatches(response.data)
      setBatchId((current) => {
        if (initialBatchId && response.data.some((batch) => batch.id === initialBatchId)) return initialBatchId
        if (current && response.data.some((batch) => batch.id === current)) return current
        return response.data[0]?.id || ''
      })
      setStatusMessage(response.data.length ? '请选择需要复核的作业批次。' : '暂无可复核批次，请先完成上传、OCR 校对和批改。')
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '批次加载失败，请检查服务端状态。')
    } finally {
      setLoading(false)
    }
  }

  const loadQueue = async (options: { preserveSelected?: boolean } = {}): Promise<ReviewItem[]> => {
    if (!batchId) {
      setQueue([])
      setTaskCount(0)
      setGradingRun(null)
      setGradingPreflight(null)
      return []
    }
    setLoading(true)
    setError('')
    try {
      const [queueResponse, taskResponse] = await Promise.all([
        api.grading.reviewQueue(batchId, { status: queueStatus }),
        api.grading.listTasks(batchId),
      ])
      const runResponse = await api.grading.getLatestRun(batchId)
      const validationResponse = await api.batches.validate(batchId).catch(() => null)
      setGradingPreflight(validationResponse?.data ?? null)
      setGradingRun(runResponse.data)
      setQueue(queueResponse.data)
      setTaskCount(taskResponse.data.length)
      const runIsActive = Boolean(runResponse.data && ['queued', 'running'].includes(runResponse.data.status))
      setSelectedId((current) => current && (options.preserveSelected || runIsActive || queueResponse.data.some((entry) => entry.id === current)) ? current : queueResponse.data[0]?.id || '')
      if (runResponse.data && ['queued', 'running'].includes(runResponse.data.status)) {
        setStatusMessage(`批改${runResponse.data.status === 'queued' ? '已排队，等待 Worker 领取' : '正在执行'}：${runResponse.data.progress_current}/${runResponse.data.progress_total || runResponse.data.total_tasks} 个任务。`)
      } else {
        setStatusMessage(queueResponse.data.length ? `已加载 ${queueResponse.data.length} 条${queueStatus === 'all' ? '评分结果（含已复核）' : queueStatus === 'reviewed' ? '已复核结果' : '待处理/复核结果'}。` : '当前筛选没有真实评分结果。')
      }
      return queueResponse.data
    } catch (cause) {
      setQueue([])
      setError(cause instanceof ApiClientError ? cause.message : '复核队列加载失败，请重试。')
      return []
    } finally {
      setLoading(false)
    }
  }

  const loadItem = async (id: string) => {
    if (!id) {
      setItem(null)
      return
    }
    try {
      const response = await api.grading.getReviewItem(id)
      setItem(response.data)
      setScoreDraft(response.data.teacher_score ?? response.data.suggested_score ?? '')
      setCommentDraft(response.data.teacher_comment ?? '')
      setReasonDraft('')
      setReasonValidationError('')
      setAnomalyResolution('')
      setHistory(null)
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '评分详情加载失败，请刷新队列。')
    }
  }

  useEffect(() => { void loadBatches() }, [initialBatchId])
  useEffect(() => { void loadQueue() }, [batchId, queueStatus])
  useEffect(() => { void loadItem(selectedId) }, [selectedId])
  useEffect(() => {
    if (!batchId || !gradingRun || !['queued', 'running'].includes(gradingRun.status)) return
    let active = true
    const pollRun = async () => {
      try {
        const response = await api.grading.getLatestRun(batchId)
        if (!active) return
        setGradingRun(response.data)
        if (response.data && !['queued', 'running'].includes(response.data.status)) {
          const currentSelection = selectedId
          await loadQueue({ preserveSelected: true })
          if (currentSelection) await loadItem(currentSelection)
        }
      } catch {
        // 后台轮询失败不覆盖当前状态，用户仍可点击“刷新队列”重试。
      }
    }
    const timer = window.setInterval(() => void pollRun(), 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [batchId, gradingRun?.status, selectedId])

  const startGrading = async () => {
    if (!batchId) return
    setWorking(true)
    setError('')
    try {
      const validation = await api.batches.validate(batchId)
      setGradingPreflight(validation.data)
      if (!validation.data.valid_for_grading) {
        const messages = (validation.data.grading_errors?.length ? validation.data.grading_errors : validation.data.errors).map((detail) => detail.message || detail.code || '存在未满足的批改前置条件')
        setError(`当前批次不能发起批改：${messages.slice(0, 4).join('；')}${messages.length > 4 ? `；另有 ${messages.length - 4} 项` : ''}`)
        setStatusMessage('请按下方前置条件处理完成后再发起批改。')
        return
      }
      const retryFailedRun = Boolean(gradingRun && ['failed', 'partial_failed'].includes(gradingRun.status))
      const response = await api.grading.createRun(batchId, { scope: {}, ...(retryFailedRun ? { force_rerun: true } : {}) }, createIdempotencyKey())
      setGradingRun(response.data.run)
      setStatusMessage(response.data.message || `批改运行已入队：${response.data.run.id}`)
      notify('批改运行已入队，完成后可刷新复核队列。')
      await loadQueue()
    } catch (cause) {
      if (cause instanceof ApiClientError) {
        const details = cause.details.map((detail) => detail.message || detail.code).filter(Boolean)
        setError(details.length ? `${cause.message} ${details.slice(0, 4).join('；')}${details.length > 4 ? `；另有 ${details.length - 4} 项` : ''}` : cause.message)
        setStatusMessage(cause.code === 'MODEL_KEY_UNAVAILABLE' ? '请到“模型配置”填写并保存 API Key，保存后再发起批改。' : '请按下方前置条件处理完成后再发起批改。')
      } else {
        setError('批改运行提交失败，请先处理前置校验。')
      }
    } finally {
      setWorking(false)
    }
  }

  const saveReview = async (confirm: boolean) => {
    if (!item) return
    const scoreText = scoreDraft.trim()
    const teacherScore = scoreText ? Number(scoreText) : Number.NaN
    const maxScore = Number(item.question.max_score)
    if (!scoreText || !Number.isFinite(teacherScore)) {
      setError('请填写有效的教师确认分数。')
      return
    }
    if (!Number.isFinite(maxScore) || teacherScore < 0 || teacherScore > maxScore) {
      setError(`教师确认分数必须在 0–${item.question.max_score ?? '题目分值'} 范围内。`)
      return
    }
    const reopeningReviewedResult = item.result_status === 'reviewed' && !confirm
    if ((reasonRequired || reopeningReviewedResult) && !reasonDraft.trim()) {
      setReasonValidationError('此操作需要填写修改原因。')
      setError(reopeningReviewedResult && !reasonRequired ? '撤回复核状态需要填写原因。' : '请先填写修改原因，再保存或确认本题。')
      window.requestAnimationFrame(() => {
        reasonInputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        reasonInputRef.current?.focus()
      })
      return
    }
    setWorking(true)
    setError('')
    setReasonValidationError('')
    try {
      const payload = {
        teacher_score: teacherScore,
        teacher_comment: commentDraft.trim() || null,
        anomaly_resolution: anomalyResolution || null,
        reason: reasonDraft.trim() || null,
        version: item.version,
      }
      const response = confirm
        ? await api.grading.confirmReviewItem(item.id, payload)
        : await api.grading.saveReviewItem(item.id, payload)
      setItem(response.data)
      setReasonDraft('')
      setReasonValidationError('')
      notify(confirm ? '教师复核结果已确认并持久化。' : '教师修改已保存，结果仍保持待复核。')
      setStatusMessage(confirm ? '本题已确认；正式成绩仍以整份作业全部复核为准。' : '修改已保存，当前结果仍未进入正式成绩。')
      const nextQueue = await loadQueue()
      if (confirm) {
        const next = nextQueue.find((entry) => entry.id !== item.id && entry.result_status !== 'reviewed')
        if (next) setSelectedId(next.id)
      }
    } catch (cause) {
      if (cause instanceof ApiClientError && cause.code === 'REVIEW_REASON_REQUIRED') {
        setReasonValidationError('后端要求填写修改原因，请补充原因后重试。')
        setError(cause.message)
        window.requestAnimationFrame(() => {
          reasonInputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
          reasonInputRef.current?.focus()
        })
      } else {
        setError(cause instanceof ApiClientError ? cause.message : '复核结果保存失败，请检查分数、原因和版本。')
      }
    } finally {
      setWorking(false)
    }
  }

  const rerunReview = async () => {
    if (!item) return
    if (item.result_status === 'reviewed' && !reasonDraft.trim()) {
      setReasonValidationError('重新批改已复核题目需要填写原因。')
      setError('请填写重新批改原因后再提交。')
      window.requestAnimationFrame(() => {
        reasonInputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        reasonInputRef.current?.focus()
      })
      return
    }
    setWorking(true)
    setError('')
    try {
      const response = await api.grading.rerunReviewItem(item.id, { reason: reasonDraft.trim() || null, version: item.version })
      notify('当前题目已重新入队，原结果不会被当作新的正式成绩。')
      setStatusMessage(`题目重跑已入队：${response.data.task.id}`)
      await loadQueue()
      await loadItem(item.id)
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '题目重跑失败，请检查任务状态。')
    } finally {
      setWorking(false)
    }
  }

  const openHistory = async () => {
    if (!item) return
    setWorking(true)
    try {
      const response = await api.grading.reviewHistory(item.id)
      setHistory(response.data)
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '历史记录加载失败。')
    } finally {
      setWorking(false)
    }
  }

  const itemNumber = item?.question.question_no || '—'
  const hasAnomaly = Boolean(item?.anomalies.length)
  const originalName = item?.source_page.original_name || ''
  const isPdf = originalName.toLowerCase().endsWith('.pdf')
  const gradingRunActive = Boolean(gradingRun && ['queued', 'running'].includes(gradingRun.status))
  const gradingRunRetryable = Boolean(gradingRun && ['failed', 'partial_failed'].includes(gradingRun.status))
  const progressCurrent = gradingRun?.progress_current ?? 0
  const progressTotal = gradingRun?.progress_total || gradingRun?.total_tasks || 0
  const progressPercent = progressTotal ? Math.min(100, Math.round((progressCurrent / progressTotal) * 100)) : 0
  const runCounts = gradingRun?.counts ?? {}
  const isSingleTaskExecution = Boolean(gradingRun && (gradingRun.execution_scope === 'single_task' || (progressTotal > 0 && progressTotal < gradingRun.total_tasks)))
  const persistedScore = item?.teacher_score ?? item?.suggested_score ?? null
  const currentScore = scoreDraft.trim() ? Number(scoreDraft) : null
  const scoreChanged = Boolean(item && currentScore !== null && (persistedScore === null || Number(persistedScore) !== currentScore))
  const commentChanged = Boolean(item && (commentDraft.trim() || null) !== (item.teacher_comment?.trim() || null))
  const reasonRequired = Boolean(item && (scoreChanged || commentChanged || anomalyResolution))
  const reasonFieldRequired = reasonRequired || Boolean(reasonValidationError)
  const preflightErrors = gradingPreflight?.grading_errors?.length ? gradingPreflight.grading_errors : gradingPreflight?.errors ?? []
  const objectiveRerunIsIndependent = Boolean(item?.question.question_type === 'objective' && preflightErrors.length && preflightErrors.every((detail) => detail.code === 'MODEL_KEY_UNAVAILABLE' || (detail.code || '').startsWith('SUBJECTIVE_')))

  return <><div className="content-wrap">
    <PageHeader eyebrow={selectedBatch ? `批改流程 · 第 3/4 步 · ${selectedBatch.title}` : '批改流程 · 第 3/4 步'} title="学生作业批改与教师复核" description="OCR 和答案覆盖完成后发起批改；教师确认每题结果后，才进入第 4 步评分报告。" action={<StatusPill tone="warning">AI 生成，教师复核后生效</StatusPill>} />
    <div className="workflow-strip"><WorkflowItem index="1" label="班级、批次与上传" done /><WorkflowItem index="2" label="题干/答案与 OCR 校对" done /><WorkflowItem index="3" label="学生 OCR 与批改" current /><WorkflowItem index="4" label="评分与报告" /></div>
    <section className="panel grading-toolbar-panel">
      <div className="grading-toolbar-grid">
        <label className="field"><span>正式批次 <em>*</em></span><select value={batchId} onChange={(event) => setBatchId(event.target.value)} disabled={loading || working}><option value="">{loading ? '正在加载批次…' : batches.length ? '请选择批次' : '暂无批次'}</option>{batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.title} · {batch.class_name || batch.class_id}</option>)}</select></label>
        <label className="field"><span>结果筛选</span><select value={queueStatus} onChange={(event) => setQueueStatus(event.target.value as typeof queueStatus)} disabled={working}><option value="pending_review">待处理（待复核/重跑）</option><option value="reviewed">已复核</option><option value="all">全部结果（含已复核）</option></select></label>
        <div className="grading-toolbar-actions"><SecondaryButton icon={ArrowsClockwise} onClick={() => void loadQueue()} disabled={!batchId || loading || working}>刷新队列</SecondaryButton>{item && <SecondaryButton icon={ArrowsClockwise} onClick={() => void rerunReview()} disabled={working || gradingRunActive}>重跑本题（当前学生）</SecondaryButton>}<PrimaryButton icon={CheckSquare} onClick={startGrading} disabled={!batchId || working || gradingRunActive}>{working ? '处理中…' : gradingRunActive ? '批改进行中…' : gradingRunRetryable ? '重新批改整批' : '发起整批批改'}</PrimaryButton></div>
      </div>
      <div className="workflow-message" role="status" aria-live="polite"><Info size={17} aria-hidden="true" /><span>{statusMessage} 当前批改任务数：{taskCount}。</span></div>
      {gradingRun && <div className={`workflow-message ${['failed', 'partial_failed'].includes(gradingRun.status) ? 'error' : ''}`} role="status" aria-live="polite"><Info size={17} aria-hidden="true" /><span>批改状态：{gradingRunStatusLabel(gradingRun.status, isSingleTaskExecution ? 'single_task' : 'run', gradingRun.task_status)} · {progressCurrent}/{progressTotal || '—'} {isSingleTaskExecution ? '项单题重跑' : '个任务'}{isSingleTaskExecution ? ` · 批次累计 ${runCounts.succeeded ?? 0} 项成功、${runCounts.failed ?? 0} 项失败` : ''}{gradingRun.worker_id ? ` · Worker ${gradingRun.worker_id}` : ''}{gradingRun.error_message ? ` · ${gradingRun.error_message}` : ''}<span className="grading-inline-progress" aria-label={`批改进度 ${progressPercent}%`}><span style={{ width: `${progressPercent}%` }} /></span></span></div>}
      {gradingPreflight && <div className={`workflow-message ${gradingPreflight.valid_for_grading || objectiveRerunIsIndependent ? '' : 'error'}`} role={gradingPreflight.valid_for_grading || objectiveRerunIsIndependent ? 'status' : 'alert'}><Info size={17} aria-hidden="true" /><span>{gradingPreflight.valid_for_grading ? '批改前置条件已满足：题干、评分标准、学生归属和答案覆盖均已确认。' : objectiveRerunIsIndependent ? <>当前提示针对整批批改，因批次含主观题而需要相应配置或模型 Key；单独重跑当前客观题使用规则引擎，不需要主观题 API Key。点击“重跑本题（当前学生）”可只重跑当前学生的这道题。</> : <>批改前置条件未满足：{preflightErrors.slice(0, 5).map((detail, index) => <span key={`${detail.code || 'detail'}-${index}`}>{index ? '；' : ''}{detail.message || detail.code || '请查看批次详情'}</span>)}{preflightErrors.length > 5 ? '；请继续查看后端校验详情' : ''}</>}</span></div>}
      {error && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{error}</span><button className="text-button" type="button" onClick={() => void loadQueue()} disabled={!batchId || working}>重试</button></div>}
    </section>
    <div className="review-layout real-review-layout">
      <section className="panel review-queue">
        <div className="review-queue-head"><div><h2>复核队列</h2><p>{visibleQueue.length ? `${visibleQueue.length} / ${queue.length} 条真实评分结果` : '没有符合当前筛选的结果'}</p></div><span className="queue-filter"><WarningCircle size={16} aria-hidden="true" />异常优先</span></div><div className="queue-tools"><label className="input-with-icon queue-search"><MagnifyingGlass size={16} aria-hidden="true" /><span className="sr-only">搜索学生、题号或题型</span><input value={queueQuery} onChange={(event) => setQueueQuery(event.target.value)} placeholder="搜索学生、学号、题号或题型（如客观题）" /></label><label className="queue-check"><input type="checkbox" checked={onlyAnomalies} onChange={(event) => setOnlyAnomalies(event.target.checked)} />仅异常</label><label className="queue-sort"><span className="sr-only">队列排序</span><select value={queueSort} onChange={(event) => setQueueSort(event.target.value as typeof queueSort)}><option value="priority">按异常优先</option><option value="score">按分数</option><option value="student">按学生</option></select></label></div>
        <div className="queue-list">{visibleQueue.map((entry) => <button key={entry.id} className={`queue-item ${selectedId === entry.id ? 'active' : ''}`} type="button" onClick={() => setSelectedId(entry.id)}><div className={`queue-status ${entry.anomalies.some((anomaly) => anomaly.severity === 'blocking') ? 'danger' : entry.anomalies.length ? 'warning' : entry.result_status === 'reviewed' ? 'success' : 'neutral'}`}><span>{entry.question.question_no || '—'}</span></div><div className="queue-copy"><strong>{entry.student.display_name || entry.student.student_code || '未归属学生'}</strong><span>第 {entry.question.question_no || '—'} 题 · {entry.question.question_type === 'subjective' ? '主观题' : '客观题'} · {entry.result_status === 'reviewed' ? '已复核' : entry.result_status === 'stale' ? '重跑处理中' : entry.result_status === 'failed' ? '批改失败' : '待复核'}</span></div><b>{entry.teacher_score ?? entry.suggested_score ?? '待评分'}</b><CaretRight size={17} aria-hidden="true" /></button>)}{!visibleQueue.length && <div className="empty-inline"><CheckSquare size={27} aria-hidden="true" /><span>{queue.length ? '没有符合当前搜索或异常筛选的结果。' : batchId ? '当前没有可展示的真实评分结果；请先发起批改或刷新任务状态。' : '请选择批次。'}</span></div>}</div>
        <div className="queue-progress"><div><span>当前筛选结果</span><strong>{visibleQueue.length} 条</strong></div><div className="progress-track"><span style={{ width: `${visibleQueue.length ? Math.min(100, Math.max(8, (visibleQueue.filter((entry) => entry.result_status === 'reviewed').length / visibleQueue.length) * 100)) : 0}%` }} /></div></div>
      </section>
      <section className="review-detail">
        {!item && <div className="panel empty-detail"><CheckSquare size={32} aria-hidden="true" /><h2>选择一条真实评分结果</h2><p>批改 Worker 生成结果后，会在这里展示学生答案、评分依据和教师复核入口。</p></div>}
        {item && <>
          <div className="review-detail-head"><div><span className="detail-kicker">{item.student.display_name || item.student.student_code || '未归属学生'} · 第 {itemNumber} 题</span><h2>{item.question.question_type === 'subjective' ? '主观题评分' : '客观题规则判分'}</h2></div><div className="detail-head-actions"><button className="text-button" type="button" onClick={() => moveSelection(-1)} disabled={working || visibleQueue.length < 2}>上一条</button><button className="text-button" type="button" onClick={() => moveSelection(1)} disabled={working || visibleQueue.length < 2}>下一条</button><button className="icon-button" type="button" aria-label="查看原图证据" onClick={() => notify(item.source_page.id ? '原图证据已显示在下方。' : '当前结果没有可用原图页。')}><Eye size={20} /></button><button className="icon-button" type="button" aria-label="查看操作历史" onClick={() => void openHistory()} disabled={working}><Clock size={20} /></button></div></div>
          {hasAnomaly && <div className="exception-banner" role="alert"><WarningCircle size={20} weight="fill" aria-hidden="true" /><div><strong>当前结果含异常提示</strong><span>异常仅作为复核线索，不直接等同于抄袭或零分结论。</span></div></div>}
          {item.task_status === 'failed' && <div className="exception-banner" role="alert"><WarningCircle size={20} weight="fill" aria-hidden="true" /><div><strong>本题批改任务失败</strong><span>{item.task_error_message || item.task_error_code || 'Worker 未能生成评分结果，请查看原图和答案后重试。'}{gradingRunActive ? ' 当前批次仍在处理，结束后可单独重跑本题。' : ' 可仅重跑当前学生的本题，不影响其他学生的结果。'}</span></div><PrimaryButton icon={ArrowsClockwise} onClick={() => void rerunReview()} disabled={working || gradingRunActive}>{working ? '正在提交…' : gradingRunActive ? '批次处理中' : '仅重跑本题'}</PrimaryButton></div>}
          <div className="evidence-grid">
            <section className="detail-card"><div className="detail-card-title"><h3>题目与作答</h3><StatusPill tone="info">答案 v{item.answer.version_no ?? '—'}</StatusPill></div><div className="prompt-box"><span>题目要求</span><p>{item.question.question_prompt || '当前题目未保存题干。请回到试卷 OCR 校对补充主观题题干。'}</p></div><div className="answer-box"><span>学生答案</span><p>{item.answer.is_blank_confirmed ? '教师已确认本题为空题。' : item.answer.answer_text || '当前没有可显示的答案文本。'}</p></div>{item.source_page.id && <div className="grading-source-preview">{isPdf ? <iframe title="评分原图 PDF" src={api.pages.originalUrl(item.source_page.id)} /> : <ZoomableImage src={api.pages.originalUrl(item.source_page.id)} alt={`${originalName} 第 ${item.source_page.page_index ?? '未知'} 页`} onOpen={() => setPreviewImage({ src: api.pages.originalUrl(item.source_page.id ?? ''), alt: `${originalName} 第 ${item.source_page.page_index ?? '未知'} 页`, title: `学生作业原图 · 第 ${item.source_page.page_index ?? '未知'} 页` })} />}</div>}</section>
            <section className="detail-card score-card"><div className="detail-card-title"><h3>评分依据</h3><span className="model-tag"><Sparkle size={14} aria-hidden="true" />{item.model_name || item.rule_version || '规则引擎'}</span></div><div className="rubric-meta"><span>规则版本：{item.rule_version || '—'}</span><span>提示词版本：{item.prompt_version || '不适用'}</span></div><div className="score-suggestion"><div><span>AI / 规则建议分</span><strong>{item.suggested_score ?? '—'} <small>/ {item.question.max_score ?? '—'}</small></strong></div><StatusPill tone={item.task_status === 'queued' || item.task_status === 'running' ? 'info' : item.task_status === 'failed' ? 'danger' : item.result_status === 'reviewed' ? 'success' : 'warning'}>{item.task_status === 'queued' ? '批改排队中' : item.task_status === 'running' ? '正在批改' : item.task_status === 'failed' ? '批改失败' : item.result_status === 'reviewed' ? '已复核' : item.result_status === 'stale' ? '等待重批' : '待复核'}</StatusPill></div><div className="score-points">{item.points.length ? item.points.map((point) => <div key={point.id}><span>{point.label || point.rubric_point_id}</span><b>{point.suggested_score} / {point.max_score || '—'}</b></div>) : <div><span>判分证据</span><b>{item.evidence ? '已记录' : '—'}</b></div>}</div><label className="field teacher-score"><span>教师确认分数 <em>*</em></span><div className="score-input"><input type="number" value={scoreDraft} onChange={(event) => { setScoreDraft(event.target.value); setReasonValidationError('') }} min="0" max={item.question.max_score ?? undefined} step="0.01" disabled={working} aria-describedby="score-helper" /><strong>/ {item.question.max_score ?? '—'}</strong></div><small id="score-helper">分数必须在 0–{item.question.max_score ?? '题目分值'} 范围内；修改建议分后，请在下方填写原因。</small></label></section>
          </div>
          <section className="detail-card feedback-card"><div className="detail-card-title"><div><h3>评语与复核结论</h3><p>保存修改不会直接进入正式成绩；确认后还需整份作业全部复核。</p></div><PencilSimple size={21} className="heading-icon" aria-hidden="true" /></div><div className="ai-comment-box"><span>AI 结构化评语</span><p>{item.ai_comment || '本题暂无 AI 评语。'}</p></div><label className="field"><span>教师评语</span><textarea value={commentDraft} onChange={(event) => { setCommentDraft(event.target.value); setReasonValidationError('') }} placeholder="在这里补充或改写教师评语" disabled={working} /></label><label className="field"><span>异常处理结论</span><select value={anomalyResolution} onChange={(event) => { setAnomalyResolution(event.target.value as typeof anomalyResolution); setReasonValidationError('') }} disabled={working}><option value="">暂不处理异常</option><option value="confirmed">确认异常提示</option><option value="resolved">标记已处理</option><option value="ignored">忽略提示</option></select></label><label className={`field review-reason-field ${reasonFieldRequired ? 'required' : ''}`}><span>修改原因{reasonFieldRequired && <em> *</em>}</span><input ref={reasonInputRef} value={reasonDraft} onChange={(event) => { setReasonDraft(event.target.value); if (event.target.value.trim()) { setReasonValidationError(''); setError('') } }} placeholder={reasonRequired ? '请填写本次改分、评语或异常处理的原因' : '选填；若撤回复核状态或修改评分内容时需要填写'} disabled={working} aria-required={reasonFieldRequired} aria-invalid={Boolean(reasonValidationError)} aria-describedby={reasonValidationError ? 'review-reason-error' : 'review-reason-helper'} />{reasonValidationError ? <small className="review-reason-error" id="review-reason-error" role="alert">{reasonValidationError}</small> : <small id="review-reason-helper">{reasonRequired ? '检测到评分、评语或异常处理有修改，需要记录原因。' : item.result_status === 'reviewed' ? '内容未修改时再次确认无需原因；若保存为待复核，撤回复核状态需要填写原因。' : '只有修改评分、评语或异常处理时，才需要填写原因。'}</small>}</label><div className="review-actions"><SecondaryButton onClick={() => void saveReview(false)} disabled={working}>保存待复核</SecondaryButton><PrimaryButton icon={Check} onClick={() => void saveReview(true)} disabled={working}>{item.result_status === 'reviewed' ? '再次确认结果' : '确认本题并进入下一题'}</PrimaryButton></div></section>
          <div className="history-strip"><Clock size={17} aria-hidden="true" /><span>版本 v{item.version} · {item.result_status === 'reviewed' ? '教师已确认' : '等待教师确认'} · 原始评分与教师修改均保留</span><button className="text-button" type="button" onClick={() => void openHistory()} disabled={working}>查看历史</button></div>
          {history && <section className="panel review-history-panel"><div className="panel-heading"><div><h3>复核历史</h3><p>只读展示当前评分结果的保存、确认和重跑记录。</p></div><Clock size={21} className="heading-icon" aria-hidden="true" /></div>{history.records.length ? <div className="review-history-list">{history.records.map((record) => <div key={record.id}><strong>{record.action}</strong><span>{record.reason || '未填写原因'} · {record.created_at}</span></div>)}</div> : <div className="empty-inline">当前结果暂无复核记录。</div>}</section>}
        </>}
      </section>
    </div>
  </div>{previewImage && <ImageLightbox target={previewImage} onClose={() => setPreviewImage(null)} />}</>
}

function Rubrics({ onCreate, notify, refreshToken }: { onCreate: () => void; notify: (message: string) => void; refreshToken: number }) {
  const [rubrics, setRubrics] = useState<Rubric[]>([])
  const [details, setDetails] = useState<Record<string, Rubric>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [workingId, setWorkingId] = useState('')
  const [editingRubric, setEditingRubric] = useState<Rubric | null>(null)
  const [editForm, setEditForm] = useState({ name: '', subject: '英语', score: '', points: '', examples: '' })
  const [editFormError, setEditFormError] = useState('')
  const [editSubmitting, setEditSubmitting] = useState(false)
  const [rubricQuery, setRubricQuery] = useState('')
  const [subjectFilter, setSubjectFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [versionHistoryRubric, setVersionHistoryRubric] = useState<Rubric | null>(null)
  const [versionHistory, setVersionHistory] = useState<RubricVersion[]>([])
  const [versionHistoryLoading, setVersionHistoryLoading] = useState(false)

  const loadRubrics = async () => {
    setLoading(true)
    setError('')
    try {
      const response = await api.rubrics.list({ question_type: 'subjective', subject: subjectFilter === 'all' ? undefined : subjectFilter, status: statusFilter === 'all' ? undefined : statusFilter })
      setRubrics(response.data)
      // /rubrics already returns current_version; avoid one detail request per card.
      setDetails(Object.fromEntries(response.data.map((rubric) => [rubric.id, rubric])))
    } catch (cause) {
      setRubrics([])
      setError(cause instanceof ApiClientError ? cause.message : '评分标准加载失败，请检查后端服务。')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void loadRubrics() }, [refreshToken, subjectFilter, statusFilter])

  const visibleRubrics = useMemo(() => {
    const normalizedQuery = rubricQuery.trim().toLowerCase()
    if (!normalizedQuery) return rubrics
    return rubrics.filter((rubric) => `${rubric.name}${rubric.subject}${rubric.id}`.toLowerCase().includes(normalizedQuery))
  }, [rubrics, rubricQuery])

  const openVersionHistory = async (rubric: Rubric) => {
    setVersionHistoryRubric(rubric)
    setVersionHistory([])
    setVersionHistoryLoading(true)
    try {
      const response = await api.rubrics.versions(rubric.id)
      setVersionHistory(response.data)
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '版本历史加载失败，请重试。')
    } finally {
      setVersionHistoryLoading(false)
    }
  }

  const toggleStatus = async (rubric: Rubric) => {
    setWorkingId(rubric.id)
    setError('')
    try {
      const response = rubric.status === 'active' ? await api.rubrics.deactivate(rubric.id) : await api.rubrics.activate(rubric.id)
      setRubrics((current) => current.map((item) => item.id === rubric.id ? { ...item, ...response.data } : item))
      setDetails((current) => ({ ...current, [rubric.id]: response.data }))
      notify(rubric.status === 'active' ? '评分标准已停用；历史批次仍保留已绑定版本。' : '评分标准已启用，可用于新建批次。')
    } catch (cause) {
      setError(cause instanceof ApiClientError ? cause.message : '评分标准状态更新失败，请重试。')
    } finally {
      setWorkingId('')
    }
  }

  const beginEdit = (rubric: Rubric) => {
    const detail = details[rubric.id]
    const version = detail?.current_version
    const singlePoint = version?.points.length === 1 ? version.points[0] : null
    const pointText = singlePoint?.label.includes('\n')
      ? singlePoint.label
      : version?.points.map((point) => `${point.label} ${point.max_score} 分`).join('\n') ?? ''
    setEditingRubric(rubric)
    setEditForm({
      name: detail?.name ?? rubric.name,
      subject: subjectLabels[(detail?.subject ?? rubric.subject) as BatchFormState['subject']] || detail?.subject || rubric.subject,
      score: version?.total_score ?? '',
      points: pointText,
      examples: version?.examples.map((example) => example.content).join('\n') ?? '',
    })
    setEditFormError('')
    setError('')
  }

  const submitEdit = async (event: FormEvent) => {
    event.preventDefault()
    setEditFormError('')
    if (!editingRubric || !editForm.name.trim() || !editForm.points.trim() || !editForm.score.trim()) {
      setEditFormError('请补充评分标准名称、题目总分和给大模型使用的评分说明。')
      return
    }
    const parsed = parseRubricInput(editForm.points, editForm.score)
    if (parsed.error) {
      setEditFormError(parsed.error)
      return
    }
    const validPoints = parsed.points
    const version = details[editingRubric.id]?.current_version?.version_no ?? editingRubric.current_version_no
    if (!version) {
      setError('当前评分标准没有可编辑的版本，请刷新后重试。')
      return
    }
    setEditSubmitting(true)
    setError('')
    try {
      const response = await api.rubrics.update(editingRubric.id, {
        name: editForm.name.trim(),
        subject: subjectApiValues[editForm.subject] || editForm.subject,
        question_type: 'subjective',
        total_score: editForm.score,
        points: validPoints,
        examples: editForm.examples.split(/\r?\n/).map((content) => content.trim()).filter(Boolean).map((content) => ({ content })),
        version,
      })
      setRubrics((current) => current.map((item) => item.id === editingRubric.id ? { ...item, ...response.data } : item))
      setDetails((current) => ({ ...current, [editingRubric.id]: response.data }))
      setEditingRubric(null)
      notify('评分标准已更新并生成新版本；历史绑定版本保持不变。')
    } catch (cause) {
      setEditFormError(cause instanceof ApiClientError ? cause.message : '评分标准更新失败，请检查评分说明、分值和版本状态。')
    } finally {
      setEditSubmitting(false)
    }
  }

  const versionHistoryPanel = versionHistoryRubric ? <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setVersionHistoryRubric(null) }}><section className="modal version-history-modal" role="dialog" aria-modal="true" aria-labelledby="rubric-history-title"><div className="modal-header"><div><span className="eyebrow">评分标准 · 只读历史</span><h2 id="rubric-history-title">{versionHistoryRubric.name} 的版本历史</h2><p>历史版本只读；已绑定题目继续使用原版本。</p></div><button className="icon-button" type="button" aria-label="关闭版本历史" onClick={() => setVersionHistoryRubric(null)}><X size={20} /></button></div>{versionHistoryLoading ? <div className="empty-detail"><Timer size={28} aria-hidden="true" /><p>正在读取版本历史…</p></div> : versionHistory.length ? <div className="version-history-list">{versionHistory.map((version) => <div className="version-history-row" key={version.id}><div><strong>v{version.version_no}</strong><span>{version.points.length} 个评分点 · 总分 {version.total_score} · {new Date(version.created_at).toLocaleString('zh-CN')}</span></div><StatusPill tone={version.status === 'retired' ? 'neutral' : 'success'}>{version.status === 'retired' ? '已停用' : '可用'}</StatusPill></div>)}</div> : <div className="empty-detail"><BookOpen size={28} aria-hidden="true" /><p>当前评分标准还没有版本记录。</p></div>}</section></div> : null
  const editModal = editingRubric ? <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !editSubmitting) setEditingRubric(null) }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="rubric-edit-title"><div className="modal-header"><div><span className="eyebrow">评分标准知识库 · 新版本</span><h2 id="rubric-edit-title">编辑评分标准</h2><p>保存后生成新版本，历史批次继续使用原绑定版本。</p></div><button className="icon-button" type="button" aria-label="关闭评分标准编辑" onClick={() => setEditingRubric(null)} disabled={editSubmitting}><X size={20} /></button></div><form className="modal-form" onSubmit={submitEdit}>{editFormError && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{editFormError}</span></div>}<label className="field"><span>标准名称 <em>*</em></span><input value={editForm.name} onChange={(event) => setEditForm((current) => ({ ...current, name: event.target.value }))} disabled={editSubmitting} /></label><div className="form-grid"><label className="field"><span>学科 <em>*</em></span><select value={editForm.subject} onChange={(event) => setEditForm((current) => ({ ...current, subject: event.target.value }))} disabled={editSubmitting}><option>语文</option><option>数学</option><option>英语</option></select></label><label className="field"><span>题目总分（仅用于批次参考） <em>*</em></span><input type="number" min="1" value={editForm.score} onChange={(event) => setEditForm((current) => ({ ...current, score: event.target.value }))} disabled={editSubmitting} /></label></div><label className="field"><span>给大模型的自然语言评分说明 <em>*</em></span><textarea value={editForm.points} onChange={(event) => setEditForm((current) => ({ ...current, points: event.target.value }))} placeholder="自由写步骤、关键点、扣分规则、边界情况和阅卷提示" aria-describedby="rubric-edit-points-help" disabled={editSubmitting} /><small id="rubric-edit-points-help">整段文字原样交给大模型；不拆分、不计算，也不会用题目满分截断模型评分。</small></label><label className="field"><span>示例作答</span><textarea value={editForm.examples} onChange={(event) => setEditForm((current) => ({ ...current, examples: event.target.value }))} placeholder="每行一个示例作答，可留空" disabled={editSubmitting} /></label><div className="modal-actions"><SecondaryButton onClick={() => setEditingRubric(null)} disabled={editSubmitting}>取消</SecondaryButton><PrimaryButton type="submit" icon={Check} disabled={editSubmitting}>{editSubmitting ? '保存中…' : '保存新版本'}</PrimaryButton></div></form></section></div> : null
  return <div className="content-wrap"><PageHeader eyebrow="评分标准知识库" title="可复用的评分依据" description="按学科和题型管理给大模型使用的自然语言评分说明、示例作答与版本状态。" action={<PrimaryButton onClick={onCreate}>新建评分标准</PrimaryButton>} />
    <div className="demo-notice soft"><Info size={18} aria-hidden="true" /><span>评分标准变更后，只会使受影响题目结果待重批；未受影响题目不会被无故重跑。</span></div>
    <section className="panel rubric-filters"><label className="field compact-field"><span>搜索标准</span><div className="input-with-icon"><MagnifyingGlass size={17} aria-hidden="true" /><input value={rubricQuery} onChange={(event) => setRubricQuery(event.target.value)} placeholder="标准名称或编号" /></div></label><label className="field compact-field"><span>学科</span><select value={subjectFilter} onChange={(event) => setSubjectFilter(event.target.value)}><option value="all">全部学科</option><option value="chinese">语文</option><option value="math">数学</option><option value="english">英语</option></select></label><label className="field compact-field"><span>状态</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">全部状态</option><option value="active">启用</option><option value="inactive">停用</option></select></label><span className="filter-result">显示 {rubrics.length} 条真实标准</span></section>
    {error && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{error}</span><button className="text-button" type="button" onClick={() => void loadRubrics()}>重试</button></div>}
    <section className="rubric-grid">{visibleRubrics.map((rubric) => { const detail = details[rubric.id]; const version = detail?.current_version; return <article className="rubric-card" key={rubric.id}><div className="rubric-card-top"><div className="rubric-icon"><BookOpen size={22} weight="duotone" aria-hidden="true" /></div><button className="icon-button" type="button" aria-label={`刷新${rubric.name}详情`} onClick={() => void loadRubrics()} disabled={loading || Boolean(workingId) || editSubmitting}><ArrowsClockwise size={19} /></button></div><div className="rubric-card-body"><div className="rubric-subject">{subjectLabels[rubric.subject as BatchFormState['subject']] || rubric.subject} · v{rubric.current_version_no ?? '—'}</div><h2>{rubric.name}</h2><p>{version ? `${version.points.map((point) => `${point.label} ${point.max_score} 分`).join(' · ') || '暂无评分点'}` : loading ? '正在读取评分点…' : '评分点详情暂不可用'}</p></div><div className="rubric-card-bottom"><StatusPill tone={rubric.status === 'active' ? 'success' : 'neutral'}>{rubric.status === 'active' ? '启用' : '停用'}</StatusPill><span>{version ? `${version.points.length} 个评分点 · ${version.total_score} 分` : '版本详情'}</span></div><div className="rubric-card-actions"><button className="rubric-edit" type="button" onClick={() => beginEdit(rubric)} disabled={workingId === rubric.id || editSubmitting || !version}>{editSubmitting && editingRubric?.id === rubric.id ? '编辑中…' : '编辑评分标准'}</button><button className="rubric-edit rubric-status-toggle" type="button" onClick={() => void toggleStatus(rubric)} disabled={workingId === rubric.id || editSubmitting}>{workingId === rubric.id ? '更新中…' : rubric.status === 'active' ? '停用标准' : '重新启用'}</button></div></article> })}{!loading && !visibleRubrics.length && <div className="panel empty-detail rubric-empty"><BookOpen size={32} aria-hidden="true" /><h2>{rubricQuery.trim() ? '没有匹配的评分标准' : '暂无评分标准'}</h2><p>{rubricQuery.trim() ? '请调整搜索词后重试。' : '创建第一个主观题评分标准后，批次配置页才能绑定评分标准版本。'}</p></div>}{loading && <div className="panel empty-detail rubric-empty"><Timer size={32} aria-hidden="true" /><h2>正在加载评分标准</h2><p>页面不会用演示卡片替代真实数据。</p></div>}</section>
    <section className="panel rubric-rules"><div className="panel-heading"><div><h2>版本与模型使用规则</h2><p>评分说明是给大模型看的自然语言上下文，保存后按版本绑定到批次题目。</p></div><ShieldCheck size={23} className="heading-icon" aria-hidden="true" /></div><div className="rule-grid"><InfoRow label="评分说明" value="自由填写步骤、关键点、扣分规则和边界情况，不解析文本内数字" /><InfoRow label="模型评分" value="按教师说明理解并返回原始评分，不用题目满分截断" /><InfoRow label="异常处理" value="模型失败、结构错误进入可恢复流程并保留教师复核" /></div></section><section className="panel rubric-history-launcher"><div className="panel-heading"><div><h2>版本历史</h2><p>查看服务端保存的评分标准版本及停用状态。</p></div><Clock size={22} className="heading-icon" aria-hidden="true" /></div><div className="history-launcher-list">{visibleRubrics.slice(0, 6).map((rubric) => <button key={rubric.id} className="history-launcher-row" type="button" onClick={() => void openVersionHistory(rubric)}><span><strong>{rubric.name}</strong><small>{subjectLabels[rubric.subject as BatchFormState['subject']] || rubric.subject} · 当前 v{rubric.current_version_no ?? '—'}</small></span><span className="text-button">查看历史 <ArrowRight size={15} aria-hidden="true" /></span></button>)}{!visibleRubrics.length && <div className="empty-inline">暂无可查看的版本历史。</div>}</div></section>{versionHistoryPanel}{editModal}</div>
}

function Reports({ mode, setMode, notify, navigate }: { mode: 'reviewed' | 'ai'; setMode: (value: 'reviewed' | 'ai') => void; notify: (message: string) => void; navigate: (page: PageKey) => void }) {
  const [batches, setBatches] = useState<AssignmentBatch[]>([])
  const [batchId, setBatchId] = useState('')
  const [report, setReport] = useState<Awaited<ReturnType<typeof api.reports.batch>>['data'] | null>(null)
  const [statistics, setStatistics] = useState<Awaited<ReturnType<typeof api.reports.statistics>>['data'] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reloadToken, setReloadToken] = useState(0)
  const [exportFormat, setExportFormat] = useState<'pdf' | 'docx'>('pdf')
  const [exportTask, setExportTask] = useState<Awaited<ReturnType<typeof api.reports.getExport>>['data'] | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')
  const [studentDetail, setStudentDetail] = useState<Awaited<ReturnType<typeof api.reports.student>>['data'] | null>(null)
  const [studentDetailLoading, setStudentDetailLoading] = useState(false)
  const [studentDetailError, setStudentDetailError] = useState('')
  const [reportPreviewImage, setReportPreviewImage] = useState<ImagePreviewTarget | null>(null)
  const studentModalRef = useModalAccessibility(Boolean(studentDetail), studentDetailLoading, () => setStudentDetail(null))
  const apiMode = mode === 'ai' ? 'ai_preview' : 'reviewed'

  useEffect(() => {
    let active = true
    api.batches.list({ page_size: 100 }).then((response) => {
      if (!active) return
      setBatches(response.data)
      setBatchId((current) => current || response.data[0]?.id || '')
    }).catch((cause) => {
      if (active) setError(cause instanceof ApiClientError ? cause.message : '批次加载失败，报告无法读取。')
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!batchId) {
      setReport(null)
      setStatistics(null)
      setLoading(false)
      return
    }
    let active = true
    setLoading(true)
    setError('')
    Promise.all([api.reports.batch(batchId, { mode: apiMode, page_size: 200 }), api.reports.statistics(batchId, apiMode)]).then(([reportResponse, statisticsResponse]) => {
      if (!active) return
      setReport(reportResponse.data)
      setStatistics(statisticsResponse.data)
    }).catch((cause) => {
      if (!active) return
      setReport(null)
      setStatistics(null)
      setError(cause instanceof ApiClientError ? cause.message : '报告查询失败，请重试。')
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [batchId, apiMode, reloadToken])

  useEffect(() => {
    setExportTask(null)
    setExportError('')
    setStudentDetail(null)
    setStudentDetailError('')
    setReportPreviewImage(null)
  }, [batchId, apiMode])

  async function openStudentDetail(student: ReportStudentRow) {
    if (!batchId || !student.student_id) {
      notify('当前成绩行没有可追溯的学生标识。')
      return
    }
    setStudentDetailLoading(true)
    setStudentDetailError('')
    try {
      const response = await api.reports.student(batchId, student.student_id, apiMode)
      setStudentDetail(response.data)
    } catch (cause) {
      setStudentDetailError(cause instanceof ApiClientError ? cause.message : '个人成绩详情加载失败，请重试。')
    } finally {
      setStudentDetailLoading(false)
    }
  }

  async function startExport() {
    if (!batchId || exporting) return
    setExporting(true)
    setExportError('')
    try {
      let task = (await api.reports.createExport(batchId, { report_mode: apiMode, format: exportFormat })).data
      setExportTask(task)
      for (let attempt = 0; attempt < 50 && (task.status === 'queued' || task.status === 'running'); attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1200))
        task = (await api.reports.getExport(task.id)).data
        setExportTask(task)
      }
      if (task.status === 'failed') {
        throw new Error(`报告导出失败（${task.failure_code || 'EXPORT_GENERATION_FAILED'}）。`)
      }
      if (task.status !== 'succeeded') {
        throw new Error('报告仍在后台生成，请稍后在当前页面重新查看导出状态。')
      }
      notify(`${exportFormat === 'pdf' ? 'PDF' : 'Word'} 报告已生成，可以下载。`)
    } catch (cause) {
      setExportError(cause instanceof ApiClientError ? cause.message : cause instanceof Error ? cause.message : '报告导出失败，请重试。')
    } finally {
      setExporting(false)
    }
  }

  const selectedBatch = batches.find((batch) => batch.id === batchId)
  const distributionEntries = statistics ? Object.entries(statistics.distribution) : []
  const maxDistribution = Math.max(...distributionEntries.map(([, value]) => value), 1)
  const subjectLabel = selectedBatch ? subjectLabels[selectedBatch.subject as BatchFormState['subject']] || selectedBatch.subject : ''
  return <div className="content-wrap"><PageHeader eyebrow={selectedBatch ? `批改报告 · ${selectedBatch.class_name || selectedBatch.class_id}` : '批改报告'} title="班级成绩与薄弱项" description="正式口径只统计整份作业全部题目已教师复核的学生；AI 初评单独展示。" action={<div className="report-export-action"><label className="sr-only" htmlFor="report-export-format">导出格式</label><select id="report-export-format" value={exportFormat} onChange={(event) => setExportFormat(event.target.value as 'pdf' | 'docx')} disabled={exporting}><option value="pdf">PDF</option><option value="docx">Word</option></select><button className="button button-secondary" type="button" onClick={() => void startExport()} disabled={!batchId || loading || exporting}>{exporting ? '生成中…' : '导出报告'}<FileText size={17} aria-hidden="true" /></button></div>} />
    <div className="report-controls"><label className="field compact-field"><span>批次范围</span><select value={batchId} onChange={(event) => setBatchId(event.target.value)} disabled={loading || !batches.length}><option value="">{batches.length ? '请选择批次' : '暂无批次'}</option>{batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.title} · {batch.class_name || batch.class_id}</option>)}</select></label><div className="mode-toggle" role="group" aria-label="报告数据口径"><button type="button" className={mode === 'reviewed' ? 'active' : ''} aria-pressed={mode === 'reviewed'} onClick={() => setMode('reviewed')}>教师已复核</button><button type="button" className={mode === 'ai' ? 'active' : ''} aria-pressed={mode === 'ai'} onClick={() => setMode('ai')}>AI 初评参考</button></div><span className="report-note"><Info size={16} aria-hidden="true" />{mode === 'reviewed' ? '正式成绩口径' : '仅供教师参考，不进入正式成绩'}</span>{exportTask && <span className="report-export-status" role="status" aria-live="polite">{exportTask.status === 'queued' ? '已加入导出队列' : exportTask.status === 'running' ? '正在生成文件' : exportTask.status === 'succeeded' ? '文件已生成' : '导出失败'}{exportTask.status === 'succeeded' && <a className="text-button" href={api.reports.exportDownloadUrl(exportTask.id)}>下载{exportTask.format === 'pdf' ? ' PDF' : ' Word'}</a>}</span>}</div>
    {error && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{error}</span><button className="text-button" type="button" onClick={() => setReloadToken((value) => value + 1)}>重试</button></div>}
    {exportError && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{exportError}</span><button className="text-button" type="button" onClick={() => void startExport()} disabled={!batchId || exporting}>重试导出</button></div>}
    {loading && <div className="panel empty-detail report-empty"><Timer size={32} aria-hidden="true" /><h2>正在读取真实报告</h2><p>报告不会用静态成绩替代后端统计。</p></div>}
    {!loading && !report && <div className="panel empty-detail report-empty"><ChartBar size={32} aria-hidden="true" /><h2>{batchId ? '当前没有可展示的报告数据' : '请选择批次'}</h2><p>完成批改并确认结果后，正式成绩才会进入报告。</p></div>}
    {!loading && report && statistics && <><section className="report-metrics"><ReportMetric label="参与批改" value={`${statistics.student_count} 份`} note={`${subjectLabel} · 当前口径`} /><ReportMetric label="平均分" value={statistics.average_score || '—'} note={`批次设定分值 ${report.total_score}（参考）`} /><ReportMetric label="待复核" value={`${statistics.pending_review_count} 份`} note="未计入正式成绩" /><ReportMetric label="最高分" value={statistics.highest_score || '—'} note={statistics.highest_student_name || '暂无'} /></section><div className="report-grid"><section className="panel chart-panel"><div className="panel-heading"><div><h2>成绩分布</h2><p>范围：{report.batch_title} · {mode === 'reviewed' ? '教师已复核结果' : 'AI 初评结果'}</p></div><ChartBar size={23} className="heading-icon" aria-hidden="true" /></div><div className="chart-summary" aria-label="成绩分布摘要">{distributionEntries.length ? distributionEntries.map(([label, value]) => `${label} ${value} 人`).join('，') + '。' : '当前没有已计分学生。'}</div><div className="bar-chart">{distributionEntries.map(([label, value]) => <div className="bar-row" key={label}><span className="bar-label">{label}</span><div className="bar-track"><span style={{ width: `${(value / maxDistribution) * 100}%` }} /></div><strong>{value} 人</strong></div>)}</div><div className="chart-legend"><span><i className="legend-dot blue" />{mode === 'reviewed' ? '已复核成绩' : 'AI 初评参考'}</span><span><i className="legend-dot gray" />未复核不计入正式成绩</span></div></section><section className="panel insight-panel"><div className="panel-heading"><div><h2>讲评线索</h2><p>来自当前报告中已记录的扣分原因</p></div><MagicWand size={23} className="heading-icon" aria-hidden="true" /></div>{statistics.frequent_errors.length ? statistics.frequent_errors.slice(0, 3).map((entry, index) => <div className="insight-item" key={entry.label}><div className={`insight-number ${index === 0 ? 'orange' : index === 1 ? 'blue' : 'purple'}`}>{String(index + 1).padStart(2, '0')}</div><div><strong>{entry.label}</strong><span>出现 {entry.count} 次</span></div><ArrowRight size={17} aria-hidden="true" /></div>) : <div className="empty-inline">当前没有已记录的扣分原因。</div>}</section></div><section className="panel report-table-panel"><div className="panel-heading"><div><h2>个人成绩单</h2><p>统计口径与当前筛选保持一致，未复核结果会显著标识；点击详情读取服务端个人成绩。</p></div></div><div className="score-table" role="table" aria-label="个人成绩单"><div className="score-table-head" role="row"><span>学生</span><span>客观题</span><span>主观题</span><span>总分</span><span>状态</span><span>详情</span></div>{report.students.map((student) => <div className="score-table-row" role="row" key={student.assignment_group_id}><strong>{student.student_name || student.student_code || '未归属学生'}</strong><span>{student.objective_score ?? '—'} / {student.objective_max_score ?? '—'}</span><span>{student.subjective_score ?? '—'} / {student.subjective_max_score ?? '—'}</span><strong className={`score-highlight ${student.total_score ? '' : 'muted-score'}`}>{student.total_score || '—'}</strong><StatusPill tone={student.status === 'reviewed' ? 'success' : 'warning'}>{student.status === 'reviewed' ? '教师已复核' : '部分待复核'}</StatusPill><button className="text-button" type="button" onClick={() => void openStudentDetail(student)} disabled={studentDetailLoading || !student.student_id}>{studentDetailLoading ? '读取中…' : '详情'}</button></div>)}{!report.students.length && <div className="empty-inline">当前口径没有符合条件的学生成绩。</div>}</div></section><div className="footer-note"><Info size={16} aria-hidden="true" /><span>通用导出字段包括批次元数据、班级统计、成绩分布、个人成绩、逐题分数与教师评语；生成过程会冻结本次报告快照。</span></div></>}
    {studentDetailError && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{studentDetailError}</span><button className="text-button" type="button" onClick={() => setStudentDetailError('')}>关闭</button></div>}
    {studentDetail && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !studentDetailLoading) setStudentDetail(null) }}><section ref={studentModalRef} className="modal student-report-modal" role="dialog" aria-modal="true" aria-labelledby="student-report-title"><div className="modal-header"><div><span className="eyebrow">个人成绩详情 · {studentDetail.batch.batch_title}</span><h2 id="student-report-title">{studentDetail.student.student_name || studentDetail.student.student_code || '未归属学生'}</h2><p>{apiMode === 'reviewed' ? '教师已复核口径' : 'AI 初评参考口径'} · 原图、逐题评分与评语均来自服务端结果。</p></div><button className="icon-button" type="button" aria-label="关闭个人成绩详情" onClick={() => setStudentDetail(null)} disabled={studentDetailLoading}><X size={20} /></button></div><div className="student-report-summary student-report-score-rail"><div className="student-report-score-hero"><span>当前口径总分</span><strong>{studentDetail.student.total_score ?? '—'}<small> / {studentDetail.student.total_max_score}</small></strong><em>{apiMode === 'reviewed' ? '正式成绩' : '仅供教师参考'}</em></div><div className="student-report-score-grid"><InfoRow label="AI评分" value={`${studentDetail.student.ai_total_score ?? '—'} / ${studentDetail.student.ai_total_max_score}`} /><InfoRow label="教师确认分" value={`${studentDetail.student.teacher_total_score ?? '—'} / ${studentDetail.student.teacher_total_max_score}`} /><InfoRow label="客观题" value={`${studentDetail.student.objective_score ?? '—'} / ${studentDetail.student.objective_max_score ?? '—'}`} /><InfoRow label="主观题" value={`${studentDetail.student.subjective_score ?? '—'} / ${studentDetail.student.subjective_max_score ?? '—'}`} /><InfoRow label="状态" value={studentDetail.student.status === 'reviewed' ? '教师已复核' : '部分待复核'} /><InfoRow label="缺失题目" value={studentDetail.student.missing_questions.length ? studentDetail.student.missing_questions.join('、') : '无'} /></div></div><div className="student-report-detail-body"><section className="student-report-section student-report-evidence"><div className="student-report-section-heading"><div><span className="eyebrow">原图证据 · {studentDetail.student.pages.length} 页</span><h3>学生作业图片</h3><p>点击图片可放大、缩小和还原；PDF 按原件内嵌预览。</p></div><Eye size={22} aria-hidden="true" /></div>{studentDetail.student.pages.length ? <div className="student-report-page-grid">{studentDetail.student.pages.map((page) => { const pageUrl = api.pages.originalUrl(page.id); const pageLabel = page.page_sequence ? `第 ${page.page_sequence} 页` : `原文件第 ${page.page_index} 页`; return <article className="student-report-page-card" key={page.id}><div className="student-report-page-preview">{page.is_pdf ? <iframe title={`${page.original_name} · ${pageLabel}`} src={pageUrl} /> : <ZoomableImage src={pageUrl} alt={`${page.original_name} · ${pageLabel}`} onOpen={() => setReportPreviewImage({ src: pageUrl, alt: `${page.original_name} · ${pageLabel}`, title: `学生作业原图 · ${pageLabel}` })} />}</div><div className="student-report-page-caption"><strong>{pageLabel}</strong><span>{page.original_name}</span></div></article> })}</div> : <div className="empty-inline">当前学生没有可追溯的学生作业原图。</div>}</section><section className="student-report-section student-report-evaluations"><div className="student-report-section-heading"><div><span className="eyebrow">AI 评分与教师反馈</span><h3>评分和评价</h3><p>按题目展示 AI 建议分、AI评价和教师评语，便于逐题核对。</p></div><Sparkle size={22} aria-hidden="true" /></div><div className="student-report-ai-note"><strong>AI 已生成 {studentDetail.student.ai_scored_question_count} / {studentDetail.student.questions.length} 题评分</strong><span>{studentDetail.student.ai_total_score ? `AI总分 ${studentDetail.student.ai_total_score} / ${studentDetail.student.ai_total_max_score}` : '当前没有可用的 AI 评分'}</span></div>{studentDetail.student.questions.length ? <div className="student-report-question-list">{studentDetail.student.questions.map((question) => <article className="student-report-question-card" key={question.id}><div className="student-report-question-head"><strong>第 {question.question_no} 题</strong><StatusPill tone={question.result_status === 'reviewed' ? 'success' : question.ai_score ? 'info' : 'warning'}>{question.result_status === 'reviewed' ? '教师已复核' : question.ai_score ? 'AI已评分' : '待处理'}</StatusPill></div><div className="student-report-question-scores"><div><span>AI评分</span><strong>{question.ai_score ?? '—'} <small>/ {question.max_score}</small></strong></div><div><span>教师确认分</span><strong>{question.teacher_score ?? '—'} <small>/ {question.max_score}</small></strong></div></div><div className="student-report-comment student-report-comment-ai"><span>AI评价</span><p>{question.ai_comment || '本题暂无 AI 评价。'}</p></div><div className="student-report-comment student-report-comment-teacher"><span>教师评语（培育建议）</span><p>{question.teacher_comment || '教师尚未填写评语。'}</p></div></article>)}</div> : <div className="empty-inline">当前没有逐题评分结果。</div>}</section></div><div className="modal-actions"><SecondaryButton onClick={() => { setStudentDetail(null); navigate('grading') }}>进入复核页</SecondaryButton><PrimaryButton icon={X} onClick={() => setStudentDetail(null)}>关闭</PrimaryButton></div></section></div>}
    {reportPreviewImage && <ImageLightbox target={reportPreviewImage} onClose={() => setReportPreviewImage(null)} />}
  </div>
}

function ReportMetric({ label, value, note }: { label: string; value: string; note: string }) { return <div className="report-metric"><span>{label}</span><strong>{value}</strong><small>{note}</small></div> }

type SettingsFormState = {
  teacherName: string
  providerName: string
  modelName: string
  baseUrl: string
  apiKey: string
  storageRoot: string
  retentionDays: string
  queueLimit: string
  workerConcurrency: string
}

const defaultSettingsForm: SettingsFormState = {
  teacherName: '林老师',
  providerName: 'DeepSeek',
  modelName: 'deepseek-flash',
  baseUrl: '',
  apiKey: '',
  storageRoot: './backend/data/uploads',
  retentionDays: '',
  queueLimit: '20',
  workerConcurrency: '4',
}

function Settings({ apiKeyVisible, setApiKeyVisible, notify }: { apiKeyVisible: boolean; setApiKeyVisible: (value: boolean) => void; notify: (message: string) => void }) {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [form, setForm] = useState<SettingsFormState>(defaultSettingsForm)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState('')

  const applySettings = (value: AppSettings) => {
    setSettings(value)
    setForm({
      teacherName: value.teacher_name,
      providerName: value.model_provider,
      modelName: value.model_name,
      baseUrl: value.model_base_url ?? '',
      apiKey: '',
      storageRoot: value.storage_root,
      retentionDays: value.retention_days === null ? '' : String(value.retention_days),
      queueLimit: String(value.queue_limit),
      workerConcurrency: String(value.worker_concurrency),
    })
  }

  useEffect(() => {
    let active = true
    api.settings.get().then((response) => {
      if (active) applySettings(response.data)
    }).catch(() => {
      // 保留原型可浏览性，但明确标记为演示数据，不把失败伪装成后端成功。
      if (active) setError('后端设置接口暂未连接，当前显示本地演示配置；保存和连接测试不会伪造成功。')
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [])

  const updateField = (key: keyof SettingsFormState, value: string) => {
    setForm((current) => ({ ...current, [key]: value }))
    if (error) setError('')
  }

  const getErrorMessage = (cause: unknown) => {
    if (cause instanceof ApiClientError) return cause.message
    return '后端设置接口不可用，请确认 API 服务已启动后重试。'
  }

  const handleSave = async (event: FormEvent) => {
    event.preventDefault()
    if (!form.providerName.trim() || !form.modelName.trim()) {
      setError('请填写服务商名称和模型名称。')
      return
    }
    setSaving(true)
    setError('')
    try {
      const modelResponse = await api.settings.updateModel({
        provider_name: form.providerName.trim(),
        model_name: form.modelName.trim(),
        model_base_url: form.baseUrl.trim() || null,
        ...(form.apiKey.trim() ? { api_key: form.apiKey.trim() } : {}),
        clear_api_key: false,
      })
      await api.settings.updateProfile({ teacher_name: form.teacherName.trim() || '未命名教师' })
      const storageResponse = await api.settings.updateStorage({
        storage_root: form.storageRoot.trim(),
        retention_days: form.retentionDays.trim() ? Number(form.retentionDays) : null,
        queue_limit: Number(form.queueLimit) || 20,
        worker_concurrency: Number(form.workerConcurrency) || 4,
      })
      applySettings(storageResponse.data)
      // modelResponse 用于确保模型接口保存请求成功，避免未来改为部分响应时静默忽略。
      if (!modelResponse.data) throw new Error('模型配置响应为空')
      notify(form.apiKey.trim() ? '设置已保存，API Key 已交给当前服务使用' : '设置已保存；已保留当前服务中的 API Key')
    } catch (cause) {
      setError(getErrorMessage(cause))
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setError('')
    try {
      // 测试当前表单，而不是要求用户先重复点击“保存配置”。
      await api.settings.updateModel({
        provider_name: form.providerName.trim(),
        model_name: form.modelName.trim(),
        model_base_url: form.baseUrl.trim() || null,
        ...(form.apiKey.trim() ? { api_key: form.apiKey.trim() } : {}),
      })
      const response = await api.settings.testModel()
      notify(`连接测试通过：${response.data.provider_name} / ${response.data.model_name}`)
    } catch (cause) {
      setError(getErrorMessage(cause))
    } finally {
      setTesting(false)
    }
  }

  const configured = Boolean(settings?.api_key_configured && settings.model_base_url)
  return <div className="content-wrap">
    <PageHeader eyebrow="设置 · 单教师配置" title="模型与运行设置" description="可配置 DeepSeek、OpenAI 或其他兼容 /chat/completions 的模型服务；API Key 在此输入后交给当前服务使用。" action={<SecondaryButton icon={ShieldCheck} onClick={() => notify('API Key 只在当前后端进程内暂存，不写入数据库或浏览器存储')}>隐私说明</SecondaryButton>} />
    {error && <div className="demo-notice" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{error}</span></div>}
    <div className="settings-grid">
      <section className="panel settings-main">
        <div className="panel-heading"><div><h2>模型 API 配置</h2><p>填写服务商、模型、Base URL 和 API Key，保存后即可进行真实连接测试与模型批改。</p></div><StatusPill tone={configured ? 'success' : 'warning'}>{configured ? '已配置' : '待配置'}</StatusPill></div>
        <form className="settings-form" onSubmit={handleSave}>
          <label className="field"><span>教师姓名 <em>*</em></span><input value={form.teacherName} onChange={(event) => updateField('teacherName', event.target.value)} autoComplete="name" /></label>
          <div className="form-grid">
            <label className="field"><span>服务商名称 <em>*</em></span><input value={form.providerName} onChange={(event) => updateField('providerName', event.target.value)} placeholder="例如：DeepSeek、OpenAI 或自建服务" /></label>
            <label className="field"><span>模型名称 <em>*</em></span><input value={form.modelName} onChange={(event) => updateField('modelName', event.target.value)} placeholder="例如：deepseek-flash" /></label>
          </div>
          <label className="field"><span>OpenAI 兼容 Base URL</span><input type="url" value={form.baseUrl} onChange={(event) => updateField('baseUrl', event.target.value)} placeholder="例如：https://api.example.com/v1" /><small>仅保存地址；实际连接测试会请求该地址的 /models。</small></label>
          <label className="field"><span>模型 API Key <em>*</em></span><div className="input-with-action"><input type={apiKeyVisible ? 'text' : 'password'} value={form.apiKey} onChange={(event) => updateField('apiKey', event.target.value)} autoComplete="off" placeholder={settings?.api_key_configured ? '当前服务已配置；留空可保留现有 Key' : '粘贴你的 DeepSeek / OpenAI 兼容 API Key'} aria-describedby="model-api-key-help" /><button type="button" aria-label={apiKeyVisible ? '隐藏模型 API Key' : '显示模型 API Key'} onClick={() => setApiKeyVisible(!apiKeyVisible)}>{apiKeyVisible ? <Eye size={18} /> : <Eye size={18} weight="duotone" />}</button></div><small id="model-api-key-help">Key 只通过本次请求交给当前后端进程使用，不写入数据库；后端重启后需要重新输入。不要把 Key 发到聊天或截图中。</small></label>
          <div className="settings-actions"><SecondaryButton onClick={() => setForm(defaultSettingsForm)} disabled={saving || testing}>恢复默认示例</SecondaryButton><PrimaryButton type="submit" icon={Check} disabled={loading || saving || testing}>{saving ? '保存中…' : '保存配置'}</PrimaryButton><SecondaryButton icon={ArrowsClockwise} onClick={handleTest} disabled={loading || saving || testing}>{testing ? '测试中…' : '测试真实连接'}</SecondaryButton></div>
        </form>
      </section>
      <section className="panel settings-side">
        <div className="panel-heading"><div><h2>文件与数据</h2><p>原图、PDF、OCR 结果以文件形式持久化。</p></div><Database size={23} className="heading-icon" aria-hidden="true" /></div>
        <label className="field"><span>文件保存位置</span><input value={form.storageRoot} onChange={(event) => updateField('storageRoot', event.target.value)} /><small>结构化业务数据使用 SQLite 保存；修改后需按部署配置生效。</small></label>
          <label className="field"><span>数据保留期限</span><select value={form.retentionDays} onChange={(event) => updateField('retentionDays', event.target.value)}><option value="">本地保存，手动清理</option><option value="30">30 天后提醒清理</option><option value="90">90 天后提醒清理</option></select></label>
          <div className="form-grid"><label className="field"><span>待处理队列上限 <em>*</em></span><input type="number" min="1" max="10000" step="1" value={form.queueLimit} onChange={(event) => updateField('queueLimit', event.target.value)} disabled={saving || loading} /><small>超出上限的任务应进入后端可恢复的排队或拒绝流程。</small></label><label className="field"><span>后台并发数 <em>*</em></span><input type="number" min="1" max="64" step="1" value={form.workerConcurrency} onChange={(event) => updateField('workerConcurrency', event.target.value)} disabled={saving || loading} /><small>首版建议值为 4，最终以 OCR、模型和 SQLite 实测校准。</small></label></div>
        <div className="privacy-callout"><ShieldCheck size={20} weight="duotone" aria-hidden="true" /><div><strong>外部 API 边界</strong><p>姓名、学号等敏感信息仍需脱敏；供应商费用、限流和作业数据处理协议需单独确认。</p></div></div>
        <div className="boundary-grid"><InfoRow label="待处理队列" value={`${form.queueLimit} 个起步`} /><InfoRow label="后台并发" value={`${form.workerConcurrency} 个实例`} /><InfoRow label="正式成绩" value="教师复核后生效" /></div>
      </section>
    </div>
    <div className="footer-note"><Info size={16} aria-hidden="true" /><span>连接测试失败会显示真实失败原因；未连接后端时不会显示模拟成功。</span></div>
  </div>
}

function BatchPurgeModal({ batch, error, submitting, onClose, onConfirm }: { batch: Batch; error: string; submitting: boolean; onClose: () => void; onConfirm: (confirmName: string) => void }) {
  const modalRef = useModalAccessibility(true, submitting, onClose)
  const [confirmName, setConfirmName] = useState('')
  const inputId = 'batch-purge-confirm-name'
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !submitting) onClose() }}><section ref={modalRef} className="modal batch-purge-modal" role="dialog" aria-modal="true" aria-labelledby="batch-purge-modal-title"><div className="modal-header"><div><span className="eyebrow">危险操作 · 批次清理</span><h2 id="batch-purge-modal-title">撤销 OCR 并删除批次</h2><p>这会删除该批次的原始文件、页面记录、OCR 结果、任务记录、学生归属和存储缓存，且无法恢复。</p></div><button className="icon-button" type="button" aria-label="关闭批次删除确认" onClick={onClose} disabled={submitting}><X size={20} /></button></div><form className="modal-form" onSubmit={(event) => { event.preventDefault(); onConfirm(confirmName.trim()) }}>{error && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{error}</span></div>}<div className="batch-purge-warning"><WarningCircle size={22} weight="fill" aria-hidden="true" /><div><strong>请确认这是误上传的批次</strong><span>未保存答案、成绩、教师复核或报告的学生归属会一并移除；存在这些后续记录，或有任务正在运行时，系统会拒绝删除。</span></div></div><label className="field" htmlFor={inputId}><span>输入批次名称确认 <em>*</em></span><input id={inputId} value={confirmName} onChange={(event) => setConfirmName(event.target.value)} placeholder={batch.title} autoComplete="off" disabled={submitting} aria-describedby={`${inputId}-hint`} /><small id={`${inputId}-hint`}>请完整输入：{batch.title}</small></label><div className="modal-actions"><SecondaryButton onClick={onClose} disabled={submitting}>取消</SecondaryButton><button className="button batch-purge-confirm" type="submit" disabled={submitting || confirmName.trim() !== batch.title}>{submitting ? '删除中…' : '确认撤销并删除'}<TrashSimple size={17} aria-hidden="true" /></button></div></form></section></div>
}

function ReviewedBatchDeleteModal({ batch, error, submitting, onClose, onConfirm }: { batch: Batch; error: string; submitting: boolean; onClose: () => void; onConfirm: (password: string, confirmName: string) => void }) {
  const modalRef = useModalAccessibility(true, submitting, onClose)
  const [step, setStep] = useState<1 | 2>(1)
  const [password, setPassword] = useState('')
  const [passwordVisible, setPasswordVisible] = useState(false)
  const [confirmName, setConfirmName] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [localError, setLocalError] = useState('')
  const passwordId = 'reviewed-batch-delete-password'
  const nameId = 'reviewed-batch-delete-confirm-name'
  const displayError = error || localError

  const continueToConfirmation = () => {
    if (!password.trim()) {
      setLocalError('请输入当前教师账号密码。')
      return
    }
    setLocalError('')
    setStep(2)
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !submitting) onClose() }}><section ref={modalRef} className="modal batch-purge-modal reviewed-batch-delete-modal" role="dialog" aria-modal="true" aria-labelledby="reviewed-batch-delete-title"><div className="modal-header"><div><span className="eyebrow">危险操作 · 永久删除</span><h2 id="reviewed-batch-delete-title">物理删除已复核批次</h2><p>删除后将移除该批次的作业原图、OCR、学生答案、AI评分、教师评语、复核记录和导出文件，无法恢复。</p></div><button className="icon-button" type="button" aria-label="关闭物理删除确认" onClick={onClose} disabled={submitting}><X size={20} /></button></div><form className="modal-form" onSubmit={(event) => { event.preventDefault(); if (step === 1) continueToConfirmation(); else onConfirm(password, confirmName.trim()) }}>{displayError && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{displayError}</span></div>}{step === 1 ? <><div className="batch-purge-warning"><WarningCircle size={22} weight="fill" aria-hidden="true" /><div><strong>第一步：验证当前教师账号</strong><span>密码只用于本次服务端校验，不会保存到浏览器、数据库或日志。允许使用密码管理器和粘贴。</span></div></div><label className="field" htmlFor={passwordId}><span>当前账号密码 <em>*</em></span><div className="input-with-action"><input id={passwordId} type={passwordVisible ? 'text' : 'password'} value={password} onChange={(event) => { setPassword(event.target.value); setLocalError('') }} autoComplete="current-password" placeholder="请输入登录批小智的密码" disabled={submitting} aria-describedby={`${passwordId}-hint`} /><button type="button" aria-label={passwordVisible ? '隐藏当前账号密码' : '显示当前账号密码'} onClick={() => setPasswordVisible((value) => !value)} disabled={submitting}>{passwordVisible ? <Eye size={18} /> : <Eye size={18} weight="duotone" />}</button></div><small id={`${passwordId}-hint`}>需要验证当前登录教师账号的密码。</small></label></> : <><div className="batch-purge-warning"><WarningCircle size={22} weight="fill" aria-hidden="true" /><div><strong>第二步：确认永久删除</strong><span>密码验证已完成。请再次核对批次名称；确认后，后端会在一个事务中清理关联数据，再删除对应文件。</span></div></div><label className="field" htmlFor={nameId}><span>输入批次名称确认 <em>*</em></span><input id={nameId} value={confirmName} onChange={(event) => { setConfirmName(event.target.value); setLocalError('') }} placeholder={batch.title} autoComplete="off" disabled={submitting} aria-describedby={`${nameId}-hint`} /><small id={`${nameId}-hint`}>请完整输入：{batch.title}</small></label><label className="danger-confirm-check"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} disabled={submitting} /><span>我已了解：该操作不可恢复，后续将不能查看该批次的成绩和复核记录。</span></label></>}{<div className="modal-actions"><SecondaryButton onClick={onClose} disabled={submitting}>取消</SecondaryButton>{step === 2 && <button className="button button-secondary" type="button" onClick={() => { setStep(1); setLocalError(''); setConfirmName('') }} disabled={submitting}>返回上一步</button>}<button className="button batch-purge-confirm" type="submit" disabled={submitting || (step === 1 ? !password.trim() : confirmName.trim() !== batch.title || !acknowledged)}>{submitting ? '删除中…' : step === 1 ? '验证密码并继续' : '确认物理删除'}<TrashSimple size={17} aria-hidden="true" /></button></div>}</form></section></div>
}

function BatchModal({ editing, form, setForm, errors, submitting, submittingMessage, onClose, onSubmit }: { editing: boolean; form: BatchFormState; setForm: React.Dispatch<React.SetStateAction<BatchFormState>>; errors: Record<string, string>; submitting: boolean; submittingMessage: string; onClose: () => void; onSubmit: (event: FormEvent, saveMode: 'draft' | 'upload', questionPaperFile: File | null) => void }) {
  const modalRef = useModalAccessibility(true, submitting, onClose)
  const [classes, setClasses] = useState<ClassRoom[]>([])
  const [rubrics, setRubrics] = useState<Rubric[]>([])
  const [loadingOptions, setLoadingOptions] = useState(true)
  const [optionsError, setOptionsError] = useState('')
  const [questionPaperFile, setQuestionPaperFile] = useState<File | null>(null)

  useEffect(() => {
    let cancelled = false
    const loadOptions = async () => {
      setLoadingOptions(true)
      try {
        const [classResponse, rubricResponse] = await Promise.all([
          api.classes.list('active'),
          api.rubrics.list({ question_type: 'subjective', status: 'active' }),
        ])
        if (cancelled) return
        setClasses(classResponse.data)
        const matchingRubrics = rubricResponse.data.filter((rubric) => rubric.subject === form.subject)
        setRubrics(matchingRubrics)
        if (!matchingRubrics.length && rubricResponse.data.length) {
          const availableSubjects = Array.from(new Set(rubricResponse.data.map((rubric) => subjectLabels[rubric.subject as BatchFormState['subject']] || rubric.subject)))
          setOptionsError(`当前批次学科为${subjectLabels[form.subject]}，已有评分标准属于${availableSubjects.join('、')}，请先切换批次学科。`)
        } else if (matchingRubrics.length && !matchingRubrics.some((rubric) => rubric.current_version?.id)) {
          setOptionsError('当前学科已有评分标准，但版本信息未加载完整；保存后的评分标准无需发布，请刷新后重试。')
        } else {
          setOptionsError('')
        }
        if (!form.classId && classResponse.data[0]) setForm((current) => ({ ...current, classId: classResponse.data[0].id }))
      } catch (cause) {
        if (!cancelled) setOptionsError(cause instanceof ApiClientError ? cause.message : '无法加载班级和评分标准，请检查后端服务。')
      } finally {
        if (!cancelled) setLoadingOptions(false)
      }
    }
    void loadOptions()
    return () => { cancelled = true }
  }, [form.subject])

  const updateQuestion = (index: number, changes: Partial<BatchQuestionInput>) => {
    setForm((current) => ({ ...current, questions: current.questions.map((item, itemIndex) => itemIndex === index ? { ...item, ...changes } : item) }))
  }

  const addQuestion = () => {
    setForm((current) => ({ ...current, questions: [...current.questions, { question_no: String(current.questions.length + 1), question_type: 'objective', max_score: '1', question_prompt: null, reference_answer: '', rubric_version_id: null }] }))
  }

  const removeQuestion = (index: number) => {
    if (form.questions.length <= 1) return
    setForm((current) => ({ ...current, questions: current.questions.filter((_, itemIndex) => itemIndex !== index) }))
  }

  const chooseQuestionPaper = (file: File | undefined) => {
    if (!file) return
    const isSupported = ['image/jpeg', 'image/png', 'application/pdf'].includes(file.type) || /\.(jpe?g|png|pdf)$/i.test(file.name)
    if (!isSupported) {
      setOptionsError('题干/答案文件仅支持 JPG、JPEG、PNG 或 PDF。')
      setQuestionPaperFile(null)
      return
    }
    if (file.size > 50 * 1024 * 1024) {
      setOptionsError('题干/答案文件不能超过 50MB，请压缩后重试。')
      setQuestionPaperFile(null)
      return
    }
    setOptionsError('')
    setQuestionPaperFile(file)
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !submitting) onClose() }}><section ref={modalRef} className="modal batch-modal" role="dialog" aria-modal="true" aria-labelledby="batch-modal-title"><div className="modal-header"><div><span className="eyebrow">作业管理 · {editing ? '编辑批次' : '新建批次'}</span><h2 id="batch-modal-title">{editing ? '编辑作业批次' : '创建作业批次'}</h2><p>{editing ? '保存后生成新的批次配置版本，原文件、页面和学生关联保持不变。' : '先完成最少必要配置，再进入批量上传和识别流程。'}</p></div><button className="icon-button" type="button" aria-label="关闭批次编辑" onClick={onClose} disabled={submitting}><X size={20} /></button></div><div className="stepper"><span className="current"><b>1</b>批次配置</span><i /><span><b>2</b>批量上传</span><i /><span><b>3</b>识别与批改</span></div><form className="modal-form" onSubmit={(event) => { const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null; void onSubmit(event, submitter?.value === 'upload' ? 'upload' : 'draft', questionPaperFile) }}>{errors.form && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{errors.form}</span></div>}{optionsError && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-live="polite" /><span>{optionsError}</span></div>}<div className="form-grid"><label className="field"><span>批次名称 <em>*</em></span><input value={form.title} onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))} placeholder="例如：八年级英语单元测试" aria-invalid={Boolean(errors.title)} aria-describedby={errors.title ? 'batch-title-error' : undefined} />{errors.title && <small id="batch-title-error" className="field-error">{errors.title}</small>}</label><label className="field"><span>班级 <em>*</em></span><select value={form.classId} onChange={(event) => setForm((current) => ({ ...current, classId: event.target.value }))} disabled={loadingOptions || submitting} aria-invalid={Boolean(errors.classId)} aria-describedby={errors.classId ? 'batch-class-error' : undefined}><option value="">{loadingOptions ? '正在加载班级…' : classes.length ? '请选择班级' : '暂无启用班级'}</option>{classes.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.student_count ?? 0} 名学生</option>)}</select>{errors.classId && <small id="batch-class-error" className="field-error">{errors.classId}</small>}</label><label className="field"><span>学科 <em>*</em></span><select value={form.subject} onChange={(event) => setForm((current) => ({ ...current, subject: event.target.value as BatchFormState['subject'], questions: current.questions.map((item) => ({ ...item, rubric_version_id: item.question_type === 'subjective' ? null : item.rubric_version_id })) }))} disabled={submitting}><option value="chinese">语文</option><option value="math">数学</option><option value="english">英语</option></select></label><label className="field"><span>批次总分 <em>*</em></span><input type="number" min="0.01" step="0.01" value={form.totalScore} onChange={(event) => setForm((current) => ({ ...current, totalScore: event.target.value }))} aria-invalid={Boolean(errors.totalScore)} aria-describedby={errors.totalScore ? 'score-error' : undefined} disabled={submitting} />{errors.totalScore && <small id="score-error" className="field-error">{errors.totalScore}</small>}</label></div>{!editing && <section className="question-paper-upload" aria-labelledby="batch-question-paper-title"><div className="section-label" id="batch-question-paper-title">题目与参考答案图片（可选）</div><p className="batch-form-note">可以上传一次题目与参考答案图片，由系统 OCR 并按题号自动归属；也可以完全不上传图片，直接在下方录入题目文本和批次共享参考答案。</p><label className="file-dropzone compact" htmlFor="batch-question-paper-file"><CloudArrowUp size={24} weight="duotone" aria-hidden="true" /><strong>{questionPaperFile ? questionPaperFile.name : '选择题目与参考答案图片或 PDF'}</strong><span>支持 JPG、PNG、PDF，单个文件不超过 50MB</span><input id="batch-question-paper-file" type="file" accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf" onChange={(event) => chooseQuestionPaper(event.target.files?.[0])} disabled={submitting} /></label>{questionPaperFile && <button className="text-button question-paper-clear-file" type="button" onClick={() => { setQuestionPaperFile(null); const input = document.getElementById('batch-question-paper-file') as HTMLInputElement | null; if (input) input.value = '' }} disabled={submitting}>清除当前题目与答案文件</button>}</section>}<div className="question-config-heading"><div><strong>按题号录入题目、批次共享参考答案与评分标准</strong><span>题目文本可以直接录入，也可以由图片 OCR 生成后再核对；当前共 {form.questions.length} 道题。</span></div><button className="text-button" type="button" onClick={addQuestion} disabled={submitting}><Plus size={15} aria-hidden="true" />添加题目</button></div><div className="question-config-list">{form.questions.map((item, index) => <div className="question-config-card" key={`${index}-${item.question_no}`}><div className="question-config-title"><strong>第 {index + 1} 题</strong>{form.questions.length > 1 && <button className="icon-button" type="button" aria-label={`删除第 ${index + 1} 题`} onClick={() => removeQuestion(index)} disabled={submitting}><X size={16} /></button>}</div><div className="form-grid"><label className="field"><span>题号 <em>*</em></span><input value={item.question_no} onChange={(event) => updateQuestion(index, { question_no: event.target.value })} aria-invalid={Boolean(errors[`question_${index}`])} disabled={submitting} /></label><label className="field"><span>题型 <em>*</em></span><select value={item.question_type} onChange={(event) => updateQuestion(index, { question_type: event.target.value as 'objective' | 'subjective', rubric_version_id: event.target.value === 'subjective' ? null : null })} disabled={submitting}><option value="objective">客观题</option><option value="subjective">主观题</option></select></label><label className="field"><span>单题分值 <em>*</em></span><input type="number" min="0.01" step="0.01" value={String(item.max_score)} onChange={(event) => updateQuestion(index, { max_score: event.target.value })} aria-invalid={Boolean(errors[`question_${index}`])} disabled={submitting} />{errors[`question_${index}`] && <small className="field-error">{errors[`question_${index}`]}</small>}</label>{item.question_type === 'subjective' && <label className="field"><span>主观题评分标准</span><select value={item.rubric_version_id ?? ''} onChange={(event) => updateQuestion(index, { rubric_version_id: event.target.value || null })} disabled={submitting}><option value="">待配置（保存草稿，暂不能批改）</option>{rubrics.filter((rubric) => rubric.current_version?.id).map((rubric) => <option key={rubric.id} value={rubric.current_version?.id}>{rubric.name} · v{rubric.current_version?.version_no}</option>)}</select></label>}</div><label className="field"><span>题目文本{!questionPaperFile && <em> *</em>}</span><textarea value={item.question_prompt ?? ''} onChange={(event) => updateQuestion(index, { question_prompt: event.target.value })} placeholder="直接录入本题题干；如果上传了图片，这里会在 OCR 后回填并供你核对" disabled={submitting} /><small>不上传图片时，这里就是题干来源；上传图片时，OCR 结果仍可在题干 OCR 页面继续校对。</small></label><label className="field"><span>参考答案（本批次共用）{item.question_type === 'objective' && <em> *</em>}</span><textarea value={item.reference_answer ?? ''} onChange={(event) => updateQuestion(index, { reference_answer: event.target.value })} placeholder={item.question_type === 'objective' ? '系统识别后核对客观题参考答案；多个等价答案请按确定规则填写' : '系统识别后核对主观题参考答案；可补录评分要点'} disabled={submitting} /></label></div>)}</div><small className="batch-form-note">题目与参考答案图片只上传一次；没有图片时直接保存这里的文本；后续学生作业只上传学生照片，自动复用本批次共享参考答案。</small><div className="modal-actions"><SecondaryButton onClick={onClose} disabled={submitting}>取消</SecondaryButton><button className="button button-secondary" type="submit" name="save_mode" value="draft" disabled={submitting}>{submitting ? submittingMessage : editing ? '保存批次草稿' : '仅保存草稿'}</button><button className="button button-primary" type="submit" name="save_mode" value="upload" disabled={submitting || loadingOptions || classes.length === 0}>{submitting ? submittingMessage : editing ? '保存并返回作业管理' : questionPaperFile ? isQuestionPaperImage(questionPaperFile) ? '保存并识别题目' : '保存并进入题干 OCR' : '保存并进入学生作业上传'}<ArrowRight size={17} weight="bold" aria-hidden="true" /></button></div></form></section></div>
}

function RubricModal({ form, setForm, submitting, error, onClose, onSubmit }: { form: { name: string; subject: string; score: string; points: string; examples: string }; setForm: React.Dispatch<React.SetStateAction<{ name: string; subject: string; score: string; points: string; examples: string }>>; submitting: boolean; error: string; onClose: () => void; onSubmit: (event: FormEvent) => void }) {
  const modalRef = useModalAccessibility(true, submitting, onClose)
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !submitting) onClose() }}><section ref={modalRef} className="modal" role="dialog" aria-modal="true" aria-labelledby="rubric-modal-title"><div className="modal-header"><div><span className="eyebrow">评分标准知识库</span><h2 id="rubric-modal-title">新建评分标准</h2><p>保存后立即可用于新建批次和主观题批改，并生成可追溯版本。</p></div><button className="icon-button" type="button" aria-label="关闭评分标准" onClick={onClose} disabled={submitting}><X size={20} /></button></div><form className="modal-form" onSubmit={onSubmit}>{error && <div className="workflow-message error" role="alert"><WarningCircle size={18} weight="fill" aria-hidden="true" /><span>{error}</span></div>}<label className="field"><span>标准名称 <em>*</em></span><input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="例如：主观题综合评分标准" disabled={submitting} /></label><div className="form-grid"><label className="field"><span>学科 <em>*</em></span><select value={form.subject} onChange={(event) => setForm((current) => ({ ...current, subject: event.target.value }))} disabled={submitting}><option>语文</option><option>数学</option><option>英语</option></select></label><label className="field"><span>题目总分（仅用于批次参考） <em>*</em></span><input type="number" min="1" value={form.score} onChange={(event) => setForm((current) => ({ ...current, score: event.target.value }))} disabled={submitting} /><small>不会解析说明中的数字，也不会把它当作模型评分上限。</small></label></div><label className="field"><span>给大模型的自然语言评分说明 <em>*</em></span><textarea value={form.points} onChange={(event) => setForm((current) => ({ ...current, points: event.target.value }))} placeholder="自由写步骤、关键点、扣分规则、边界情况和阅卷提示" aria-describedby="rubric-points-help" disabled={submitting} /><small id="rubric-points-help">整段文字会原样作为评分依据交给大模型；不拆分、不计算、不用题目满分截断模型结果。</small></label><label className="field"><span>示例作答</span><textarea value={form.examples} onChange={(event) => setForm((current) => ({ ...current, examples: event.target.value }))} placeholder="每行一个示例作答，可留空" disabled={submitting} /><small>示例会作为评分依据的一部分保存到当前版本。</small></label><div className="modal-actions"><SecondaryButton onClick={onClose} disabled={submitting}>取消</SecondaryButton><PrimaryButton type="submit" icon={Check} disabled={submitting}>{submitting ? '保存中…' : '保存评分标准'}</PrimaryButton></div></form></section></div>
}

export default App
