/**
 * 批小智正式 API 请求层。
 * 页面组件只通过这里访问后端，避免把会话 Cookie、统一错误格式和 API 前缀散落在各页面。
 */

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')

export type ApiMeta = { request_id: string; page?: number; page_size?: number; total?: number }

export type ApiResponse<T> = { data: T; meta: ApiMeta }

export type ApiErrorDetail = { field?: string; message?: string; code?: string; [key: string]: unknown }

export type ApiErrorBody = {
  error: {
    code: string
    message: string
    details: ApiErrorDetail[]
    request_id: string
  }
}

export class ApiClientError extends Error {
  readonly status: number
  readonly code: string
  readonly details: ApiErrorDetail[]
  readonly requestId?: string

  constructor(status: number, body: ApiErrorBody | undefined) {
    super(body?.error.message || `请求失败（${status}）`)
    this.name = 'ApiClientError'
    this.status = status
    this.code = body?.error.code || 'REQUEST_FAILED'
    this.details = body?.error.details || []
    this.requestId = body?.error.request_id
  }
}

type RequestOptions = Omit<RequestInit, 'body'> & { body?: unknown }

async function request<T>(path: string, options: RequestOptions = {}): Promise<ApiResponse<T>> {
  const headers = new Headers(options.headers)
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData
  if (options.body !== undefined && !isFormData) headers.set('Content-Type', 'application/json')

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
    credentials: 'include',
    body: isFormData ? (options.body as FormData) : options.body === undefined ? undefined : JSON.stringify(options.body),
  })
  const payload = (await response.json().catch(() => undefined)) as ApiResponse<T> | ApiErrorBody | undefined
  if (!response.ok) throw new ApiClientError(response.status, payload && 'error' in payload ? payload : undefined)
  return payload as ApiResponse<T>
}

export type User = { id: string; email: string; display_name: string; role: string; status: string }
export type SessionInfo = { authenticated: boolean; user: User | null; expires_at: string | null }

export type AppSettings = {
  id: string | null
  teacher_name: string
  model_provider: string
  model_name: string
  model_base_url: string | null
  api_key_configured: boolean
  api_key_ref_hint: string | null
  storage_root: string
  retention_days: number | null
  queue_limit: number
  worker_concurrency: number
  updated_at: string | null
}

export type Student = {
  id: string
  class_id: string
  student_code: string
  display_name: string
  status: 'active' | 'inactive'
  created_at: string
  updated_at: string
}

export type ClassRoom = {
  id: string
  name: string
  status: 'active' | 'inactive'
  version: number
  student_count: number | null
  students?: Student[]
  created_at: string
  updated_at: string
}

export type BatchQuestion = {
  id: string
  question_no: string
  question_type: string
  max_score: string
  question_prompt: string | null
  reference_answer: string | null
  rubric_version_id: string | null
  sort_order: number
  version: number
}

export type AssignmentBatch = {
  id: string
  title: string
  class_id: string
  class_name: string | null
  subject: string
  total_score: string
  status: string
  current_revision: number
  version: number
  questions: BatchQuestion[]
  created_at: string
  updated_at: string
  archived_at: string | null
  next_action?: 'edit' | 'upload'
}

export type QuestionType = 'objective' | 'subjective'

export type RubricPoint = { id: string; label: string; max_score: string; sort_order: number }
export type RubricExample = { id: string; rubric_point_id: string | null; content: string; created_at: string }
export type RubricVersion = {
  id: string
  version_no: number
  total_score: string
  status: 'draft' | 'published' | 'retired'
  source_version_id: string | null
  created_by: string
  created_at: string
  points: RubricPoint[]
  examples: RubricExample[]
}
export type Rubric = {
  id: string
  name: string
  subject: string
  question_type: 'subjective'
  status: 'active' | 'inactive'
  current_version_no: number | null
  created_at: string
  updated_at: string
  current_version?: RubricVersion | null
}

export type SourceFile = {
  file_id: string
  status: 'pending' | 'validating' | 'success' | 'failed'
  original_name: string
  mime_type: string
  extension: string
  file_role: 'student_work' | 'question_paper'
  size_bytes: number
  sha256: string
  failure_code: string | null
  page_count: number | null
  created_at: string
  task_id?: string
}

export type UploadTask = {
  task_id: string
  task_type: string
  batch_id: string | null
  resource_id: string | null
  status: string
  stage: string
  progress_current: number
  progress_total: number
  success_count: number
  failed_count: number
  retry_count: number
  error_code: string | null
  error_message: string | null
  error_items: ApiErrorDetail[]
  created_at: string
  started_at: string | null
  finished_at: string | null
  answer_photo?: {
    id: string
    source_page_id: string
    question_id: string
    original_name: string
    status: string
    answer_text: string | null
    confidence: string | null
    recognition_mode: RecognitionMode
    error_code: string | null
    error_message: string | null
    updated_at: string
  }
}

export type RecognitionMode = 'printed' | 'chinese_handwriting' | 'english_handwriting' | 'math_handwriting'
export type OcrProfile = {
  recognition_mode: RecognitionMode
  engine_name: string
  engine_version: string
  preprocess_version: string
  parameters: Record<string, unknown>
}
export type PageGrouping = {
  id: string
  assignment_group_id: string | null
  student_id: string | null
  student_code: string | null
  student_name: string | null
  page_sequence: number | null
  grouping_status: string
  grouping_confidence: string | null
  grouping_reason: string | null
  confirmed_by: string | null
  confirmed_at: string | null
  version: number
  assignment_group_status: string | null
}
export type OcrBlock = {
  id: string
  block_index: number
  text_raw: string
  confidence: string | null
  x1: string | null
  y1: string | null
  x2: string | null
  y2: string | null
  question_id: string | null
  created_at: string
}
export type OcrRun = {
  id: string
  batch_id: string
  source_page_id: string
  engine_name: string
  engine_version: string
  preprocess_version: string
  status: string
  raw_output_storage_key: string | null
  error_code: string | null
  started_at: string | null
  finished_at: string | null
  blocks?: OcrBlock[]
}
export type SourcePage = {
  id: string
  source_file_id: string
  original_name: string
  file_role: 'student_work' | 'question_paper'
  page_index: number
  page_count: number | null
  original_storage_key: string
  processed_storage_key: string | null
  transform: Record<string, unknown> | null
  status: string
  created_at: string
  updated_at: string
  grouping: PageGrouping | null
  latest_ocr_run: OcrRun | null
  ocr_runs?: OcrRun[]
  saved_answers?: Record<string, {
    answer_text: string
    is_blank_confirmed: boolean
    source_type: 'ocr' | 'manual' | 'teacher_corrected'
    answer_photo_id: string | null
    coverage_status: string
    version_no: number
  }>
  batch_id?: string
  batch_title?: string
  subject?: string
}
export type QuestionLayoutPage = { source_page_id: string; page_index: number; width: number; height: number }
export type QuestionLayoutRegion = {
  question_id: string
  question_no: string
  source_page_id: string
  page_index: number
  x1: number
  y1: number
  x2: number
  y2: number
  confidence: number
  ocr_block_ids: string[]
}
export type QuestionLayout = {
  id: string
  batch_id: string
  source_page_id: string
  status: 'confirmed'
  version: number
  source_pages: QuestionLayoutPage[]
  regions: QuestionLayoutRegion[]
  confirmed_by: string | null
  confirmed_at: string | null
  created_at: string
  updated_at: string
}
export type OcrTaskResponse = {
  task: UploadTask & { max_retries?: number; page_count?: number; profile?: OcrProfile }
  reused: boolean
  execution?: string
  message?: string
  ocr_run_id?: string
}
export type AnswerPhotoOcrResponse = {
  task: UploadTask & { profile?: OcrProfile }
  photo: {
    id: string
    source_page_id: string
    question_id: string
    original_name: string
    status: string
    answer_text: string | null
    confidence: string | null
    recognition_mode: RecognitionMode
    error_code: string | null
    error_message: string | null
    updated_at: string
  }
  reused: boolean
  execution?: string
  message?: string
}
export type PageHistory = {
  page_id: string
  reviews: Array<{
    id: string
    entity_type: string
    entity_id: string
    action: string
    before: unknown
    after: unknown
    reason: string | null
    created_at: string
  }>
  answer_versions: Array<{
    id: string
    assignment_group_id: string
    question_id: string
    version_no: number
    answer_text: string | null
    is_blank_confirmed: boolean
    source_type: string
    confidence: string | null
    created_by: string
    created_at: string
  }>
}

export type GradingTask = {
  id: string
  grading_run_id: string
  assignment_group_id: string
  student_id: string | null
  student_code: string | null
  student_name: string | null
  question_id: string
  question_no: string | null
  task_type: 'objective' | 'subjective'
  status: string
  input_hash: string
  retry_count: number
  max_retries: number
  error_code: string | null
  error_message: string | null
  result_id: string | null
  result_status: string | null
  suggested_score: string | null
  teacher_score: string | null
  result_version: number | null
  created_at: string
  updated_at: string
}

export type ReviewPoint = {
  id: string
  rubric_point_id: string
  label: string | null
  max_score: string | null
  suggested_score: string
  teacher_score: string | null
  evidence: string | null
  deduction_reason: string | null
}

export type ReviewItem = {
  id: string
  version: number
  batch_id: string
  grading_run_id: string
  grading_task_id: string
  assignment_group_id: string
  student: { id: string | null; student_code: string | null; display_name: string | null }
  question: { id: string; question_no: string | null; question_type: string; question_prompt: string | null; reference_answer: string | null; max_score: string | null; rubric_version_id: string | null }
  answer: { version_id: string; version_no: number | null; answer_text: string | null; is_blank_confirmed: boolean; source_type: string | null; source_page_id: string | null }
  source_page: { id: string | null; page_index: number | null; original_name: string | null }
  result_status: string
  suggested_score: string | null
  teacher_score: string | null
  ai_comment: string | null
  teacher_comment: string | null
  evidence: unknown
  deduction: unknown
  model_name: string | null
  prompt_version: string | null
  rule_version: string | null
  input_hash: string
  task_status: string
  task_error_code: string | null
  task_error_message: string | null
  points: ReviewPoint[]
  anomalies: Array<{ id: string; anomaly_type: string; severity: string; evidence: unknown; status: string; resolution: string | null; created_at: string; resolved_at: string | null }>
  created_at: string
  updated_at: string
}

export type ReviewHistory = { item_id: string; records: Array<{ id: string; action: string; before: unknown; after: unknown; reason: string | null; created_at: string }> }

export type ReportStudentRow = {
  assignment_group_id: string
  student_id: string | null
  student_code: string | null
  student_name: string | null
  objective_score: string | null
  objective_max_score: string | null
  subjective_score: string | null
  subjective_max_score: string | null
  total_score: string | null
  total_max_score: string
  status: 'reviewed' | 'pending_review'
  missing_questions: string[]
}

export type ReportStudentPage = {
  id: string
  original_name: string
  mime_type: string
  extension: string
  page_index: number
  page_count: number | null
  page_sequence: number | null
  status: string
  is_pdf: boolean
}

export type ReportStudentQuestion = {
  id: string
  question_no: string
  question_type: string
  question_prompt: string | null
  max_score: string
  ai_score: string | null
  teacher_score: string | null
  ai_comment: string | null
  teacher_comment: string | null
  evidence: unknown
  deduction: unknown
  result_status: string
  task_status: string
  task_error_message: string | null
}

export type ReportStudentDetail = ReportStudentRow & {
  ai_total_score: string | null
  ai_total_max_score: string
  ai_scored_question_count: number
  teacher_total_score: string | null
  teacher_total_max_score: string
  pages: ReportStudentPage[]
  questions: ReportStudentQuestion[]
}

export type BatchReport = {
  batch_id: string
  batch_title: string
  class_id: string
  subject: string
  total_score: string
  mode: 'reviewed' | 'ai_preview'
  students: ReportStudentRow[]
  pending_review_count: number
  generated_at: string
  page?: number
  page_size?: number
  total?: number
}

export type ReportStatistics = {
  batch_id: string
  batch_title: string
  mode: 'reviewed' | 'ai_preview'
  student_count: number
  scored_count: number
  pending_review_count: number
  average_score: string | null
  highest_score: string | null
  highest_student_name: string | null
  distribution: Record<string, number>
  frequent_errors: Array<{ label: string; count: number }>
}

export type ReportExport = {
  id: string
  batch_id: string
  report_mode: 'reviewed' | 'ai_preview'
  format: 'pdf' | 'docx'
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  input_hash: string
  failure_code: string | null
  created_at: string
  finished_at: string | null
  download_url: string | null
}

export type GradingRun = {
  id: string
  batch_id: string
  scope: { assignment_group_ids: string[]; question_ids: string[] }
  model_provider: string | null
  status: string
  rule_version: string
  model_name: string | null
  prompt_version: string | null
  created_by: string
  created_at: string
  finished_at: string | null
  execution_scope: 'run' | 'single_task'
  task_id: string | null
  task_status: string | null
  stage: string | null
  progress_current: number
  progress_total: number
  worker_id: string | null
  heartbeat_at: string | null
  error_code: string | null
  error_message: string | null
  counts: Record<string, number>
  total_tasks: number
}

export type WorkbenchSummary = {
  pending: number
  processing: number
  reviewed: number
  rubrics: number
  tasks: Array<{
    id: string
    student: string
    question: string
    status: string
    tone: 'exception' | 'review' | 'pending'
    batch_id: string
    batch_status: string
  }>
}

export type StudentInput = { student_code: string; display_name: string }
export type BatchQuestionInput = Omit<BatchQuestion, 'id' | 'sort_order' | 'version'> & { question_type: QuestionType }
export type RubricPointInput = { label: string; max_score: string | number; sort_order: number }
export type RubricExampleInput = { content: string; rubric_point_id?: string | null }

export const api = {
  auth: {
    login: (body: { email: string; password: string; remember_me?: boolean }) => request<{ user: User; expires_at: string }>('/auth/login', { method: 'POST', body }),
    register: (body: { email: string; display_name: string; password: string }) => request<{ user: User; expires_at: string }>('/auth/register', { method: 'POST', body }),
    session: () => request<SessionInfo>('/auth/session'),
    logout: () => request<{ logged_out: boolean }>('/auth/logout', { method: 'POST' }),
  },
  settings: {
    get: () => request<AppSettings>('/settings'),
    updateProfile: (body: { teacher_name: string }) => request<AppSettings>('/settings/profile', { method: 'PATCH', body }),
    updateModel: (body: { provider_name: string; model_name: string; model_base_url?: string | null; api_key?: string | null; api_key_ref?: string | null; clear_api_key?: boolean }) => request<AppSettings>('/settings/model', { method: 'PATCH', body }),
    testModel: () => request<{ connected: boolean; provider_name: string; model_name: string; status_code: number; probe: string }>('/settings/model/test', { method: 'POST' }),
    updateStorage: (body: { storage_root: string; retention_days?: number | null; queue_limit: number; worker_concurrency: number }) => request<AppSettings>('/settings/storage', { method: 'PATCH', body }),
  },
  classes: {
    list: (status?: string) => request<ClassRoom[]>(`/classes${status ? `?status=${encodeURIComponent(status)}` : ''}`),
    create: (body: { name: string; students?: StudentInput[] }) => request<ClassRoom>('/classes', { method: 'POST', body }),
    get: (classId: string) => request<ClassRoom>(`/classes/${encodeURIComponent(classId)}`),
    update: (classId: string, body: { name?: string; status?: 'active' | 'inactive'; version: number }) => request<ClassRoom>(`/classes/${encodeURIComponent(classId)}`, { method: 'PATCH', body }),
    students: (classId: string) => request<Student[]>(`/classes/${encodeURIComponent(classId)}/students`),
    createStudent: (classId: string, body: StudentInput) => request<Student>(`/classes/${encodeURIComponent(classId)}/students`, { method: 'POST', body }),
    updateStudent: (studentId: string, body: Partial<StudentInput> & { status?: 'active' | 'inactive' }) => request<Student>(`/classes/students/${encodeURIComponent(studentId)}`, { method: 'PATCH', body }),
  },
  batches: {
    list: (params: { status?: string; class_id?: string; page?: number; page_size?: number } = {}) => {
      const query = new URLSearchParams()
      Object.entries(params).forEach(([key, value]) => value !== undefined && query.set(key, String(value)))
      return request<AssignmentBatch[]>(`/batches${query.size ? `?${query}` : ''}`)
    },
    summary: () => request<WorkbenchSummary>('/batches/summary'),
    create: (body: { title: string; class_id: string; subject: string; total_score: string | number; questions: BatchQuestionInput[]; save_mode?: 'draft' | 'upload' }) => request<AssignmentBatch>('/batches', { method: 'POST', body }),
    get: (batchId: string) => request<AssignmentBatch>(`/batches/${encodeURIComponent(batchId)}`),
    update: (batchId: string, body: { title?: string; subject?: string; total_score?: string | number; class_id?: string; version: number }) => request<AssignmentBatch>(`/batches/${encodeURIComponent(batchId)}`, { method: 'PATCH', body }),
    remove: (batchId: string) => request<{ deleted: boolean; id: string }>(`/batches/${encodeURIComponent(batchId)}`, { method: 'DELETE' }),
    purge: (batchId: string, confirmName: string) => request<{ purged: boolean; id: string; deleted_files: number; deleted_pages: number; deleted_groups: number; deleted_groupings: number }>(`/batches/${encodeURIComponent(batchId)}/purge`, { method: 'POST', body: { confirm_name: confirmName } }),
    deleteReviewed: (batchId: string, body: { password: string; confirm_name: string }) => request<{ deleted: boolean; id: string; deleted_files: number; deleted_pages: number; deleted_groups: number; deleted_exports: number }>(`/batches/${encodeURIComponent(batchId)}/delete-reviewed`, { method: 'POST', body }),
    validate: (batchId: string) => request<{ valid_for_upload: boolean; valid_for_grading: boolean; errors: ApiErrorDetail[]; grading_errors?: ApiErrorDetail[]; has_files: boolean; has_question_paper?: boolean }>(`/batches/${encodeURIComponent(batchId)}/validate`, { method: 'POST' }),
    replaceQuestions: (batchId: string, body: { questions: BatchQuestionInput[]; version: number; preserve_existing_structure?: boolean }) => request<AssignmentBatch>(`/batches/${encodeURIComponent(batchId)}/questions`, { method: 'PUT', body }),
    updateQuestionPrompt: (batchId: string, questionId: string, body: { question_prompt?: string; reference_answer?: string | null; source_page_id?: string; ocr_block_ids?: string[]; version: number }) => request<AssignmentBatch>(`/batches/${encodeURIComponent(batchId)}/questions/${encodeURIComponent(questionId)}/prompt`, { method: 'PUT', body }),
    questionLayout: (batchId: string) => request<QuestionLayout | null>(`/batches/${encodeURIComponent(batchId)}/question-layout`),
    saveQuestionLayout: (batchId: string, body: { source_page_id: string; source_pages: QuestionLayoutPage[]; regions: QuestionLayoutRegion[]; version: number }) => request<QuestionLayout>(`/batches/${encodeURIComponent(batchId)}/question-layout`, { method: 'PUT', body }),
    archive: (batchId: string) => request<{ id: string; status: string; archived_at: string }>(`/batches/${encodeURIComponent(batchId)}/archive`, { method: 'POST' }),
    files: (batchId: string, status?: string, fileRole?: SourceFile['file_role']) => {
      const query = new URLSearchParams()
      if (status) query.set('status', status)
      if (fileRole) query.set('file_role', fileRole)
      return request<SourceFile[]>(`/batches/${encodeURIComponent(batchId)}/files${query.size ? `?${query}` : ''}`)
    },
    pages: (batchId: string, status?: string, fileRole: SourceFile['file_role'] = 'student_work') => {
      const query = new URLSearchParams({ file_role: fileRole })
      if (status) query.set('status', status)
      return request<SourcePage[]>(`/batches/${encodeURIComponent(batchId)}/pages?${query}`)
    },
    startOcr: (batchId: string, body: { recognition_mode: RecognitionMode }, idempotencyKey?: string, fileRole: SourceFile['file_role'] = 'student_work') => request<OcrTaskResponse>(`/batches/${encodeURIComponent(batchId)}/ocr-runs?file_role=${encodeURIComponent(fileRole)}`, { method: 'POST', headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined, body }),
    ocrTask: (batchId: string, fileRole: SourceFile['file_role'] = 'student_work') => request<UploadTask | null>(`/batches/${encodeURIComponent(batchId)}/ocr-task?file_role=${encodeURIComponent(fileRole)}`),
    uploadFile: (batchId: string, file: File, idempotencyKey: string) => {
      const body = new FormData()
      body.append('file', file)
      return request<SourceFile>(`/batches/${encodeURIComponent(batchId)}/files`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body })
    },
    uploadQuestionPaper: (batchId: string, file: File, idempotencyKey: string) => {
      const body = new FormData()
      body.append('file', file)
      return request<SourceFile>(`/batches/${encodeURIComponent(batchId)}/question-paper`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body })
    },
  },
  files: {
    remove: (fileId: string) => request<{ deleted: boolean; file_id: string; batch_id: string }>(`/files/${encodeURIComponent(fileId)}`, { method: 'DELETE' }),
    purge: (fileId: string, confirmName: string) => request<{ purged: boolean; file_id: string; batch_id: string }>(`/files/${encodeURIComponent(fileId)}/purge`, { method: 'POST', body: { confirm_name: confirmName } }),
    retry: (fileId: string, file: File, idempotencyKey: string) => {
      const body = new FormData()
      body.append('file', file)
      return request<SourceFile>(`/files/${encodeURIComponent(fileId)}/retry`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body })
    },
  },
  pages: {
    get: (pageId: string) => request<SourcePage>(`/pages/${encodeURIComponent(pageId)}`),
    originalUrl: (pageId: string) => `${API_BASE_URL}/pages/${encodeURIComponent(pageId)}/original`,
    retryOcr: (pageId: string, body: { recognition_mode: RecognitionMode }, idempotencyKey?: string) => request<OcrTaskResponse>(`/pages/${encodeURIComponent(pageId)}/ocr-retry`, { method: 'POST', headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined, body }),
    recognizeAnswerPhoto: (pageId: string, questionId: string, recognitionMode: RecognitionMode, file: File, idempotencyKey?: string) => {
      const body = new FormData()
      body.append('file', file)
      body.append('question_id', questionId)
      body.append('recognition_mode', recognitionMode)
      return request<AnswerPhotoOcrResponse>(`/pages/${encodeURIComponent(pageId)}/answer-photo-ocr`, { method: 'POST', headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined, body })
    },
    updateGrouping: (pageId: string, body: { student_id?: string | null; assignment_group_id?: string | null; page_sequence?: number | null; reason: string; version: number }) => request<SourcePage>(`/pages/${encodeURIComponent(pageId)}/grouping`, { method: 'PATCH', body }),
    saveCorrection: (pageId: string, body: { answers: Array<{ question_id: string; present_on_page: boolean; answer_text?: string | null; is_blank_confirmed: boolean; source_type: 'ocr' | 'manual' | 'teacher_corrected' }>; save_mode: 'draft' | 'confirm'; version: number }) => request<SourcePage & { saved_answer_count: number; save_mode: 'draft' | 'confirm' }>(`/pages/${encodeURIComponent(pageId)}/correction`, { method: 'PUT', body }),
    confirm: (pageId: string, body: { version: number }) => request<SourcePage>(`/pages/${encodeURIComponent(pageId)}/confirm`, { method: 'POST', body }),
    history: (pageId: string) => request<PageHistory>(`/pages/${encodeURIComponent(pageId)}/history`),
  },
  tasks: {
    get: (taskId: string) => request<UploadTask>(`/tasks/${encodeURIComponent(taskId)}`),
  },
  rubrics: {
    list: (params: { subject?: string; question_type?: string; status?: string } = {}) => {
      const query = new URLSearchParams()
      Object.entries(params).forEach(([key, value]) => value !== undefined && query.set(key, value))
      return request<Rubric[]>(`/rubrics${query.size ? `?${query}` : ''}`)
    },
    create: (body: { name: string; subject: string; question_type: 'subjective'; total_score: string | number; points: RubricPointInput[]; examples?: RubricExampleInput[] }) => request<Rubric>('/rubrics', { method: 'POST', body }),
    get: (rubricId: string) => request<Rubric>(`/rubrics/${encodeURIComponent(rubricId)}`),
    update: (rubricId: string, body: { name: string; subject: string; question_type: 'subjective'; total_score: string | number; points: RubricPointInput[]; examples?: RubricExampleInput[]; version: number }) => request<Rubric>(`/rubrics/${encodeURIComponent(rubricId)}`, { method: 'PATCH', body }),
    activate: (rubricId: string) => request<Rubric>(`/rubrics/${encodeURIComponent(rubricId)}/activate`, { method: 'POST' }),
    deactivate: (rubricId: string) => request<Rubric>(`/rubrics/${encodeURIComponent(rubricId)}/deactivate`, { method: 'POST' }),
    versions: (rubricId: string) => request<RubricVersion[]>(`/rubrics/${encodeURIComponent(rubricId)}/versions`),
  },
  grading: {
    createRun: (batchId: string, body: { scope: { assignment_group_ids?: string[]; question_ids?: string[] }; force_rerun?: boolean }, idempotencyKey?: string) => request<{ run: GradingRun; reused: boolean; execution?: string; message?: string }>(`/batches/${encodeURIComponent(batchId)}/grading-runs`, { method: 'POST', headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined, body }),
    getRun: (runId: string) => request<GradingRun>(`/grading-runs/${encodeURIComponent(runId)}`),
    getLatestRun: (batchId: string) => request<GradingRun | null>(`/batches/${encodeURIComponent(batchId)}/grading-runs/latest`),
    listTasks: (batchId: string, params: { run_id?: string; status?: string } = {}) => {
      const query = new URLSearchParams()
      Object.entries(params).forEach(([key, value]) => value !== undefined && query.set(key, value))
      return request<GradingTask[]>(`/batches/${encodeURIComponent(batchId)}/grading-tasks${query.size ? `?${query}` : ''}`)
    },
    retryTask: (taskId: string) => request<{ task: GradingTask; execution: string }>(`/grading-tasks/${encodeURIComponent(taskId)}/retry`, { method: 'POST' }),
    rerunTask: (taskId: string) => request<{ task: GradingTask; execution: string }>(`/grading-tasks/${encodeURIComponent(taskId)}/rerun`, { method: 'POST' }),
    getResult: (resultId: string) => request<Record<string, unknown>>(`/grading-results/${encodeURIComponent(resultId)}`),
    reviewQueue: (batchId: string, params: { status?: string; page?: number; page_size?: number } = {}) => {
      const query = new URLSearchParams()
      Object.entries(params).forEach(([key, value]) => value !== undefined && query.set(key, String(value)))
      return request<ReviewItem[]>(`/batches/${encodeURIComponent(batchId)}/review-queue${query.size ? `?${query}` : ''}`)
    },
    getReviewItem: (itemId: string) => request<ReviewItem>(`/review-items/${encodeURIComponent(itemId)}`),
    saveReviewItem: (itemId: string, body: { teacher_score?: string | number | null; teacher_comment?: string | null; anomaly_resolution?: 'confirmed' | 'ignored' | 'resolved' | null; reason?: string | null; point_scores?: Array<{ point_id: string; teacher_score: string | number }>; version: number }) => request<ReviewItem>(`/review-items/${encodeURIComponent(itemId)}`, { method: 'PATCH', body }),
    confirmReviewItem: (itemId: string, body: { teacher_score?: string | number | null; teacher_comment?: string | null; anomaly_resolution?: 'confirmed' | 'ignored' | 'resolved' | null; reason?: string | null; point_scores?: Array<{ point_id: string; teacher_score: string | number }>; version: number }) => request<ReviewItem>(`/review-items/${encodeURIComponent(itemId)}/confirm`, { method: 'POST', body }),
    rerunReviewItem: (itemId: string, body: { reason?: string | null; version: number }) => request<{ task: GradingTask; execution: string }>(`/review-items/${encodeURIComponent(itemId)}/rerun`, { method: 'POST', body }),
    reviewHistory: (itemId: string) => request<ReviewHistory>(`/review-items/${encodeURIComponent(itemId)}/history`),
  },
  reports: {
    batch: (batchId: string, params: { mode?: 'reviewed' | 'ai_preview'; page?: number; page_size?: number } = {}) => {
      const query = new URLSearchParams()
      Object.entries(params).forEach(([key, value]) => value !== undefined && query.set(key, String(value)))
      return request<BatchReport>(`/batches/${encodeURIComponent(batchId)}/reports${query.size ? `?${query}` : ''}`)
    },
    student: (batchId: string, studentId: string, mode: 'reviewed' | 'ai_preview' = 'reviewed') => request<{ batch: BatchReport; student: ReportStudentDetail }>(`/batches/${encodeURIComponent(batchId)}/reports/students/${encodeURIComponent(studentId)}?mode=${encodeURIComponent(mode)}`),
    statistics: (batchId: string, mode: 'reviewed' | 'ai_preview' = 'reviewed') => request<ReportStatistics>(`/batches/${encodeURIComponent(batchId)}/reports/statistics?mode=${encodeURIComponent(mode)}`),
    createExport: (batchId: string, body: { report_mode: 'reviewed' | 'ai_preview'; format: 'pdf' | 'docx' }) => request<ReportExport>(`/batches/${encodeURIComponent(batchId)}/reports/exports`, { method: 'POST', body }),
    getExport: (exportId: string) => request<ReportExport>(`/report-exports/${encodeURIComponent(exportId)}`),
    exportDownloadUrl: (exportId: string) => `${API_BASE_URL}/report-exports/${encodeURIComponent(exportId)}/download`,
  },
}
