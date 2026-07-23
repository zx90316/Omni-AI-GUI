import { useNavigate } from 'react-router-dom'

const STATUS_MAP = {
    pending: { label: '等待中', className: 'badge-pending' },
    processing: { label: '辨識中', className: 'badge-processing' },
    completed: { label: '已完成', className: 'badge-completed' },
    failed: { label: '失敗', className: 'badge-failed' },
}

function downloadText(text, type, extension, filename) {
    const blob = new Blob([text], { type })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${filename}.${extension}`
    anchor.click()
    URL.revokeObjectURL(url)
}

export default function OcrTaskDetail({ task }) {
    const navigate = useNavigate()
    const status = STATUS_MAP[task.status] || STATUS_MAP.pending
    const options = task.task_options || {}
    const persisted = task.result_data || {}
    const results = persisted.results || []
    const markdown = persisted.document?.markdown || results
        .filter(result => result.success)
        .map(result => result.markdown || result.raw || '')
        .filter(Boolean)
        .join('\n\n---\n\n')
    const jsonValue = persisted.merged || persisted.document?.json || results
        .filter(result => result.success)
        .map(result => result.data ?? result.json)
    const jsonText = JSON.stringify(jsonValue, null, 2)
    const baseName = task.filename.replace(/\.[^.]+$/, '') || 'ocr_result'
    const isProcessing = ['pending', 'processing'].includes(task.status)

    return (
        <div className="fade-in">
            <div className="ocr-results-header" style={{ marginBottom: 'var(--space-xl)' }}>
                <div>
                    <button className="btn btn-outline btn-sm" onClick={() => navigate('/')} style={{ marginBottom: 'var(--space-sm)' }}>
                        ← 返回清單
                    </button>
                    <h2 style={{ fontSize: '1.5rem', fontWeight: 700 }}>📄 {task.filename}</h2>
                    <div className="task-card-meta" style={{ marginTop: 'var(--space-sm)' }}>
                        <span className={`badge ${status.className}`}>{status.label}</span>
                        <span>{options.task || task.language}</span>
                        <span>·</span>
                        <span>{options.provider || 'local'}</span>
                        <span>·</span>
                        <span>{task.model}</span>
                    </div>
                </div>
                <div className="ocr-results-actions">
                    <button className="btn btn-outline btn-sm" onClick={() => navigate('/ocr')}>新增 OCR 任務</button>
                    {task.status === 'completed' && (
                        <>
                            <button
                                className="btn btn-outline btn-sm"
                                onClick={() => downloadText(markdown, 'text/markdown', 'md', `${baseName}_ocr`)}
                                disabled={!markdown}
                            >
                                💾 Markdown
                            </button>
                            <button
                                className="btn btn-outline btn-sm"
                                onClick={() => downloadText(jsonText, 'application/json', 'json', `${baseName}_ocr`)}
                            >
                                💾 JSON
                            </button>
                        </>
                    )}
                </div>
            </div>

            {isProcessing && (
                <div className="card ocr-processing-card">
                    <div className="ocr-processing-icon"><div className="spinner-lg" /></div>
                    <h3>{task.progress_message}</h3>
                    <p className="text-muted">{Math.round(task.progress || 0)}%</p>
                    <div className="progress-bar-container" style={{ marginTop: 16 }}>
                        <div className="progress-bar-fill processing" style={{ width: `${task.progress || 0}%` }} />
                    </div>
                    <p className="text-muted" style={{ marginTop: 12 }}>可以離開此頁；任務會在背景繼續執行。</p>
                </div>
            )}

            {task.status === 'failed' && (
                <div className="ocr-error"><span>⚠️</span> {task.error_message || '辨識失敗'}</div>
            )}

            {persisted.merged && (
                <div className="card ocr-merged-card" style={{ marginBottom: 'var(--space-lg)' }}>
                    <div className="ocr-result-card-header"><span className="badge badge-completed">📑 多頁合併結果</span></div>
                    <pre className="ocr-raw-text">{JSON.stringify(persisted.merged, null, 2)}</pre>
                </div>
            )}

            {results.length > 0 && (
                <div className="ocr-results-grid">
                    {results.map((result, index) => (
                        <div key={`${result.page}-${index}`} className={`card ocr-result-card ${result.success ? '' : 'ocr-result-error'}`}>
                            <div className="ocr-result-card-header">
                                <span className={`badge ${result.success ? 'badge-completed' : 'badge-failed'}`}>
                                    {result.success ? '✅ 成功' : '❌ 失敗'}
                                </span>
                                <span className="text-muted" style={{ fontSize: '0.8rem' }}>
                                    第 {result.page} / {result.total} 頁 · {result.provider || options.provider}
                                </span>
                            </div>
                            {result.error && <p className="ocr-error">{result.error}</p>}
                            {result.markdown && options.output_format !== 'json' && (
                                <div className="ocr-result-raw">
                                    <strong>Markdown / 文字</strong>
                                    <pre className="ocr-raw-text">{result.markdown}</pre>
                                </div>
                            )}
                            {result.data != null && options.output_format !== 'markdown' && (
                                <div className="ocr-result-raw">
                                    <strong>結構化 JSON</strong>
                                    <pre className="ocr-raw-text">{JSON.stringify(result.data, null, 2)}</pre>
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}
