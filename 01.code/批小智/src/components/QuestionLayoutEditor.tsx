import { useRef, useState, type PointerEvent } from 'react'
import { TrashSimple } from '@phosphor-icons/react'
import type { BatchQuestion, QuestionLayoutPage, QuestionLayoutRegion } from '../api/client'

type DragState = { pointerId: number; x1: number; y1: number; x2: number; y2: number }

type Props = {
  questions: BatchQuestion[]
  sourcePages: QuestionLayoutPage[]
  imageUrls: Record<string, string>
  regions: QuestionLayoutRegion[]
  onChange: (regions: QuestionLayoutRegion[]) => void
}

function clampUnit(value: number) {
  return Math.max(0, Math.min(1, value))
}

export function QuestionLayoutEditor({ questions, sourcePages, imageUrls, regions, onChange }: Props) {
  const [pageId, setPageId] = useState(sourcePages[0]?.source_page_id ?? '')
  const [questionId, setQuestionId] = useState(questions[0]?.id ?? '')
  const [drag, setDrag] = useState<DragState | null>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const page = sourcePages.find((item) => item.source_page_id === pageId) ?? sourcePages[0]
  const question = questions.find((item) => item.id === questionId) ?? questions[0]
  const pageRegions = regions.map((region, index) => ({ region, index })).filter(({ region }) => region.source_page_id === page?.source_page_id)

  const point = (event: PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return {
      x: clampUnit((event.clientX - rect.left) / rect.width),
      y: clampUnit((event.clientY - rect.top) / rect.height),
    }
  }

  const startDrawing = (event: PointerEvent<HTMLDivElement>) => {
    if (!page || !question || event.button !== 0) return
    const start = point(event)
    event.currentTarget.setPointerCapture(event.pointerId)
    setDrag({ pointerId: event.pointerId, x1: start.x, y1: start.y, x2: start.x, y2: start.y })
  }

  const moveDrawing = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag || drag.pointerId !== event.pointerId) return
    const end = point(event)
    setDrag((current) => current ? { ...current, x2: end.x, y2: end.y } : null)
  }

  const finishDrawing = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag || !page || !question || drag.pointerId !== event.pointerId) return
    const end = point(event)
    const region: QuestionLayoutRegion = {
      question_id: question.id,
      question_no: question.question_no,
      source_page_id: page.source_page_id,
      page_index: page.page_index,
      x1: Math.min(drag.x1, end.x),
      y1: Math.min(drag.y1, end.y),
      x2: Math.max(drag.x1, end.x),
      y2: Math.max(drag.y1, end.y),
      confidence: 1,
      ocr_block_ids: [],
    }
    if (region.x2 - region.x1 >= 0.008 && region.y2 - region.y1 >= 0.006) onChange([...regions, region])
    setDrag(null)
  }

  const updateRegion = (index: number, patch: Partial<QuestionLayoutRegion>) => {
    onChange(regions.map((region, currentIndex) => currentIndex === index ? { ...region, ...patch } : region))
  }

  const updateEdge = (index: number, key: 'x1' | 'y1' | 'x2' | 'y2', value: string) => {
    const percent = Number(value)
    if (!Number.isFinite(percent)) return
    updateRegion(index, { [key]: clampUnit(percent / 100) })
  }

  if (!page || !question) return <div className="empty-inline">选择题目页和题目后，才能设置答题区域。</div>

  return <div className="question-layout-editor">
    <div className="question-layout-editor-toolbar">
      <label className="field compact"><span>模板页面</span><select value={page.source_page_id} onChange={(event) => setPageId(event.target.value)}>{sourcePages.map((item) => <option key={item.source_page_id} value={item.source_page_id}>第 {item.page_index} 页</option>)}</select></label>
      <label className="field compact"><span>拖拽区域归属题目</span><select value={question.id} onChange={(event) => setQuestionId(event.target.value)}>{questions.map((item) => <option key={item.id} value={item.id}>第 {item.question_no} 题 · {item.question_type === 'objective' ? '客观题' : '主观题'}</option>)}</select></label>
      <p>在原图上按住并拖动，框出这道题实际作答的位置。一个题目可以添加多个分散区域；任何区域都不要重叠。</p>
    </div>
    <div className="question-layout-editor-stage-wrap">
      <div
        ref={stageRef}
        className={`question-layout-editor-stage${drag ? ' drawing' : ''}`}
        style={{ aspectRatio: `${page.width} / ${page.height}` }}
        onPointerDown={startDrawing}
        onPointerMove={moveDrawing}
        onPointerUp={finishDrawing}
        onPointerCancel={() => setDrag(null)}
        role="group"
        aria-label={`第 ${page.page_index} 页原图答题区域编辑，可拖动创建第 ${question.question_no} 题区域`}
      >
        <img src={imageUrls[page.source_page_id]} alt={`第 ${page.page_index} 页题目和作答原图`} draggable={false} />
        {pageRegions.map(({ region, index }) => <div
          key={`${region.question_id}-${index}`}
          className={`question-layout-editor-box${region.question_id === question.id ? ' active' : ''}`}
          style={{ left: `${region.x1 * 100}%`, top: `${region.y1 * 100}%`, width: `${(region.x2 - region.x1) * 100}%`, height: `${(region.y2 - region.y1) * 100}%` }}
          aria-hidden="true"
        >第 {region.question_no} 题</div>)}
        {drag && <div className="question-layout-editor-box draft" style={{ left: `${Math.min(drag.x1, drag.x2) * 100}%`, top: `${Math.min(drag.y1, drag.y2) * 100}%`, width: `${Math.abs(drag.x2 - drag.x1) * 100}%`, height: `${Math.abs(drag.y2 - drag.y1) * 100}%` }} aria-hidden="true" />}
      </div>
    </div>
    <div className="question-layout-editor-list" aria-label="已设置的答题区域">
      {regions.map((region, index) => <div className="question-layout-editor-row" key={`${region.source_page_id}-${region.question_id}-${index}`}>
        <div className="question-layout-editor-row-head">
          <label className="field compact"><span>题目</span><select value={region.question_id} onChange={(event) => {
            const selected = questions.find((item) => item.id === event.target.value)
            updateRegion(index, { question_id: event.target.value, question_no: selected?.question_no ?? region.question_no })
          }}>{questions.map((item) => <option key={item.id} value={item.id}>第 {item.question_no} 题</option>)}</select></label>
          <span className="question-layout-editor-page">第 {region.page_index} 页</span>
          <button type="button" className="secondary-button question-layout-editor-delete" onClick={() => onChange(regions.filter((_item, currentIndex) => currentIndex !== index))} aria-label={`删除第 ${region.question_no} 题第 ${region.page_index} 页区域`}><TrashSimple size={16} aria-hidden="true" />删除区域</button>
        </div>
        <div className="question-layout-editor-coordinates">
          {(['x1', 'y1', 'x2', 'y2'] as const).map((key) => <label className="field compact" key={key}><span>{{ x1: '左', y1: '上', x2: '右', y2: '下' }[key]}（%）</span><input type="number" min="0" max="100" step="0.5" value={(region[key] * 100).toFixed(1)} onChange={(event) => updateEdge(index, key, event.target.value)} /></label>)}
        </div>
      </div>)}
      {!regions.length && <div className="empty-inline">尚未设置区域。请在上方原图中拖拽，或检查题目页是否已加载。</div>}
    </div>
  </div>
}
