import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchWithAuth } from '../utils/api.js'

const DEFAULT_MODEL = 'zai-org/GLM-OCR'

const DEFAULT_FIELDS = [
    { key: '製作日期', value: '' },
    { key: '報告編號', value: '' },
    { key: '報告類別', value: '' },
    { key: '申請者名稱', value: '' },
    { key: '申請者地址', value: '' },
    { key: '申請法規項目名稱', value: '' },
    { key: '廠牌', value: '' },
    { key: '製造廠地址', value: '' },
    { key: '型式系列/型式系列編號', value: '' },
    { key: '型式名稱/型式編號', value: '' },
]

const TASKS = [
    { id: 'document', label: '📑 完整文件解析', hint: '版面、表格、公式、Markdown 與結構化 JSON' },
    { id: 'text', label: '📝 文字辨識', hint: 'Text Recognition' },
    { id: 'table', label: '▦ 表格辨識', hint: 'Table Recognition' },
    { id: 'formula', label: '∑ 公式辨識', hint: 'Formula Recognition' },
    { id: 'extract', label: '🏷️ 欄位萃取', hint: '依 JSON schema 擷取資訊' },
]

const PROVIDERS = [
    { id: 'local', label: '專案內本機推論', hint: 'Transformers，不需要 Ollama' },
    { id: 'openai', label: 'vLLM / SGLang', hint: 'OpenAI 相容服務，適合正式環境' },
    { id: 'ollama', label: 'Ollama（相容）', hint: '保留舊環境使用，非必要依賴' },
]

function downloadText(text, type, extension) {
    const blob = new Blob([text], { type })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `glm_ocr_${Date.now()}.${extension}`
    anchor.click()
    URL.revokeObjectURL(url)
}

export default function OCR() {
    const [file, setFile] = useState(null)
    const [dragOver, setDragOver] = useState(false)
    const fileInputRef = useRef(null)

    const [task, setTask] = useState('document')
    const [provider, setProvider] = useState('local')
    const [model, setModel] = useState(DEFAULT_MODEL)
    const [enableLayout, setEnableLayout] = useState(true)
    const [outputFormat, setOutputFormat] = useState('both')
    const [maxRetries, setMaxRetries] = useState(3)
    const [autoMerge, setAutoMerge] = useState(false)
    const [fields, setFields] = useState(() => DEFAULT_FIELDS.map(field => ({ ...field })))

    const [capabilities, setCapabilities] = useState(null)
    const [processing, setProcessing] = useState(false)
    const [progress, setProgress] = useState(0)
    const [currentPage, setCurrentPage] = useState(0)
    const [totalPages, setTotalPages] = useState(0)
    const [results, setResults] = useState([])
    const [mergedResult, setMergedResult] = useState(null)
    const [documentResult, setDocumentResult] = useState(null)
    const [error, setError] = useState('')
    const [copied, setCopied] = useState(false)

    useEffect(() => {
        let active = true
        fetchWithAuth('/api/ocr/capabilities')
            .then(response => response.ok ? response.json() : null)
            .then(data => { if (active && data) setCapabilities(data) })
            .catch(() => {})
        return () => { active = false }
    }, [])

    const currentProvider = useMemo(
        () => capabilities?.providers?.find(item => item.name === provider),
        [capabilities, provider],
    )
    const taskInfo = TASKS.find(item => item.id === task)

    const resetOutput = useCallback(() => {
        setResults([])
        setMergedResult(null)
        setDocumentResult(null)
        setProgress(0)
        setCurrentPage(0)
        setTotalPages(0)
        setError('')
    }, [])

    const selectFile = useCallback(selected => {
        if (!selected) return
        setFile(selected)
        resetOutput()
    }, [resetOutput])

    const changeProvider = value => {
        setProvider(value)
        if (value === 'local' && (model === 'glm-ocr' || model === 'glm-ocr:latest')) {
            setModel(DEFAULT_MODEL)
        } else if (value === 'ollama' && model === DEFAULT_MODEL) {
            setModel('glm-ocr:latest')
        }
    }

    const updateField = (index, key, value) => {
        setFields(previous => previous.map((field, fieldIndex) => (
            fieldIndex === index ? { ...field, [key]: value } : field
        )))
    }

    const buildFieldSchema = () => {
        const schema = {}
        fields.filter(field => field.key.trim()).forEach(field => {
            let value = field.value
            if (/^[\[{]/.test(value.trim())) {
                try {
                    value = JSON.parse(value)
                } catch {
                    // Keep non-JSON format hints as plain text.
                }
            }
            schema[field.key.trim()] = value
        })
        return schema
    }

    const handleSubmit = async () => {
        if (!file) {
            setError('請先上傳檔案')
            return
        }
        const schema = task === 'extract' ? buildFieldSchema() : {}
        if (task === 'extract' && Object.keys(schema).length === 0) {
            setError('欄位萃取至少需要一個欄位')
            return
        }

        setProcessing(true)
        resetOutput()
        try {
            const formData = new FormData()
            formData.append('file', file)
            formData.append('fields', JSON.stringify(schema))
            formData.append('task', task)
            formData.append('provider', provider)
            formData.append('model', model)
            formData.append('max_retries', String(maxRetries))
            formData.append('auto_merge', autoMerge ? 'true' : 'false')
            formData.append('enable_layout', enableLayout ? 'true' : 'false')
            formData.append('output_format', outputFormat)

            const response = await fetchWithAuth('/api/ocr/process', {
                method: 'POST',
                body: formData,
            })
            if (!response.ok) {
                const detail = await response.json().catch(() => ({}))
                throw new Error(detail.detail || `HTTP ${response.status}`)
            }

            const reader = response.body.getReader()
            const decoder = new TextDecoder()
            let buffer = ''
            while (true) {
                const { done, value } = await reader.read()
                if (done) break
                buffer += decoder.decode(value, { stream: true })
                const lines = buffer.split('\n')
                buffer = lines.pop() || ''
                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue
                    let data
                    try {
                        data = JSON.parse(line.slice(6))
                    } catch {
                        continue
                    }
                    setProgress(data.percent || 0)
                    setCurrentPage(data.page || 0)
                    setTotalPages(data.total || 0)
                    if (data.error && !data.all_results) {
                        setError(data.error)
                    }
                    if (data.done && data.all_results) {
                        setResults(data.all_results)
                        setMergedResult(data.merged || null)
                        setDocumentResult(data.document || null)
                    } else if (!data.done) {
                        setResults(previous => [...previous, data])
                    }
                }
            }
        } catch (reason) {
            setError(reason.message || '辨識失敗')
        } finally {
            setProcessing(false)
        }
    }

    const markdownExport = documentResult?.markdown || results
        .filter(result => result.success)
        .map(result => result.markdown || result.raw || '')
        .filter(Boolean)
        .join('\n\n---\n\n')

    const jsonExportValue = mergedResult || (
        task === 'extract'
            ? results.filter(result => result.success).map(result => result.data)
            : documentResult?.json || results.filter(result => result.success).map(result => result.json)
    )
    const jsonExport = JSON.stringify(jsonExportValue, null, 2)

    const copyResult = async () => {
        const value = task === 'extract' ? jsonExport : markdownExport
        await navigator.clipboard.writeText(value)
        setCopied(true)
        setTimeout(() => setCopied(false), 1800)
    }

    const unloadModel = async () => {
        await fetchWithAuth('/api/ocr/unload', { method: 'POST' }).catch(() => {})
        const response = await fetchWithAuth('/api/ocr/capabilities').catch(() => null)
        if (response?.ok) setCapabilities(await response.json())
    }

    return (
        <div className="fade-in">
            <div className="page-header">
                <h2>📄 GLM-OCR 文件辨識</h2>
                <p>本機直接推論或連接推論服務；支援完整文件、文字、表格、公式與 JSON 欄位萃取</p>
            </div>

            {!processing && results.length === 0 && (
                <div className={`ocr-layout ${task !== 'extract' ? 'ocr-layout-full' : ''}`}>
                    <div className="ocr-upload-section">
                        <div className="card">
                            <h3 className="ocr-section-title">📁 文件與推論設定</h3>
                            <div
                                className={`upload-zone ${dragOver ? 'drag-over' : ''}`}
                                onClick={() => fileInputRef.current?.click()}
                                onDrop={event => {
                                    event.preventDefault()
                                    setDragOver(false)
                                    selectFile(event.dataTransfer.files?.[0])
                                }}
                                onDragOver={event => { event.preventDefault(); setDragOver(true) }}
                                onDragLeave={() => setDragOver(false)}
                            >
                                <input
                                    ref={fileInputRef}
                                    type="file"
                                    accept=".png,.jpg,.jpeg,.bmp,.tiff,.tif,.webp,.gif,.pdf"
                                    onChange={event => selectFile(event.target.files?.[0])}
                                    style={{ display: 'none' }}
                                />
                                {file ? (
                                    <div className="file-selected">
                                        <span className="upload-icon">📎</span>
                                        <p>{file.name}</p>
                                        <span className="upload-hint">{(file.size / 1024 / 1024).toFixed(2)} MB — 點擊更換</span>
                                    </div>
                                ) : (
                                    <>
                                        <div className="upload-icon">📤</div>
                                        <p>拖放或點擊上傳圖片 / PDF</p>
                                        <span className="upload-hint">支援常見圖片格式與 PDF，最大 50 MB</span>
                                    </>
                                )}
                            </div>

                            <div className="ocr-options-row" style={{ marginTop: 16 }}>
                                <div className="form-group">
                                    <label className="form-label">任務</label>
                                    <select className="form-select" value={task} onChange={event => setTask(event.target.value)}>
                                        {TASKS.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
                                    </select>
                                    <span className="upload-hint">{taskInfo?.hint}</span>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">推論方式</label>
                                    <select className="form-select" value={provider} onChange={event => changeProvider(event.target.value)}>
                                        {PROVIDERS.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
                                    </select>
                                    <span className="upload-hint">{PROVIDERS.find(item => item.id === provider)?.hint}</span>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">模型</label>
                                    <input className="form-input" value={model} onChange={event => setModel(event.target.value)} />
                                </div>
                            </div>

                            <div className="ocr-options-row" style={{ marginTop: 12 }}>
                                <div className="form-group">
                                    <label className="form-label">輸出格式</label>
                                    <select className="form-select" value={outputFormat} onChange={event => setOutputFormat(event.target.value)}>
                                        <option value="both">Markdown + JSON</option>
                                        <option value="markdown">Markdown</option>
                                        <option value="json">JSON</option>
                                    </select>
                                </div>
                                <div className="form-group">
                                    <label className="form-label">最多嘗試</label>
                                    <select className="form-select" value={maxRetries} onChange={event => setMaxRetries(Number(event.target.value))}>
                                        {[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value} 次</option>)}
                                    </select>
                                </div>
                            </div>

                            {task === 'document' && (
                                <label className="checkbox-label" style={{ marginTop: 16 }}>
                                    <input type="checkbox" checked={enableLayout} onChange={event => setEnableLayout(event.target.checked)} />
                                    啟用 PP-DocLayoutV3 版面分析（完整 GLM-OCR 管線）
                                </label>
                            )}
                            {task === 'extract' && (
                                <label className="checkbox-label" style={{ marginTop: 16 }}>
                                    <input type="checkbox" checked={autoMerge} onChange={event => setAutoMerge(event.target.checked)} />
                                    多頁欄位一致時自動合併
                                </label>
                            )}

                            {currentProvider && !currentProvider.available && (
                                <div className="ocr-error" style={{ marginTop: 16 }}>
                                    本機推論依賴尚未安裝，請從 Manager 重新安裝 Python 依賴。
                                </div>
                            )}
                            {task === 'document' && enableLayout && capabilities && !capabilities.glmocr?.installed && (
                                <div className="ocr-error" style={{ marginTop: 16 }}>
                                    完整文件管線尚未安裝；需要 requirements-ocr.txt 中的官方 glmocr 套件。
                                </div>
                            )}
                        </div>
                    </div>

                    {task === 'extract' && (
                        <div className="ocr-fields-section">
                            <div className="card">
                                <div className="ocr-fields-header">
                                    <h3 className="ocr-section-title">🏷️ JSON Schema 欄位</h3>
                                    <div className="ocr-fields-actions">
                                        <button className="btn btn-outline btn-sm" onClick={() => setFields(DEFAULT_FIELDS.map(item => ({ ...item })))}>載入範本</button>
                                        <button className="btn btn-outline btn-sm" onClick={() => setFields([{ key: '', value: '' }])}>清空</button>
                                    </div>
                                </div>
                                <p className="ocr-fields-hint">欄位值可填格式提示，例如 YYYY-MM-DD；也可使用 JSON 字串描述巢狀結構。</p>
                                <div className="ocr-fields-list">
                                    {fields.map((field, index) => (
                                        <div key={index} className="ocr-field-row">
                                            <input className="form-input ocr-field-key" placeholder="欄位名稱" value={field.key} onChange={event => updateField(index, 'key', event.target.value)} />
                                            <input className="form-input ocr-field-value" placeholder="格式提示（選填）" value={field.value} onChange={event => updateField(index, 'value', event.target.value)} />
                                            <button className="btn btn-outline btn-sm ocr-field-remove" onClick={() => setFields(previous => previous.filter((_, itemIndex) => itemIndex !== index))}>✕</button>
                                        </div>
                                    ))}
                                </div>
                                <button className="btn btn-outline btn-sm" onClick={() => setFields(previous => [...previous, { key: '', value: '' }])} style={{ marginTop: 12 }}>➕ 新增欄位</button>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {error && !processing && <div className="ocr-error"><span>⚠️</span> {error}</div>}

            {!processing && results.length === 0 && (
                <button className="btn btn-primary btn-lg" style={{ marginTop: 24 }} onClick={handleSubmit} disabled={!file}>
                    🔍 開始 GLM-OCR 辨識
                </button>
            )}

            {processing && (
                <div className="card ocr-processing-card fade-in">
                    <div className="ocr-processing-icon"><div className="spinner-lg" /></div>
                    <h3>正在執行 {taskInfo?.label}...</h3>
                    {totalPages > 0 && <p className="text-muted">第 {currentPage} / {totalPages} 頁</p>}
                    <div className="progress-bar-container" style={{ marginTop: 16 }}>
                        <div className="progress-bar-fill processing" style={{ width: `${progress}%` }} />
                    </div>
                </div>
            )}

            {!processing && results.length > 0 && (
                <div className="fade-in">
                    <div className="ocr-results-header">
                        <h3>辨識結果</h3>
                        <div className="ocr-results-actions">
                            <button className="btn btn-outline btn-sm" onClick={copyResult}>{copied ? '✅ 已複製' : '📋 複製'}</button>
                            <button className="btn btn-outline btn-sm" onClick={() => downloadText(markdownExport, 'text/markdown', 'md')} disabled={!markdownExport}>💾 Markdown</button>
                            <button className="btn btn-outline btn-sm" onClick={() => downloadText(jsonExport, 'application/json', 'json')}>💾 JSON</button>
                            {provider === 'local' && <button className="btn btn-outline btn-sm" onClick={unloadModel}>釋放模型</button>}
                            <button className="btn btn-primary btn-sm" onClick={resetOutput}>🔄 重新辨識</button>
                        </div>
                    </div>

                    {mergedResult && (
                        <div className="card ocr-merged-card" style={{ marginBottom: 'var(--space-lg)' }}>
                            <div className="ocr-result-card-header"><span className="badge badge-completed">📑 多頁合併結果</span></div>
                            <pre className="ocr-raw-text">{JSON.stringify(mergedResult, null, 2)}</pre>
                        </div>
                    )}

                    <div className="ocr-results-grid">
                        {results.map((result, index) => (
                            <div key={`${result.page}-${index}`} className={`card ocr-result-card ${result.success ? '' : 'ocr-result-error'}`}>
                                <div className="ocr-result-card-header">
                                    <span className={`badge ${result.success ? 'badge-completed' : 'badge-failed'}`}>{result.success ? '✅ 成功' : '❌ 失敗'}</span>
                                    <span className="text-muted" style={{ fontSize: '0.8rem' }}>第 {result.page} / {result.total} 頁 · {result.provider} · {result.elapsed_ms || 0} ms</span>
                                </div>
                                {result.error && <p className="ocr-error">{result.error}</p>}
                                {result.markdown && outputFormat !== 'json' && (
                                    <div className="ocr-result-raw">
                                        <strong>Markdown / 文字</strong>
                                        <pre className="ocr-raw-text">{result.markdown}</pre>
                                    </div>
                                )}
                                {result.data != null && outputFormat !== 'markdown' && (
                                    <div className="ocr-result-raw">
                                        <strong>結構化 JSON</strong>
                                        <pre className="ocr-raw-text">{JSON.stringify(result.data, null, 2)}</pre>
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    )
}
