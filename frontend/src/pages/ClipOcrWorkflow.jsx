import { useState, useRef, useCallback } from 'react'
import { fetchWithAuth } from '../utils/api.js'

const DEFAULT_FIELDS = [
    { key: '製作日期' },
    { key: '報告編號' },
    { key: '報告類別' },
    { key: '申請者名稱' },
    { key: '申請者地址' },
    { key: '申請法規項目名稱' },
    { key: '廠牌' },
    { key: '製造廠地址' },
    { key: '型式系列/型式系列編號' },
    { key: '型式名稱/型式編號' },
]

export default function ClipOcrWorkflow() {
    // 檔案
    const [pdfFile, setPdfFile] = useState(null)
    const [pdfDragOver, setPdfDragOver] = useState(false)
    const pdfInputRef = useRef(null)

    const [refImages, setRefImages] = useState([])
    const [imgDragOver, setImgDragOver] = useState(false)
    const imgInputRef = useRef(null)

    // CLIP 參數
    const [threshold, setThreshold] = useState(0.5)
    const [mustInclude, setMustInclude] = useState('')
    const [mustExclude, setMustExclude] = useState('')

    // OCR 參數
    const [fields, setFields] = useState(() => DEFAULT_FIELDS.map(f => ({ ...f })))
    const [model, setModel] = useState('glm-ocr')
    const [maxRetries, setMaxRetries] = useState(3)

    // 狀態
    const [processing, setProcessing] = useState(false)
    const [progress, setProgress] = useState(0)
    const [currentPage, setCurrentPage] = useState(0)
    const [totalPages, setTotalPages] = useState(0)
    const [statusText, setStatusText] = useState('')

    // 結果
    const [workflowResult, setWorkflowResult] = useState(null)
    const [error, setError] = useState('')
    const [copied, setCopied] = useState(false)
    const [hasSearched, setHasSearched] = useState(false)

    // ── PDF 處理 ──
    const handlePdfSelect = useCallback((e) => {
        const f = e.target.files?.[0]
        if (f) {
            setPdfFile(f)
            resetState()
        }
    }, [])
    const handlePdfDrop = useCallback((e) => {
        e.preventDefault()
        setPdfDragOver(false)
        const f = e.dataTransfer.files?.[0]
        if (f && f.name.toLowerCase().endsWith('.pdf')) {
            setPdfFile(f)
            resetState()
        } else {
            setError('請上傳 PDF 檔案')
        }
    }, [])

    // ── 參考圖片處理 ──
    const handleImgSelect = useCallback((e) => {
        const files = Array.from(e.target.files || [])
        if (files.length > 0) {
            setRefImages(prev => [...prev, ...files])
            resetState()
        }
    }, [])
    const handleImgDrop = useCallback((e) => {
        e.preventDefault()
        setImgDragOver(false)
        const files = Array.from(e.dataTransfer.files || []).filter(
            f => f.type.startsWith('image/')
        )
        if (files.length > 0) {
            setRefImages(prev => [...prev, ...files])
            resetState()
        }
    }, [])
    const removeImg = (index) => {
        setRefImages(prev => prev.filter((_, i) => i !== index))
    }

    const resetState = () => {
        setError('')
        setWorkflowResult(null)
        setHasSearched(false)
    }

    // ── 欄位管理 ──
    const addField = () => setFields(prev => [...prev, { key: '' }])
    const removeField = (index) => setFields(prev => prev.filter((_, i) => i !== index))
    const updateField = (index, val) => {
        setFields(prev => {
            const updated = [...prev]
            updated[index] = { key: val }
            return updated
        })
    }
    const loadTemplate = () => setFields(DEFAULT_FIELDS.map(f => ({ ...f })))
    const clearFields = () => setFields([{ key: '' }])

    // ── 送出 ──
    const handleSubmit = async () => {
        if (!pdfFile) {
            setError('請先上傳 PDF 檔案')
            return
        }
        if (refImages.length === 0) {
            setError('請至少上傳一張參考圖片')
            return
        }

        const validFields = fields.filter(f => f.key.trim())
        if (validFields.length === 0) {
            setError('請至少輸入一個欄位名稱')
            return
        }

        let fieldsDict = {}
        validFields.forEach(f => { fieldsDict[f.key.trim()] = '' })

        setProcessing(true)
        setProgress(0)
        setCurrentPage(0)
        setTotalPages(0)
        setStatusText('準備中...')
        setWorkflowResult(null)
        setError('')
        setHasSearched(false)

        try {
            const formData = new FormData()
            formData.append('pdf_file', pdfFile)
            refImages.forEach(img => formData.append('ref_images', img))
            formData.append('must_include', mustInclude)
            formData.append('must_exclude', mustExclude)
            formData.append('threshold', String(threshold))
            formData.append('fields', JSON.stringify(fieldsDict))
            formData.append('model', model)
            formData.append('max_retries', String(maxRetries))

            const response = await fetchWithAuth('/api/workflow/clip-ocr-top1', {
                method: 'POST',
                body: formData,
            })

            if (!response.ok) {
                const errData = await response.json().catch(() => ({}))
                throw new Error(errData.detail || `HTTP ${response.status}`)
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
                    try {
                        const data = JSON.parse(line.slice(6))

                        if (data.type === 'error') {
                            setError(data.error)
                            setProcessing(false)
                            setHasSearched(true)
                            return
                        }

                        if (data.type === 'progress') {
                            setProgress(data.percent || 0)
                            setCurrentPage(data.page || 0)
                            setTotalPages(data.total || 0)
                            setStatusText(data.status || '')
                        } else if (data.type === 'workflow_result') {
                            setWorkflowResult(data)
                        }
                    } catch (e) {
                        // ignore broken chunks
                    }
                }
            }
        } catch (e) {
            setError(e.message || '處理失敗')
        } finally {
            setProcessing(false)
            setHasSearched(true)
        }
    }

    // ── 複製/匯出 ──
    const copyResults = async () => {
        if (!workflowResult || !workflowResult.ocr_success) return
        const text = JSON.stringify(workflowResult.ocr_data, null, 2)
        try {
            await navigator.clipboard.writeText(text)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
        } catch { /* empty */ }
    }

    const exportJSON = () => {
        if (!workflowResult || !workflowResult.ocr_success) return
        const text = JSON.stringify(workflowResult.ocr_data, null, 2)
        const blob = new Blob([text], { type: 'application/json' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `clip_ocr_result_${workflowResult.page}.json`
        a.click()
        URL.revokeObjectURL(url)
    }

    return (
        <div className="fade-in clip-search-page">
            <div className="page-header">
                <h2>🛠️ 以圖擷取 (Clip + OCR)</h2>
                <p>上傳 PDF 與參考圖，找出最相似的單頁並自動擷取欄位資料</p>
            </div>

            {!processing && !workflowResult && !hasSearched && (
                <div className="clip-layout">
                    {/* 左側：PDF 與參考圖上傳 */}
                    <div className="clip-upload-section">
                        <div className="card" style={{ marginBottom: '16px' }}>
                            <h3 className="clip-section-title">📄 PDF 檔案</h3>
                            <div
                                className={`upload-zone ${pdfDragOver ? 'drag-over' : ''}`}
                                onClick={() => pdfInputRef.current?.click()}
                                onDrop={handlePdfDrop}
                                onDragOver={(e) => { e.preventDefault(); setPdfDragOver(true) }}
                                onDragLeave={() => setPdfDragOver(false)}
                                style={{ padding: '24px 16px' }}
                            >
                                <input ref={pdfInputRef} type="file" accept=".pdf" onChange={handlePdfSelect} style={{ display: 'none' }} />
                                {pdfFile ? (
                                    <div className="file-selected">
                                        <span className="upload-icon" style={{ fontSize: '1.8rem', marginBottom: '8px' }}>📎</span>
                                        <p>{pdfFile.name}</p>
                                        <span className="upload-hint">{(pdfFile.size / 1024 / 1024).toFixed(2)} MB</span>
                                    </div>
                                ) : (
                                    <>
                                        <div className="upload-icon" style={{ fontSize: '1.8rem', marginBottom: '8px' }}>📤</div>
                                        <p>上傳目標 PDF（最大 100MB）</p>
                                    </>
                                )}
                            </div>
                        </div>

                        <div className="card">
                            <h3 className="clip-section-title">🖼️ 參考圖片</h3>
                            <div
                                className={`upload-zone ${imgDragOver ? 'drag-over' : ''}`}
                                onClick={() => imgInputRef.current?.click()}
                                onDrop={handleImgDrop}
                                onDragOver={(e) => { e.preventDefault(); setImgDragOver(true) }}
                                onDragLeave={() => setImgDragOver(false)}
                                style={{ padding: '24px 16px', marginBottom: refImages.length > 0 ? '16px' : '0' }}
                            >
                                <input ref={imgInputRef} type="file" accept="image/*" multiple onChange={handleImgSelect} style={{ display: 'none' }} />
                                <div className="upload-icon" style={{ fontSize: '1.8rem', marginBottom: '8px' }}>📸</div>
                                <p>選擇或拖放圖片做為搜尋依據</p>
                            </div>

                            {refImages.length > 0 && (
                                <div className="clip-img-preview-list">
                                    {refImages.map((img, idx) => (
                                        <div key={idx} className="clip-img-preview-item">
                                            <img src={URL.createObjectURL(img)} alt={`preview-${idx}`} />
                                            <button className="clip-img-remove-btn" onClick={(e) => { e.stopPropagation(); removeImg(idx) }}>✕</button>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>

                    {/* 右側：搜尋條件與 OCR 欄位 */}
                    <div className="clip-options-section">
                        <div className="card h-100">
                            <h3 className="clip-section-title">⚙️ 擷取條件</h3>

                            {/* 欄位 */}
                            <div className="ocr-fields-header" style={{ marginTop: '16px' }}>
                                <label className="form-label" style={{ marginBottom: 0 }}>🏷️ 擷取欄位</label>
                                <div className="ocr-fields-actions">
                                    <button className="btn btn-outline btn-sm" onClick={loadTemplate}>載入範本</button>
                                    <button className="btn btn-outline btn-sm" onClick={clearFields}>清空</button>
                                </div>
                            </div>
                            <div className="ocr-fields-list" style={{ marginTop: '8px', maxHeight: '200px', overflowY: 'auto', paddingRight: '4px' }}>
                                {fields.map((field, index) => (
                                    <div key={index} className="ocr-field-row" style={{ marginBottom: '8px' }}>
                                        <input
                                            type="text"
                                            className="form-input ocr-field-key"
                                            placeholder="欄位名稱"
                                            value={field.key}
                                            onChange={e => updateField(index, e.target.value)}
                                        />
                                        <button className="btn btn-outline btn-sm ocr-field-remove" onClick={() => removeField(index)}>✕</button>
                                    </div>
                                ))}
                            </div>
                            <button className="btn btn-outline btn-sm" onClick={addField} style={{ marginTop: '8px', marginBottom: '24px' }}>
                                ➕ 新增欄位
                            </button>

                            {/* 參數設定 */}
                            <div className="form-group">
                                <label className="form-label">必要包含詞 (可選)</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={mustInclude}
                                    onChange={e => setMustInclude(e.target.value)}
                                    placeholder="以逗號分隔，如：cat, dog"
                                />
                            </div>

                            <div className="form-group" style={{ marginTop: '12px' }}>
                                <label className="form-label">不可包含詞 (可選)</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={mustExclude}
                                    onChange={e => setMustExclude(e.target.value)}
                                    placeholder="以逗號分隔，如：car, building"
                                />
                            </div>

                            <div className="form-group" style={{ marginTop: '12px' }}>
                                <label className="form-label">相似度閾值: {threshold}</label>
                                <input type="range" min="0" max="1" step="0.01" className="clip-slider" value={threshold} onChange={e => setThreshold(parseFloat(e.target.value))} />
                                <span className="clip-hint">0 為最寬鬆，1 為嚴格完全相符</span>
                            </div>

                            <div className="ocr-options-row" style={{ marginTop: '16px' }}>
                                <div className="form-group">
                                    <label className="form-label">選擇模型</label>
                                    <input type="text" className="form-input" value={model} onChange={e => setModel(e.target.value)} placeholder="glm-ocr" />
                                </div>
                                <div className="form-group">
                                    <label className="form-label">重試次數</label>
                                    <select className="form-select" value={maxRetries} onChange={e => setMaxRetries(Number(e.target.value))}>
                                        {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n} 次</option>)}
                                    </select>
                                </div>
                            </div>

                            {error && (
                                <div className="clip-error" style={{ marginTop: '16px' }}>
                                    <span>⚠️</span> {error}
                                </div>
                            )}

                            <div style={{ marginTop: '32px' }}>
                                <button className="btn btn-primary btn-lg" style={{ width: '100%' }} onClick={handleSubmit} disabled={!pdfFile || refImages.length === 0}>
                                    🚀 執行以圖擷取
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* 沒有結果 */}
            {!processing && !workflowResult && hasSearched && (
                <div className="card text-center fade-in" style={{ padding: '48px', marginTop: '24px' }}>
                    <div style={{ fontSize: '3rem', marginBottom: '16px' }}>🤷‍♂️</div>
                    <h3>沒有找到符合的結果</h3>
                    <p className="text-muted" style={{ marginBottom: '24px' }}>
                        請嘗試放寬相似度閾值或更換參考圖片。
                    </p>
                    <button className="btn btn-primary" onClick={() => setHasSearched(false)}>
                        🔄 重新擷取
                    </button>
                </div>
            )}

            {/* 處理中 */}
            {processing && (
                <div className="card clip-processing-card fade-in">
                    <div className="clip-processing-icon">
                        <div className="spinner-lg"></div>
                    </div>
                    <h3>{statusText || '處理中...'}</h3>
                    {totalPages > 0 && (
                        <p className="text-muted">
                            掃描進度: {currentPage} / {totalPages} 頁
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

            {/* 結果 */}
            {!processing && workflowResult && (
                <div className="fade-in">
                    <div className="clip-results-header">
                        <h3>擷取結果</h3>
                        <div className="ocr-results-actions">
                            <button className="btn btn-outline btn-sm" onClick={copyResults} disabled={!workflowResult.ocr_success}>
                                {copied ? '✅ 已複製' : '📋 複製 JSON'}
                            </button>
                            <button className="btn btn-outline btn-sm" onClick={exportJSON} disabled={!workflowResult.ocr_success}>
                                💾 匯出 JSON
                            </button>
                            <button className="btn btn-primary btn-sm" onClick={resetState}>
                                🔄 重新擷取
                            </button>
                        </div>
                    </div>

                    <p className="text-muted" style={{ marginBottom: '16px' }}>
                        最佳目標為 PDF 第 {workflowResult.page} 頁 (相似度: {(workflowResult.similarity * 100).toFixed(1)}%)
                    </p>

                    <div className="clip-layout">
                        {/* 左側放圖片 */}
                        <div className="clip-upload-section">
                            <div className="card clip-result-card" style={{ height: '100%' }}>
                                <div className="clip-result-img-wrapper">
                                    <img src={`data:image/jpeg;base64,${workflowResult.image_base64}`} alt={`Page ${workflowResult.page}`} />
                                </div>
                            </div>
                        </div>

                        {/* 右側放 OCR 結果 */}
                        <div className="clip-options-section">
                            <div className={`card h-100 ocr-result-card ${workflowResult.ocr_success ? '' : 'ocr-result-error'}`}>
                                <div className="ocr-result-card-header" style={{ marginBottom: '16px' }}>
                                    <span className={`badge ${workflowResult.ocr_success ? 'badge-completed' : 'badge-failed'}`}>
                                        {workflowResult.ocr_success ? '✅ OCR 成功' : '❌ OCR 失敗'}
                                    </span>
                                </div>

                                {workflowResult.ocr_success && workflowResult.ocr_data ? (
                                    <div className="ocr-result-data">
                                        {Object.entries(workflowResult.ocr_data).map(([key, val]) => (
                                            <div key={key} className="ocr-result-row">
                                                <span className="ocr-result-key">{key}</span>
                                                <span className="ocr-result-value">{val || '—'}</span>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="ocr-result-raw">
                                        {!workflowResult.ocr_success && (
                                            <p className="text-muted" style={{ fontSize: '0.85rem', marginBottom: '8px' }}>
                                                {workflowResult.ocr_error || '無法解析回覆'}
                                            </p>
                                        )}
                                        {workflowResult.ocr_raw && (
                                            <pre className="ocr-raw-text">{workflowResult.ocr_raw}</pre>
                                        )}
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}
