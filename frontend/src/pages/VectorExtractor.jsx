import { useState, useRef } from 'react'
import { fetchWithAuth } from '../utils/api.js'

export default function VectorExtractor() {
    const [imageFile, setImageFile] = useState(null)
    const [imagePreview, setImagePreview] = useState(null)
    const [result, setResult] = useState(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')

    // For drag and drop UI
    const [isDragging, setIsDragging] = useState(false)
    const fileInputRef = useRef(null)

    const handleFileChange = (e) => {
        const file = e.target.files[0]
        if (file) {
            setupFile(file)
        }
    }

    const setupFile = (file) => {
        setImageFile(file)
        setError('')
        setResult(null)

        const previewUrl = URL.createObjectURL(file)
        setImagePreview(previewUrl)
    }

    const handleDragOver = (e) => {
        e.preventDefault()
        setIsDragging(true)
    }

    const handleDragLeave = () => {
        setIsDragging(false)
    }

    const handleDrop = (e) => {
        e.preventDefault()
        setIsDragging(false)
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            setupFile(e.dataTransfer.files[0])
        }
    }

    const handleExtract = async () => {
        if (!imageFile) {
            setError('請先上傳圖片')
            return
        }

        setLoading(true)
        setError('')
        setResult(null)

        try {
            const formData = new FormData()
            formData.append('image_file', imageFile)

            const res = await fetchWithAuth('/api/clip-search/extract-feature-file', {
                method: 'POST',
                body: formData,
            })

            const data = await res.json()
            if (!res.ok) {
                throw new Error(data.detail || data.error || '特徵擷取請求失敗')
            }

            if (!data.success) {
                throw new Error(data.error || '特徵擷取回傳失敗')
            }

            setResult(data)

        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    const copyToClipboard = () => {
        if (result && result.vector) {
            navigator.clipboard.writeText(JSON.stringify(result.vector))
                .then(() => alert('已複製 512 維數值至剪貼簿'))
                .catch(err => alert('複製失敗: ' + err))
        }
    }

    return (
        <div className="page-container">
            <header className="page-header">
                <h2> CLIP 特徵擷取</h2>
                <p>上傳單張圖片，獲取 512 維度之特徵向量（已執行 L2 正規化）</p>
            </header>

            <main className="page-content">
                {error && <div className="alert alert-error mb-4">{error}</div>}

                <div className="card mb-4" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

                    <div
                        className={`upload-zone ${isDragging ? 'drag-over' : ''}`}
                        onDragOver={handleDragOver}
                        onDragLeave={handleDragLeave}
                        onDrop={handleDrop}
                        onClick={() => fileInputRef.current?.click()}
                        style={{
                            border: '2px dashed var(--color-border)',
                            borderRadius: '8px',
                            padding: '3rem',
                            textAlign: 'center',
                            cursor: 'pointer',
                            backgroundColor: isDragging ? 'var(--color-bg-alt)' : 'transparent',
                            transition: 'all 0.2s',
                            position: 'relative'
                        }}
                    >
                        <input
                            type="file"
                            accept="image/*"
                            ref={fileInputRef}
                            style={{ display: 'none' }}
                            onChange={handleFileChange}
                        />

                        {imagePreview ? (
                            <div style={{ maxHeight: '300px', overflow: 'hidden' }}>
                                <img
                                    src={imagePreview}
                                    alt="Preview"
                                    style={{
                                        maxWidth: '100%',
                                        maxHeight: '300px',
                                        objectFit: 'contain',
                                        borderRadius: '4px'
                                    }}
                                />
                                <div style={{ marginTop: '1rem', color: 'var(--color-text-muted)' }}>
                                    點擊或拖曳即可更換圖片
                                </div>
                            </div>
                        ) : (
                            <div>
                                <div style={{ fontSize: '3rem', marginBottom: '1rem', color: 'var(--color-text-muted)' }}>🖼️</div>
                                <h4>點擊或拖曳圖片至此</h4>
                                <p style={{ color: 'var(--color-text-muted)' }}>支援 JPG, PNG 等格式</p>
                            </div>
                        )}
                    </div>
                </div>

                <div className="actions mb-4" style={{ display: 'flex', justifyContent: 'center' }}>
                    <button
                        className="btn btn-primary"
                        onClick={handleExtract}
                        disabled={loading || !imageFile}
                        style={{ minWidth: '200px', padding: '0.75rem 1.5rem', fontSize: '1.1rem' }}
                    >
                        {loading ? '擷取中...' : '開始提取向量 (Extract)'}
                    </button>
                </div>

                {result && result.success && (
                    <div className="card">
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                            <h3 style={{ margin: 0 }}>特徵向量 (維度: {result.vector_dimension})</h3>
                            <button className="btn btn-outline btn-sm" onClick={copyToClipboard}>
                                📋 複製完整陣列 (JSON)
                            </button>
                        </div>

                        <div style={{
                            background: '#1e1e1e',
                            color: '#d4d4d4',
                            padding: '1rem',
                            borderRadius: '6px',
                            fontFamily: 'monospace',
                            fontSize: '0.9rem',
                            maxHeight: '300px',
                            overflowY: 'auto',
                            wordBreak: 'break-all'
                        }}>
                            [ {result.vector.map(v => v.toFixed(6)).join(', ')} ]
                        </div>
                    </div>
                )}
            </main>
        </div>
    )
}
