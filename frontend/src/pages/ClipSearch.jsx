import { useState, useRef, useCallback } from 'react'
import { fetchWithAuth } from '../utils/api.js'
import { useNetwork } from '../context/NetworkContext.jsx'

export default function ClipSearch() {
    const { offline, isModelCached } = useNetwork()
    const clipMissing = offline && !isModelCached('clip')
    // 檔案
    const [pdfFile, setPdfFile] = useState(null)
    const [pdfDragOver, setPdfDragOver] = useState(false)
    const pdfInputRef = useRef(null)

    const [refImages, setRefImages] = useState([])
    const [imgDragOver, setImgDragOver] = useState(false)
    const imgInputRef = useRef(null)

    // 參數
    const [mustInclude, setMustInclude] = useState('')
    const [mustExclude, setMustExclude] = useState('')
    const [threshold, setThreshold] = useState(0.5)
    const [topK, setTopK] = useState(5)

    // 處理狀態
    const [processing, setProcessing] = useState(false)
    const [progress, setProgress] = useState(0)
    const [currentPage, setCurrentPage] = useState(0)
    const [totalPages, setTotalPages] = useState(0)
    const [statusText, setStatusText] = useState('')

    // 結果
    const [results, setResults] = useState([])
    const [error, setError] = useState('')
    const [hasSearched, setHasSearched] = useState(false)

    // ── PDF 處理 ──
    const handlePdfSelect = useCallback((e) => {
        const f = e.target.files?.[0]
        if (f) {
            setPdfFile(f)
            setError('')
            setResults([])
        }
    }, [])

    const handlePdfDrop = useCallback((e) => {
        e.preventDefault()
        setPdfDragOver(false)
        const f = e.dataTransfer.files?.[0]
        if (f && f.name.toLowerCase().endsWith('.pdf')) {
            setPdfFile(f)
            setError('')
            setResults([])
        } else {
            setError('請上傳 PDF 檔案')
        }
    }, [])

    // ── 參考圖片處理 ──
    const handleImgSelect = useCallback((e) => {
        const files = Array.from(e.target.files || [])
        if (files.length > 0) {
            setRefImages(prev => [...prev, ...files])
            setError('')
            setResults([])
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
            setError('')
            setResults([])
        }
    }, [])

    const removeImg = (index) => {
        setRefImages(prev => prev.filter((_, i) => i !== index))
    }

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

        setProcessing(true)
        setProgress(0)
        setCurrentPage(0)
        setTotalPages(0)
        setStatusText('準備中...')
        setResults([])
        setError('')
        setHasSearched(false)

        try {
            const formData = new FormData()
            formData.append('pdf_file', pdfFile)
            refImages.forEach(img => formData.append('ref_images', img))
            formData.append('must_include', mustInclude)
            formData.append('must_exclude', mustExclude)
            formData.append('threshold', String(threshold))
            formData.append('top_k', String(topK))

            const response = await fetchWithAuth('/api/clip-search/analyze', {
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
                            return
                        }

                        if (data.type === 'progress') {
                            setProgress(data.percent || 0)
                            setCurrentPage(data.page || 0)
                            setTotalPages(data.total || 0)
                            setStatusText(data.status || '')
                        } else if (data.type === 'results') {
                            setResults(data.data || [])
                            setTotalPages(data.total_pages || 0)
                        }
                    } catch (e) {
                        // ignore broken chunks
                    }
                }
            }
        } catch (e) {
            setError(e.message || '搜尋失敗')
        } finally {
            setProcessing(false)
            setHasSearched(true)
        }
    }

    return (
        <div className="fade-in clip-search-page">
            <div className="page-header">
                <h2>🔍 以圖搜頁</h2>
                <p>在 PDF 中以圖片搜尋最相似頁面（基於 CLIP 模型）</p>
            </div>

            {clipMissing && (
                <div className="model-warning">
                    <span className="warning-icon">⚠️</span>
                    <span>目前處於離線模式，CLIP 模型尚未下載至本機。請先在有網路的環境中啟動系統並執行一次搜尋以下載模型，之後即可離線使用。</span>
                </div>
            )}

            {!processing && results.length === 0 && !hasSearched && (
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
                                <input
                                    ref={pdfInputRef}
                                    type="file"
                                    accept=".pdf"
                                    onChange={handlePdfSelect}
                                    style={{ display: 'none' }}
                                />
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
                                <input
                                    ref={imgInputRef}
                                    type="file"
                                    accept="image/*"
                                    multiple
                                    onChange={handleImgSelect}
                                    style={{ display: 'none' }}
                                />
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

                    {/* 右側：搜尋條件 */}
                    <div className="clip-options-section">
                        <div className="card h-100">
                            <h3 className="clip-section-title">⚙️ 搜尋條件</h3>

                            <div className="form-group">
                                <label className="form-label">必要包含詞 (可選)</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={mustInclude}
                                    onChange={e => setMustInclude(e.target.value)}
                                    placeholder="以逗號分隔，如：cat, dog"
                                />
                                <span className="clip-hint">額外增加文字特徵加分（CLIP text-image）</span>
                            </div>

                            <div className="form-group">
                                <label className="form-label">不可包含詞 (可選)</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={mustExclude}
                                    onChange={e => setMustExclude(e.target.value)}
                                    placeholder="以逗號分隔，如：car, building"
                                />
                                <span className="clip-hint">扣減包含這些特徵的頁面分數</span>
                            </div>

                            <div className="form-group">
                                <label className="form-label">相似度閾值: {threshold}</label>
                                <input
                                    type="range"
                                    min="0"
                                    max="1"
                                    step="0.01"
                                    className="clip-slider"
                                    value={threshold}
                                    onChange={e => setThreshold(parseFloat(e.target.value))}
                                />
                                <span className="clip-hint">0 為最寬鬆，1 為嚴格完全相符（建議 0.2 ~ 0.5）</span>
                            </div>

                            <div className="form-group">
                                <label className="form-label">取前幾頁 (Top K)</label>
                                <input
                                    type="number"
                                    min="1"
                                    max="50"
                                    className="form-input"
                                    value={topK}
                                    onChange={e => setTopK(parseInt(e.target.value) || 5)}
                                />
                            </div>

                            {error && (
                                <div className="clip-error">
                                    <span>⚠️</span> {error}
                                </div>
                            )}

                            <div style={{ marginTop: '32px' }}>
                                <button
                                    className="btn btn-primary btn-lg"
                                    style={{ width: '100%' }}
                                    onClick={handleSubmit}
                                    disabled={!pdfFile || refImages.length === 0}
                                >
                                    🚀 開始搜尋
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* 沒有結果 */}
            {!processing && results.length === 0 && hasSearched && (
                <div className="card text-center fade-in" style={{ padding: '48px', marginTop: '24px' }}>
                    <div style={{ fontSize: '3rem', marginBottom: '16px' }}>🤷‍♂️</div>
                    <h3>沒有找到符合的結果</h3>
                    <p className="text-muted" style={{ marginBottom: '24px' }}>
                        請嘗試放寬相似度閾值、更換參考圖片，或修改必要包含詞。
                    </p>
                    <button className="btn btn-primary" onClick={() => setHasSearched(false)}>
                        🔄 重新搜尋
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

            {/* 結果 */}
            {!processing && results.length > 0 && (
                <div className="fade-in">
                    <div className="clip-results-header">
                        <h3>搜尋結果</h3>
                        <button className="btn btn-outline btn-sm" onClick={() => { setResults([]); setProgress(0); setError('') }}>
                            🔄 重新搜尋
                        </button>
                    </div>

                    <p className="text-muted" style={{ marginBottom: '16px' }}>在 {totalPages} 頁中找到 {results.length} 個符合的結果</p>

                    <div className="clip-results-grid">
                        {results.map((r, i) => (
                            <div key={i} className="card clip-result-card">
                                <div className="clip-result-header">
                                    <span className="clip-rank-badge">#{i + 1}</span>
                                    <span className="clip-page-badge">第 {r.page} 頁</span>
                                    <span className="clip-score-badge">相似度: {(r.similarity * 100).toFixed(1)}%</span>
                                </div>
                                <div className="clip-result-img-wrapper">
                                    <img src={`data:image/jpeg;base64,${r.image_base64}`} alt={`Page ${r.page}`} />
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    )
}
