import { fetchWithAuth } from '../utils/api';
import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { ModelRequirement, useModelRequirement } from '../components/ModelStatus.jsx'
import { useModels } from '../context/ModelContext.jsx'
import MediaRangePlayer from '../components/MediaRangePlayer.jsx'

export default function NewTask() {
    const { findModelKey } = useModels()
    const [config, setConfig] = useState(null)
    const [configError, setConfigError] = useState('')
    const [file, setFile] = useState(null)
    const [model, setModel] = useState('')
    const [language, setLanguage] = useState('')
    const [diarization, setDiarization] = useState(true)
    const [traditional, setTraditional] = useState(true)
    const [submitting, setSubmitting] = useState(false)
    const [dragOver, setDragOver] = useState(false)
    const [error, setError] = useState('')
    const [mediaRange, setMediaRange] = useState({ start: 0, end: null, duration: null })
    const fileInputRef = useRef(null)
    const navigate = useNavigate()

    const loadConfig = () => {
        setConfigError('')
        fetchWithAuth('/api/config')
            .then(async res => {
                if (!res.ok) throw new Error(`HTTP ${res.status}`)
                return res.json()
            })
            .then(data => {
                setConfig(data)
                const modelKeys = Object.keys(data.models)
                if (modelKeys.length > 0) setModel(modelKeys[0])
                const langKeys = Object.keys(data.languages)
                if (langKeys.length > 0) setLanguage(langKeys[0])
            })
            .catch(err => setConfigError(`無法載入辨識設定：${err.message}`))
    }

    useEffect(() => {
        loadConfig()
    }, [])

    const selectedModelKey = config && model
        ? findModelKey(config.models[model]) || (model.includes('0.6B') ? 'asr_0.6b' : 'asr_1.7b')
        : null
    const requiredModels = [selectedModelKey, 'forced_aligner', diarization ? 'diarization' : null]
    const { blocked: modelsBlocked } = useModelRequirement(requiredModels)

    const handleDrop = (e) => {
        e.preventDefault()
        setDragOver(false)
        const droppedFile = e.dataTransfer.files[0]
        if (droppedFile) selectFile(droppedFile)
    }

    const selectFile = (nextFile) => {
        setFile(nextFile || null)
        setMediaRange({ start: 0, end: null, duration: null })
        setError('')
    }

    const handleSubmit = async () => {
        if (!file) {
            setError('請先選擇音訊或影片檔案')
            return
        }
        if (modelsBlocked) {
            setError('必要模型尚未就緒，請先透過 Manager 完成下載')
            return
        }
        setSubmitting(true)
        setError('')

        try {
            const formData = new FormData()
            formData.append('file', file)
            formData.append('model', model)
            formData.append('language', language)
            formData.append('enable_diarization', diarization)
            formData.append('to_traditional', traditional)
            const hasCustomRange = mediaRange.duration && (
                mediaRange.start > 0.01
                || mediaRange.end < mediaRange.duration - 0.01
            )
            if (hasCustomRange) {
                formData.append('start_time', mediaRange.start.toFixed(3))
                formData.append('end_time', mediaRange.end.toFixed(3))
            }

            const res = await fetchWithAuth('/api/tasks', {
                method: 'POST',
                body: formData,
            })
            if (!res.ok) {
                const err = await res.json()
                throw new Error(err.detail || '建立任務失敗')
            }

            const task = await res.json()
            navigate(`/tasks/${task.id}`)
        } catch (err) {
            setError(err.message)
            setSubmitting(false)
        }
    }

    if (!config) {
        return (
            <div className="empty-state page-state fade-in">
                {configError ? (
                    <>
                        <h2>無法載入辨識設定</h2>
                        <p>{configError}</p>
                        <button type="button" className="btn btn-primary" onClick={loadConfig}>重試</button>
                    </>
                ) : (
                    <>
                        <div className="spinner" style={{ width: 32, height: 32 }} />
                        <p>正在載入辨識設定</p>
                    </>
                )}
            </div>
        )
    }

    return (
        <div className="fade-in">
            <div className="page-header">
                <h2>➕ 新增任務</h2>
                <p>上傳音訊檔案並設定辨識參數</p>
            </div>

            <ModelRequirement modelKeys={requiredModels} title="語音辨識模型尚未就緒" />

            <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
                {/* 上傳區域 */}
                <div
                    className={`upload-zone ${dragOver ? 'drag-over' : ''}`}
                    onClick={() => fileInputRef.current?.click()}
                    onKeyDown={event => {
                        if (event.key === 'Enter' || event.key === ' ') fileInputRef.current?.click()
                    }}
                    onDrop={handleDrop}
                    onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
                    onDragLeave={() => setDragOver(false)}
                    role="button"
                    tabIndex="0"
                    aria-label="選擇或拖放音訊檔案"
                >
                    <input
                        type="file"
                        ref={fileInputRef}
                        style={{ display: 'none' }}
                        accept=".mp3,.wav,.m4a,.flac,.ogg,.wma,.aac,.mp4"
                        onChange={(e) => selectFile(e.target.files[0])}
                    />
                    {file ? (
                        <>
                            <div className="upload-icon">🎵</div>
                            <p className="file-selected">{file.name}</p>
                            <p className="upload-hint">
                                {(file.size / 1024 / 1024).toFixed(1)} MB · 點擊重新選擇
                            </p>
                        </>
                    ) : (
                        <>
                            <div className="upload-icon">📁</div>
                            <p>點擊選擇或拖放音訊檔案</p>
                            <p className="upload-hint">
                                支援 MP3, WAV, M4A, FLAC, OGG, AAC, MP4
                            </p>
                        </>
                    )}
                </div>
            </div>

            {file && (
                <div className="card media-selection-card" style={{ marginBottom: 'var(--space-lg)' }}>
                    <div className="media-selection-header">
                        <div>
                            <h3>🎧 試聽與辨識範圍</h3>
                            <p>拖曳波形上的左右界線或下方滑桿，指定這次要辨識的片段。</p>
                        </div>
                        {mediaRange.duration && (
                            <span className="media-selection-badge">
                                {mediaRange.start <= 0.01 && mediaRange.end >= mediaRange.duration - 0.01 ? '完整音訊' : '自訂片段'}
                            </span>
                        )}
                    </div>
                    <MediaRangePlayer
                        key={`${file.name}:${file.size}:${file.lastModified}`}
                        file={file}
                        range={mediaRange}
                        onRangeChange={setMediaRange}
                    />
                </div>
            )}

            {error && <div className="alert alert-error" role="alert">{error}</div>}

            {/* 設定面板 */}
            <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
                <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: 'var(--space-lg)', color: 'var(--color-text)' }}>
                    ⚙ 辨識設定
                </h3>
                <div className="form-grid">
                    <div className="form-group">
                        <label className="form-label">模型</label>
                        <select
                            className="form-select"
                            value={model}
                            onChange={(e) => setModel(e.target.value)}
                        >
                            {Object.keys(config.models).map(key => (
                                <option key={key} value={key}>{key}</option>
                            ))}
                        </select>
                    </div>

                    <div className="form-group">
                        <label className="form-label">語言</label>
                        <select
                            className="form-select"
                            value={language}
                            onChange={(e) => setLanguage(e.target.value)}
                        >
                            {Object.keys(config.languages).map(key => (
                                <option key={key} value={key}>{key}</option>
                            ))}
                        </select>
                    </div>

                    <div className="form-group full-width">
                        <label className="form-label">選項</label>
                        <div className="checkbox-group">
                            <label className="checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={diarization}
                                    onChange={(e) => setDiarization(e.target.checked)}
                                />
                                語者分離
                            </label>
                            <label className="checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={traditional}
                                    onChange={(e) => setTraditional(e.target.checked)}
                                />
                                繁體中文
                            </label>
                        </div>
                    </div>
                </div>
            </div>

            {/* 提交按鈕 */}
            <div style={{ display: 'flex', gap: 'var(--space-md)', justifyContent: 'flex-end' }}>
                <button className="btn btn-outline" onClick={() => navigate('/')}>
                    取消
                </button>
                <button
                    className="btn btn-primary btn-lg"
                    onClick={handleSubmit}
                    disabled={!file || submitting || modelsBlocked}
                    title={modelsBlocked ? '必要模型尚未下載' : undefined}
                >
                    {submitting ? (
                        <>
                            <span className="spinner" />
                            提交中...
                        </>
                    ) : (
                        '🚀 開始辨識'
                    )}
                </button>
            </div>
        </div>
    )
}
