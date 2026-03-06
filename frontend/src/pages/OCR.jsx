import { useState, useRef, useCallback } from 'react'
import { fetchWithAuth } from '../utils/api.js'

// 預設欄位範本
const DEFAULT_FIELDS = [
    { key: '製作日期', value: 'YYYYMMDD' },
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

export default function OCR() {
    // 檔案
    const [file, setFile] = useState(null)
    const [dragOver, setDragOver] = useState(false)
    const fileInputRef = useRef(null)

    // 欄位
    const [fields, setFields] = useState(() => DEFAULT_FIELDS.map(f => ({ ...f })))

    // 模型 & 重試
    const [model, setModel] = useState('glm-ocr')
    const [maxRetries, setMaxRetries] = useState(3)

    // 處理狀態
    const [processing, setProcessing] = useState(false)
    const [progress, setProgress] = useState(0)
    const [currentPage, setCurrentPage] = useState(0)
    const [totalPages, setTotalPages] = useState(0)

    // 結果
    const [results, setResults] = useState([])
    const [error, setError] = useState('')
    const [copied, setCopied] = useState(false)

    // ── 檔案處理 ──
    const handleFileSelect = useCallback((e) => {
        const f = e.target.files?.[0]
        if (f) {
            setFile(f)
            setError('')
            setResults([])
        }
    }, [])

    const handleDrop = useCallback((e) => {
        e.preventDefault()
        setDragOver(false)
        const f = e.dataTransfer.files?.[0]
        if (f) {
            setFile(f)
            setError('')
            setResults([])
        }
    }, [])

    const handleDragOver = useCallback((e) => {
        e.preventDefault()
        setDragOver(true)
    }, [])

    const handleDragLeave = useCallback(() => setDragOver(false), [])

    // ── 欄位管理 ──
    const addField = () => {
        setFields(prev => [...prev, { key: '', value: '' }])
    }

    const removeField = (index) => {
        setFields(prev => prev.filter((_, i) => i !== index))
    }

    const updateField = (index, prop, val) => {
        setFields(prev => {
            const updated = [...prev]
            updated[index] = { ...updated[index], [prop]: val }
            return updated
        })
    }

    const loadTemplate = () => {
        setFields(DEFAULT_FIELDS.map(f => ({ ...f })))
    }

    const clearFields = () => {
        setFields([{ key: '', value: '' }])
    }

    // ── 送出辨識 ──
    const handleSubmit = async () => {
        if (!file) {
            setError('請先上傳檔案')
            return
        }

        // 過濾空 key
        const validFields = fields.filter(f => f.key.trim())
        if (validFields.length === 0) {
            setError('請至少輸入一個欄位名稱')
            return
        }

        // 建立 fields dict
        const fieldsDict = {}
        validFields.forEach(f => { fieldsDict[f.key.trim()] = f.value })

        setProcessing(true)
        setProgress(0)
        setCurrentPage(0)
        setTotalPages(0)
        setResults([])
        setError('')

        try {
            const formData = new FormData()
            formData.append('file', file)
            formData.append('fields', JSON.stringify(fieldsDict))
            formData.append('model', model)
            formData.append('max_retries', String(maxRetries))

            const response = await fetchWithAuth('/api/ocr/process', {
                method: 'POST',
                body: formData,
            })

            if (!response.ok) {
                const errData = await response.json().catch(() => ({}))
                throw new Error(errData.detail || `HTTP ${response.status}`)
            }

            // SSE 讀取
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
                    try {
                        const data = JSON.parse(line.slice(6))

                        setProgress(data.percent || 0)
                        setCurrentPage(data.page || 0)
                        setTotalPages(data.total || 0)

                        if (data.error && data.done) {
                            setError(data.error)
                            break
                        }

                        if (data.done && data.all_results) {
                            setResults(data.all_results)
                        } else if (!data.done) {
                            // 即時更新中間結果
                            setResults(prev => {
                                const updated = [...prev]
                                updated.push(data)
                                return updated
                            })
                        }
                    } catch (e) {
                        // 忽略解析失敗的行
                    }
                }
            }
        } catch (e) {
            setError(e.message || '辨識失敗')
        } finally {
            setProcessing(false)
        }
    }

    // ── 複製/匯出 ──
    const copyAllResults = async () => {
        const allData = results
            .filter(r => r.success && r.data)
            .map(r => r.data)
        const text = JSON.stringify(allData.length === 1 ? allData[0] : allData, null, 2)
        try {
            await navigator.clipboard.writeText(text)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
        } catch { /* fallback */ }
    }

    const exportJSON = () => {
        const allData = results
            .filter(r => r.success && r.data)
            .map(r => r.data)
        const text = JSON.stringify(allData.length === 1 ? allData[0] : allData, null, 2)
        const blob = new Blob([text], { type: 'application/json' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `ocr_result_${Date.now()}.json`
        a.click()
        URL.revokeObjectURL(url)
    }

    // ── 渲染 ──
    return (
        <div className="fade-in">
            <div className="page-header">
                <h2>📄 OCR 辨識</h2>
                <p>上傳圖片或 PDF，使用 AI 視覺模型提取結構化資訊</p>
            </div>

            {/* ── 輸入區 ── */}
            {!processing && results.length === 0 && (
                <div className="ocr-layout">
                    {/* 左側 — 上傳 & 設定 */}
                    <div className="ocr-upload-section">
                        <div className="card">
                            <h3 className="ocr-section-title">📁 上傳檔案</h3>
                            <div
                                className={`upload-zone ${dragOver ? 'drag-over' : ''}`}
                                onClick={() => fileInputRef.current?.click()}
                                onDrop={handleDrop}
                                onDragOver={handleDragOver}
                                onDragLeave={handleDragLeave}
                            >
                                <input
                                    ref={fileInputRef}
                                    type="file"
                                    accept=".png,.jpg,.jpeg,.bmp,.tiff,.tif,.webp,.gif,.pdf"
                                    onChange={handleFileSelect}
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
                                        <span className="upload-hint">支援 PNG、JPG、BMP、TIFF、WEBP、GIF、PDF（最大 50MB）</span>
                                    </>
                                )}
                            </div>

                            {/* 模型 & 重試 */}
                            <div className="ocr-options-row">
                                <div className="form-group">
                                    <label className="form-label">模型名稱</label>
                                    <input
                                        type="text"
                                        className="form-input"
                                        value={model}
                                        onChange={e => setModel(e.target.value)}
                                        placeholder="glm-ocr"
                                    />
                                </div>
                                <div className="form-group">
                                    <label className="form-label">重試次數</label>
                                    <select
                                        className="form-select"
                                        value={maxRetries}
                                        onChange={e => setMaxRetries(Number(e.target.value))}
                                    >
                                        {[1, 2, 3, 4, 5].map(n => (
                                            <option key={n} value={n}>{n} 次</option>
                                        ))}
                                    </select>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* 右側 — 欄位編輯器 */}
                    <div className="ocr-fields-section">
                        <div className="card">
                            <div className="ocr-fields-header">
                                <h3 className="ocr-section-title">🏷️ 擷取欄位</h3>
                                <div className="ocr-fields-actions">
                                    <button className="btn btn-outline btn-sm" onClick={loadTemplate}>
                                        載入範本
                                    </button>
                                    <button className="btn btn-outline btn-sm" onClick={clearFields}>
                                        清空
                                    </button>
                                </div>
                            </div>
                            <p className="ocr-fields-hint">定義要從圖片中擷取的欄位名稱與預設值（預設值可留空）</p>

                            <div className="ocr-fields-list">
                                {fields.map((field, index) => (
                                    <div key={index} className="ocr-field-row">
                                        <input
                                            type="text"
                                            className="form-input ocr-field-key"
                                            placeholder="欄位名稱"
                                            value={field.key}
                                            onChange={e => updateField(index, 'key', e.target.value)}
                                        />
                                        <input
                                            type="text"
                                            className="form-input ocr-field-value"
                                            placeholder="預設值（選填）"
                                            value={field.value}
                                            onChange={e => updateField(index, 'value', e.target.value)}
                                        />
                                        <button
                                            className="btn btn-outline btn-sm ocr-field-remove"
                                            onClick={() => removeField(index)}
                                            title="移除此欄位"
                                        >
                                            ✕
                                        </button>
                                    </div>
                                ))}
                            </div>

                            <button className="btn btn-outline btn-sm" onClick={addField} style={{ marginTop: '12px' }}>
                                ➕ 新增欄位
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* 錯誤訊息 */}
            {error && !processing && (
                <div className="ocr-error">
                    <span>⚠️</span> {error}
                </div>
            )}

            {/* 開始辨識按鈕 */}
            {!processing && results.length === 0 && (
                <div style={{ marginTop: '24px' }}>
                    <button
                        className="btn btn-primary btn-lg"
                        onClick={handleSubmit}
                        disabled={!file || fields.filter(f => f.key.trim()).length === 0}
                    >
                        🔍 開始辨識
                    </button>
                </div>
            )}

            {/* ── 處理中 ── */}
            {processing && (
                <div className="card ocr-processing-card fade-in">
                    <div className="ocr-processing-icon">
                        <div className="spinner-lg"></div>
                    </div>
                    <h3>正在辨識中...</h3>
                    {totalPages > 0 && (
                        <p className="text-muted">
                            第 {currentPage} / {totalPages} 頁
                        </p>
                    )}
                    <div className="progress-bar-container" style={{ marginTop: '16px' }}>
                        <div className="progress-bar-fill processing" style={{ width: `${progress}%` }}></div>
                    </div>
                    <p className="text-muted" style={{ marginTop: '8px', fontSize: '0.85rem' }}>
                        {progress.toFixed(0)}%
                    </p>
                </div>
            )}

            {/* ── 結果展示 ── */}
            {!processing && results.length > 0 && (
                <div className="fade-in">
                    <div className="ocr-results-header">
                        <h3>辨識結果</h3>
                        <div className="ocr-results-actions">
                            <button className="btn btn-outline btn-sm" onClick={copyAllResults}>
                                {copied ? '✅ 已複製' : '📋 複製 JSON'}
                            </button>
                            <button className="btn btn-outline btn-sm" onClick={exportJSON}>
                                💾 匯出 JSON
                            </button>
                            <button className="btn btn-primary btn-sm" onClick={() => { setResults([]); setProgress(0); setError('') }}>
                                🔄 重新辨識
                            </button>
                        </div>
                    </div>

                    <div className="ocr-results-grid">
                        {results.map((r, i) => (
                            <div key={i} className={`card ocr-result-card ${r.success ? '' : 'ocr-result-error'}`}>
                                <div className="ocr-result-card-header">
                                    <span className={`badge ${r.success ? 'badge-completed' : 'badge-failed'}`}>
                                        {r.success ? '✅ 成功' : '❌ 失敗'}
                                    </span>
                                    <span className="text-muted" style={{ fontSize: '0.8rem' }}>
                                        第 {r.page} 頁 / 共 {r.total} 頁
                                    </span>
                                </div>
                                {r.success && r.data ? (
                                    <div className="ocr-result-data">
                                        {Object.entries(r.data).map(([key, val]) => (
                                            <div key={key} className="ocr-result-row">
                                                <span className="ocr-result-key">{key}</span>
                                                <span className="ocr-result-value">{val || '—'}</span>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="ocr-result-raw">
                                        <p className="text-muted" style={{ fontSize: '0.85rem' }}>
                                            {r.error || '無法解析回覆'}
                                        </p>
                                        {r.raw && (
                                            <pre className="ocr-raw-text">{r.raw}</pre>
                                        )}
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
