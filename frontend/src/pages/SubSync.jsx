import { fetchWithAuth } from '../utils/api';
import { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useNetwork } from '../context/NetworkContext.jsx'

// ── 載入 YouTube IFrame API ──
let ytApiReady = false
let ytApiCallbacks = []

function loadYouTubeAPI() {
    if (ytApiReady) return Promise.resolve()
    if (window.YT && window.YT.Player) {
        ytApiReady = true
        return Promise.resolve()
    }

    return new Promise((resolve) => {
        ytApiCallbacks.push(resolve)
        if (!document.getElementById('yt-iframe-api')) {
            const tag = document.createElement('script')
            tag.id = 'yt-iframe-api'
            tag.src = 'https://www.youtube.com/iframe_api'
            document.head.appendChild(tag)
            window.onYouTubeIframeAPIReady = () => {
                ytApiReady = true
                ytApiCallbacks.forEach(cb => cb())
                ytApiCallbacks = []
            }
        }
    })
}

// ── 二分搜尋：找到當前時間對應的字幕索引 ──
function findActiveSubtitle(sentences, currentTime) {
    if (!sentences || sentences.length === 0) return -1
    let lo = 0, hi = sentences.length - 1
    let result = -1
    while (lo <= hi) {
        const mid = (lo + hi) >> 1
        if (sentences[mid].start <= currentTime) {
            result = mid
            lo = mid + 1
        } else {
            hi = mid - 1
        }
    }
    // 確認還在結束時間內
    if (result >= 0 && sentences[result].end < currentTime) {
        // 在兩句之間的間隙，還是顯示上一句
        if (result + 1 < sentences.length && sentences[result + 1].start > currentTime) {
            return result
        }
    }
    return result
}

// ── 格式化時間 ──
function formatTime(seconds) {
    const m = Math.floor(seconds / 60)
    const s = Math.floor(seconds % 60)
    return `${m}:${s.toString().padStart(2, '0')}`
}

export default function SubSync() {
    const { offline, isModelCached } = useNetwork()
    const asrMissing = offline && (!isModelCached('asr_1.7b') || !isModelCached('forced_aligner'))

    // ── 狀態 ──
    const [inputType, setInputType] = useState('youtube') // 'youtube' | 'local'
    const [url, setUrl] = useState('')
    const [file, setFile] = useState(null)
    const [model, setModel] = useState('1.7B (高品質)')
    const [language, setLanguage] = useState('中文')
    const [config, setConfig] = useState(null)
    const [phase, setPhase] = useState('input') // input | processing | ready
    const [taskId, setTaskId] = useState(null)
    const [videoId, setVideoId] = useState(null)
    const [videoTitle, setVideoTitle] = useState('')
    const [progress, setProgress] = useState(0)
    const [progressMessage, setProgressMessage] = useState('')
    const [sentences, setSentences] = useState([])
    const [activeIndex, setActiveIndex] = useState(-1)
    const [error, setError] = useState('')
    const [history, setHistory] = useState([])
    const [offset, setOffset] = useState(0)
    const [searchParams, setSearchParams] = useSearchParams()

    const [maxSentenceChars, setMaxSentenceChars] = useState(30)
    const [resegmenting, setResegmenting] = useState(false)

    // ── LLM 潤飾/翻譯狀態 ──
    const [llmProviders, setLlmProviders] = useState({})
    const [llmProvider, setLlmProvider] = useState('')
    const [llmModel, setLlmModel] = useState('')
    const [llmAction, setLlmAction] = useState('polish') // polish | translate
    const [llmProgress, setLlmProgress] = useState(-1) // -1 尚未開始, 0-100 進度
    const [llmError, setLlmError] = useState('')
    const [llmCustomPrompt, setLlmCustomPrompt] = useState('')
    const [showCustomPrompt, setShowCustomPrompt] = useState(false)
    const [editingIndex, setEditingIndex] = useState(-1)
    const [editValue, setEditValue] = useState('')

    const [temperature, setTemperature] = useState(0.3)
    const [maxTokens, setMaxTokens] = useState(1024)
    const [thinkingLevel, setThinkingLevel] = useState('medium')

    const playerRef = useRef(null)
    const playerContainerRef = useRef(null)
    const timerRef = useRef(null)
    const subtitlePanelRef = useRef(null)
    const activeLineRef = useRef(null)

    // ── 避免閉包陷阱的 Ref ──
    const sentencesRef = useRef([])
    const activeIndexRef = useRef(-1)
    const offsetRef = useRef(0)

    useEffect(() => {
        sentencesRef.current = sentences
    }, [sentences])

    useEffect(() => {
        activeIndexRef.current = activeIndex
    }, [activeIndex])

    useEffect(() => {
        offsetRef.current = offset
    }, [offset])

    // ── 載入配置與歷史紀錄 ──
    const fetchHistory = useCallback(async () => {
        try {
            const r = await fetchWithAuth('/api/tasks')
            if (r.ok) setHistory(await r.json())
        } catch { }
    }, [])

    const fetchLlmProviders = useCallback(async () => {
        try {
            const r = await fetchWithAuth('/api/llm/providers')
            if (r.ok) {
                const data = await r.json()
                setLlmProviders(data)
                const providers = Object.keys(data)
                if (providers.length > 0) {
                    setLlmProvider(providers[0])
                    setLlmModel(data[providers[0]][0] || '')
                }
            }
        } catch { }
    }, [])

    useEffect(() => {
        fetchWithAuth('/api/config')
            .then(r => r.json())
            .then(setConfig)
            .catch(() => { })
        fetchHistory()
        fetchLlmProviders()
    }, [fetchHistory, fetchLlmProviders])

    // ── URL 參數載入 ──
    useEffect(() => {
        const tid = searchParams.get('taskId')
        if (tid && phase === 'input') {
            fetchResult(tid)
        }
    }, [searchParams])

    // ── 銷毀 YouTube Player ──
    useEffect(() => {
        return () => {
            if (timerRef.current) clearInterval(timerRef.current)
            if (playerRef.current) {
                try { playerRef.current.destroy() } catch { }
            }
        }
    }, [])

    // ── 自動捲動到當前字幕 ──
    useEffect(() => {
        if (activeIndex >= 0 && activeLineRef.current && subtitlePanelRef.current) {
            const panel = subtitlePanelRef.current
            const line = activeLineRef.current
            const panelRect = panel.getBoundingClientRect()
            const lineRect = line.getBoundingClientRect()

            const lineCenter = lineRect.top + lineRect.height / 2
            const panelCenter = panelRect.top + panelRect.height / 2

            if (Math.abs(lineCenter - panelCenter) > panelRect.height * 0.3) {
                panel.scrollTo({
                    top: line.offsetTop - panel.offsetTop - panelRect.height / 2 + lineRect.height / 2,
                    behavior: 'smooth'
                })
            }
        }
    }, [activeIndex])

    // ── 提交分析 ──
    const handleSubmit = async () => {
        if (inputType === 'youtube' && !url.trim()) return
        if (inputType === 'local' && !file) return

        setError('')
        setPhase('processing')
        setProgress(0)
        setProgressMessage('提交中...')

        try {
            let resp
            if (inputType === 'youtube') {
                resp = await fetchWithAuth('/api/youtube/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, model, language }),
                })
            } else {
                const formData = new FormData()
                formData.append('file', file)
                formData.append('model', model)
                formData.append('language', language)

                const token = localStorage.getItem('token') || '';
                resp = await fetchWithAuth(`/api/youtube/analyze/upload?token=${token}`, {
                    method: 'POST',
                    body: formData,
                })
            }

            if (!resp.ok) {
                const err = await resp.json()
                throw new Error(err.detail || '提交失敗')
            }
            const data = await resp.json()
            setTaskId(data.id)
            setVideoId(data.video_id)
            setSearchParams({ taskId: data.id })
            fetchHistory()

            // 開始監聽 SSE 進度
            listenProgress(data.id)
        } catch (e) {
            setError(e.message)
            setPhase('input')
        }
    }

    // ── SSE 進度監聽 ──
    const listenProgress = (tid) => {
        const token = localStorage.getItem('token') || '';
        const evtSource = new EventSource(`/api/tasks/${tid}/progress?token=${token}`)
        evtSource.onmessage = (event) => {
            const data = JSON.parse(event.data)
            setProgress(data.percent || 0)
            setProgressMessage(data.message || '')

            if (data.done) {
                evtSource.close()
                if (data.percent >= 100) {
                    // 載入完整結果
                    fetchResult(tid)
                } else {
                    setError(data.message || '處理失敗')
                    setPhase('input')
                }
            }
        }
        evtSource.onerror = () => {
            evtSource.close()
            // 嘗試 fallback 查詢
            fetchResult(tid)
        }
    }

    const fetchResult = async (tid) => {
        try {
            const resp = await fetchWithAuth(`/api/tasks/${tid}`)
            if (!resp.ok) {
                if (resp.status === 404) throw new Error('任務不存在或已被刪除')
                throw new Error('載入結果失敗')
            }
            const data = await resp.json()
            if (data.status === 'completed' && data.sentences) {
                setSentences(data.sentences)
                setVideoTitle(data.video_title || '')
                setVideoId(data.video_id)
                setTaskId(tid)
                setPhase('ready')
                fetchHistory() // 更新歷史狀態
                // 初始化 YouTube 播放器
                initPlayer(data.video_id, data.task_type)
            } else if (data.status === 'failed') {
                setError(data.error_message || '處理失敗')
                setPhase('input')
            } else {
                // 仍在處理中，等待
                setTaskId(tid)
                setPhase('processing')
                if (data.progress !== undefined) setProgress(data.progress)
                if (data.progress_message !== undefined) setProgressMessage(data.progress_message)
                setTimeout(() => fetchResult(tid), 2000)
            }
        } catch (e) {
            setError(e.message || '無法載入結果')
            setPhase('input')
            setTaskId(null)
            fetchHistory()
        }
    }

    // ── 初始化 YouTube Player ──
    const initPlayer = async (vid, taskType) => {
        if (vid && vid.startsWith('local_')) {
            // Local video/audio rendering
            if (playerContainerRef.current) {
                playerContainerRef.current.innerHTML = ''
                const videoEl = document.createElement('video')
                videoEl.controls = true
                videoEl.style.width = '100%'
                videoEl.style.height = '100%'
                videoEl.style.backgroundColor = '#000'

                if (taskType === 'local') {
                    videoEl.src = `/api/tasks/media/${vid}`
                } else {
                    videoEl.src = `/api/youtube/media/${vid}`
                }

                playerRef.current = {
                    getCurrentTime: () => videoEl.currentTime,
                    seekTo: (time) => { videoEl.currentTime = time },
                    destroy: () => {
                        videoEl.pause()
                        videoEl.src = ''
                    },
                    isLocal: true
                }

                videoEl.addEventListener('play', startTimeSync)
                videoEl.addEventListener('pause', () => {
                    stopTimeSync()
                    syncSubtitle()
                })
                videoEl.addEventListener('ended', () => {
                    stopTimeSync()
                    syncSubtitle()
                })

                playerContainerRef.current.appendChild(videoEl)
                videoEl.load()
            }
            return
        }

        await loadYouTubeAPI()

        if (playerRef.current) {
            try { playerRef.current.destroy() } catch { }
        }

        playerRef.current = new window.YT.Player('yt-player', {
            videoId: vid,
            width: '100%',
            height: '100%',
            playerVars: {
                autoplay: 0,
                modestbranding: 1,
                rel: 0,
            },
            events: {
                onReady: () => {
                    startTimeSync()
                },
                onStateChange: (event) => {
                    if (event.data === window.YT.PlayerState.PLAYING) {
                        startTimeSync()
                    } else if (event.data === window.YT.PlayerState.PAUSED ||
                        event.data === window.YT.PlayerState.ENDED) {
                        stopTimeSync()
                        // 最後同步一次
                        syncSubtitle()
                    }
                }
            }
        })
    }

    // ── 時間同步 ──
    const syncSubtitle = useCallback(() => {
        if (!playerRef.current || !playerRef.current.getCurrentTime) return
        try {
            const currentTime = playerRef.current.getCurrentTime()
            const idx = findActiveSubtitle(sentencesRef.current, currentTime - offsetRef.current)
            if (idx !== activeIndexRef.current) {
                setActiveIndex(idx)
            }
        } catch { }
    }, [])

    const startTimeSync = useCallback(() => {
        if (timerRef.current) clearInterval(timerRef.current)
        timerRef.current = setInterval(syncSubtitle, 200)
    }, [syncSubtitle])

    const stopTimeSync = () => {
        if (timerRef.current) {
            clearInterval(timerRef.current)
            timerRef.current = null
        }
    }

    // ── 點擊字幕跳轉 ──
    const handleSubtitleClick = (startTime) => {
        if (playerRef.current && playerRef.current.seekTo) {
            playerRef.current.seekTo(startTime, true)
            syncSubtitle()
        }
    }

    // ── 重新開始 ──
    const handleReset = () => {
        stopTimeSync()
        if (playerRef.current) {
            try { playerRef.current.destroy() } catch { }
            playerRef.current = null
        }
        if (playerContainerRef.current && !history.some(h => h.video_id === videoId && h.video_id.startsWith('local_'))) {
            playerContainerRef.current.innerHTML = '<div id="yt-player"></div>'
        } else if (playerContainerRef.current) {
            playerContainerRef.current.innerHTML = '<div id="yt-player"></div>'
        }
        setPhase('input')
        setUrl('')
        setFile(null)
        setTaskId(null)
        setVideoId(null)
        setVideoTitle('')
        setSentences([])
        setActiveIndex(-1)
        setProgress(0)
        setProgressMessage('')
        setError('')
        setSearchParams({})
        fetchHistory()
    }

    // ── LLM 執行邏輯 ──
    const handleLlmProcess = async () => {
        if (!taskId || !llmProvider || !llmModel) return
        setLlmProgress(0)
        setLlmError('')
        setEditingIndex(-1)

        try {
            const resp = await fetchWithAuth('/api/llm/process', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_id: taskId,
                    provider: llmProvider,
                    model: llmModel,
                    action: llmAction,
                    custom_prompt: showCustomPrompt ? llmCustomPrompt : "",
                    temperature: parseFloat(temperature),
                    max_tokens: parseInt(maxTokens, 10),
                    thinking_level: thinkingLevel
                })
            })

            if (!resp.ok) throw new Error('啟動 LLM 處理失敗')

            // Instead of dealing with chunk parsing manually which can be complex,
            // We use EventSource mechanism or fetch reader
            const reader = resp.body.getReader()
            const decoder = new TextDecoder()

            let buffer = ""
            while (true) {
                const { value, done } = await reader.read()
                if (done) break
                buffer += decoder.decode(value, { stream: true })

                // Parse SSE
                const parts = buffer.split('\n\n')
                buffer = parts.pop() || ""

                for (const part of parts) {
                    if (part.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(part.substring(6))
                            setLlmProgress(data.percent)

                            if (data.done) {
                                if (data.sentences) {
                                    setSentences(data.sentences)
                                }
                                if (data.cancelled) {
                                    setLlmError('已中斷處理')
                                }
                                setTimeout(() => setLlmProgress(-1), 1000)
                            } else {
                                // Update specific sentence in place
                                setSentences(prev => {
                                    const next = [...prev]
                                    const curr = next[data.index]
                                    next[data.index] = {
                                        ...curr,
                                        original_text: curr.original_text !== undefined ? curr.original_text : curr.text,
                                        text: data.processed_text
                                    }
                                    return next
                                })
                            }
                        } catch (e) { console.error('SSE JSON parse error', e) }
                    }
                }
            }
        } catch (e) {
            setLlmError(e.message)
            setLlmProgress(-1)
        }
    }

    // ── 中斷 LLM 處理 ──
    const handleLlmCancel = async () => {
        if (!taskId) return
        try {
            await fetchWithAuth('/api/llm/cancel', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: taskId, task_type: 'asr' })
            })
        } catch (e) { console.error('中斷失敗', e) }
    }

    // ── 匯出處理 ──
    const handleExport = (format) => {
        if (!taskId) return
        const token = localStorage.getItem('token') || '';
        window.open(`/api/tasks/${taskId}/export/${format}?variant=subtitle&token=${token}`, '_blank')
    }

    // ── 保存最終字幕 ──
    const handleSaveSentences = async () => {
        if (!taskId) return
        try {
            const resp = await fetchWithAuth('/api/llm/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_id: taskId,
                    task_type: 'asr',
                    sentences: sentences
                })
            })
            if (resp.ok) {
                alert('字幕儲存成功！')
                fetchHistory()
            }
        } catch { }
    }

    // ── 手動微調處理 ──
    const startEdit = (idx, currentText) => {
        setEditingIndex(idx)
        setEditValue(currentText)
    }

    const saveEdit = (idx) => {
        setSentences(prev => {
            const next = [...prev]
            if (next[idx].original_text === undefined && next[idx].text !== editValue) {
                next[idx].original_text = next[idx].text
            }
            next[idx].text = editValue
            return next
        })
        setEditingIndex(-1)
    }

    const handleRevertEdit = (e, idx) => {
        e.stopPropagation()
        setSentences(prev => {
            const next = [...prev]
            if (next[idx].original_text !== undefined) {
                next[idx].text = next[idx].original_text
                delete next[idx].original_text
            }
            return next
        })
    }

    const handleRevertAll = () => {
        if (!confirm('確定要復原所有字幕到最原始狀態嗎？此操作無法撤銷。')) return
        setSentences(prev => {
            return prev.map(s => {
                if (s.original_text !== undefined) {
                    const newS = { ...s, text: s.original_text }
                    delete newS.original_text
                    return newS
                }
                return s
            })
        })
    }

    // ── 去除標點符號 ──
    const [punctSpaceReplace, setPunctSpaceReplace] = useState(false)

    const handleRemovePunctuation = async (mode) => {
        if (!taskId) return
        try {
            const resp = await fetchWithAuth(`/api/tasks/${taskId}/remove-punctuation`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode, replace_with_space: punctSpaceReplace })
            })
            if (resp.ok) {
                const data = await resp.json()
                setSentences(data.sentences)
            }
        } catch (e) {
            console.error('去除標點失敗', e)
        }
    }

    const handleRevertPunctuation = async () => {
        const reverted = sentences.map(s => {
            if (s.original_text !== undefined) {
                return { ...s, text: s.original_text, original_text: undefined }
            }
            return s
        }).map(s => {
            const { original_text, ...rest } = s
            return rest
        })
        setSentences(reverted)
        try {
            await fetchWithAuth('/api/llm/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: taskId, task_type: 'asr', sentences: reverted })
            })
        } catch (e) {
            console.error('儲存復原失敗', e)
        }
    }
    const handleDeleteHistory = async (e, id) => {
        e.stopPropagation()
        if (!confirm('確定要刪除此紀錄？')) return
        try {
            await fetchWithAuth(`/api/tasks/${id}`, { method: 'DELETE' })
            fetchHistory()
            if (taskId === id) handleReset()
        } catch { }
    }

    // ── 重新分句處理 ──
    const handleResegment = async () => {
        if (!taskId) return;
        setResegmenting(true);
        try {
            const resp = await fetchWithAuth(`/api/youtube/${taskId}/resegment`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    max_sentence_chars: parseInt(maxSentenceChars, 10),
                    force_cut_chars: parseInt(maxSentenceChars, 10) + 20
                })
            });
            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || '重新分句失敗');
            }
            const data = await resp.json();
            setSentences(data.sentences);
        } catch (e) {
            alert(e.message);
        } finally {
            setResegmenting(false);
        }
    };

    // ============================
    // 渲染
    // ============================

    return (
        <div className="fade-in">
            <div className="page-header">
                <h2>🎬 SubSync</h2>
                <p>影音字幕同步器 — 輸入 YouTube 網址，AI 自動產生同步字幕</p>
            </div>

            {asrMissing && (
                <div className="model-warning">
                    <span className="warning-icon">⚠️</span>
                    <span>目前處於離線模式，ASR 模型尚未下載至本機。請先在有網路的環境中啟動系統並執行一次辨識以下載模型，之後即可離線使用。</span>
                </div>
            )}

            {/* ── 輸入階段 ── */}
            {phase === 'input' && (
                <div className="card subsync-input-card fade-in">
                    <div className="subsync-input-header">
                        <div className="subsync-input-icon">🎬</div>
                        <div>
                            <h3>匯入影音</h3>
                            <p className="text-muted">支援 YouTube 網址或上傳本地影音檔案</p>
                        </div>
                    </div>

                    <div className="subsync-input-tabs" style={{ display: 'flex', gap: '8px', marginBottom: '1rem', borderBottom: '1px solid var(--color-border)', paddingBottom: '8px' }}>
                        <button
                            className={`btn btn-sm ${inputType === 'youtube' ? 'btn-primary' : 'btn-ghost'}`}
                            onClick={() => setInputType('youtube')}
                        >
                            🔗 YouTube 網址
                        </button>
                        <button
                            className={`btn btn-sm ${inputType === 'local' ? 'btn-primary' : 'btn-ghost'}`}
                            onClick={() => setInputType('local')}
                        >
                            📁 上傳影音檔案
                        </button>
                    </div>

                    <div className="subsync-url-row">
                        {inputType === 'youtube' ? (
                            <input
                                id="youtube-url-input"
                                type="text"
                                className="form-input subsync-url-input"
                                placeholder="https://www.youtube.com/watch?v=..."
                                value={url}
                                onChange={e => setUrl(e.target.value)}
                                onKeyDown={e => e.key === 'Enter' && handleSubmit()}
                            />
                        ) : (
                            <input
                                id="local-file-input"
                                type="file"
                                className="form-input subsync-url-input"
                                accept="audio/*,video/*"
                                onChange={e => setFile(e.target.files[0])}
                                style={{ padding: '8px' }}
                            />
                        )}
                        <button
                            id="subsync-start-btn"
                            className="btn btn-accent btn-lg"
                            onClick={handleSubmit}
                            disabled={inputType === 'youtube' ? !url.trim() : !file}
                        >
                            🚀 開始分析
                        </button>
                    </div>

                    {config && (
                        <div className="subsync-options">
                            <div className="form-group">
                                <label className="form-label">模型</label>
                                <select
                                    className="form-select"
                                    value={model}
                                    onChange={e => setModel(e.target.value)}
                                >
                                    {Object.keys(config.models).map(m => (
                                        <option key={m} value={m}>{m}</option>
                                    ))}
                                </select>
                            </div>
                            <div className="form-group">
                                <label className="form-label">語言</label>
                                <select
                                    className="form-select"
                                    value={language}
                                    onChange={e => setLanguage(e.target.value)}
                                >
                                    {Object.keys(config.languages).map(l => (
                                        <option key={l} value={l}>{l}</option>
                                    ))}
                                </select>
                            </div>
                        </div>
                    )}

                    {error && (
                        <div className="subsync-error fade-in">
                            <span>⚠️</span> {error}
                        </div>
                    )}

                    {history.length > 0 && (
                        <div className="subsync-history-section fade-in">
                            <h4 style={{ margin: '2rem 0 1rem 0' }}>📅 近期處理紀錄</h4>
                            <div className="subsync-history-list">
                                {history.map(t => (
                                    <div
                                        key={t.id}
                                        className="card card-clickable subsync-history-item"
                                        onClick={() => {
                                            setSearchParams({ taskId: t.id })
                                            fetchResult(t.id)
                                        }}
                                    >
                                        <div className="history-info">
                                            <div style={{ fontWeight: 500 }} className="text-truncate">
                                                {t.task_type === 'youtube' ? '📺 ' : (t.task_type === 'subsync_upload' ? '📁 ' : '🎵 ')}
                                                {t.filename || t.video_title}
                                            </div>
                                            <div className="text-muted" style={{ fontSize: '0.85rem', marginTop: 4 }}>
                                                {new Date(t.created_at).toLocaleString()} ·
                                                <span style={{ marginLeft: 4, fontWeight: 600, color: t.status === 'completed' ? 'var(--color-primary)' : t.status === 'failed' ? 'var(--color-danger)' : 'var(--color-accent)' }}>
                                                    {t.status === 'completed' ? '✅ 完成' : t.status === 'failed' ? '❌ 失敗' : '⏳ 處理中'}
                                                </span>
                                            </div>
                                        </div>
                                        <button
                                            className="btn btn-outline btn-sm"
                                            onClick={(e) => handleDeleteHistory(e, t.id)}
                                            title="刪除"
                                        >
                                            🗑
                                        </button>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* ── 處理中 ── */}
            {phase === 'processing' && (
                <div className="card subsync-processing-card fade-in">
                    <div className="subsync-processing-icon">
                        <div className="spinner-lg"></div>
                    </div>
                    <h3>正在分析影片</h3>
                    <p className="text-muted">{progressMessage || '處理中...'}</p>

                    <div className="progress-bar-container subsync-progress">
                        <div
                            className="progress-bar-fill processing"
                            style={{ width: `${progress}%` }}
                        ></div>
                    </div>
                    <span className="subsync-progress-pct">{Math.round(progress)}%</span>

                    <div className="subsync-steps">
                        <div className={`subsync-step ${progress >= 1 ? 'active' : ''} ${progress >= 20 ? 'done' : ''}`}>
                            <span className="step-dot"></span>
                            <span>下載音頻</span>
                        </div>
                        <div className={`subsync-step ${progress >= 20 ? 'active' : ''} ${progress >= 90 ? 'done' : ''}`}>
                            <span className="step-dot"></span>
                            <span>AI 語音辨識</span>
                        </div>
                        <div className={`subsync-step ${progress >= 90 ? 'active' : ''} ${progress >= 100 ? 'done' : ''}`}>
                            <span className="step-dot"></span>
                            <span>產生字幕</span>
                        </div>
                    </div>

                    {error && (
                        <div className="subsync-error fade-in">
                            <span>⚠️</span> {error}
                            <button className="btn btn-outline btn-sm" onClick={handleReset}>重新開始</button>
                        </div>
                    )}
                </div>
            )}

            {/* ── 播放 + 字幕 ── */}
            {phase === 'ready' && (
                <div className="subsync-player-layout fade-in">
                    <div className="subsync-top-bar">
                        <h3 className="subsync-video-title">
                            {videoTitle || '影片'}
                        </h3>
                        <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                            <div className="subsync-offset-control" style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'var(--color-surface-hover)', padding: '4px 10px', borderRadius: 'var(--radius-sm)' }}>
                                <label style={{ fontSize: '0.85rem', color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>時間微調 (秒)</label>
                                <input
                                    type="number"
                                    step="0.1"
                                    className="form-input"
                                    style={{ width: '70px', padding: '2px 8px', height: '28px', fontSize: '0.9rem' }}
                                    value={offset}
                                    onChange={e => setOffset(parseFloat(e.target.value) || 0)}
                                    title="正數讓字幕延後，負數讓字幕提前"
                                />
                            </div>
                            <div className="subsync-offset-control" style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'var(--color-surface-hover)', padding: '4px 10px', borderRadius: 'var(--radius-sm)' }}>
                                <label style={{ fontSize: '0.85rem', color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>最大分句字數</label>
                                <input
                                    type="number"
                                    min="10"
                                    max="150"
                                    className="form-input"
                                    style={{ width: '60px', padding: '2px 8px', height: '28px', fontSize: '0.9rem' }}
                                    value={maxSentenceChars}
                                    onChange={e => setMaxSentenceChars(e.target.value)}
                                    title="設定自動斷句的長度限制"
                                />
                                <button
                                    className="btn btn-outline btn-sm"
                                    style={{ padding: '2px 8px', height: '28px', fontSize: '0.8rem' }}
                                    onClick={handleResegment}
                                    disabled={resegmenting}
                                >
                                    {resegmenting ? '處理中' : '套用'}
                                </button>
                            </div>
                            <button className="btn btn-outline btn-sm" onClick={handleReset}>
                                ← 新分析
                            </button>
                        </div>
                    </div>

                    <div className="subsync-player-container">
                        {/* 影片播放區域與提詞機 */}
                        <div className="subsync-video-section">
                            <div className="subsync-video-wrapper" ref={playerContainerRef}>
                                <div id="yt-player"></div>
                            </div>

                            {/* 重點提詞區域 (Teleprompter) */}
                            <div className="subsync-teleprompter">
                                <div className="teleprompter-line line-prev">
                                    {activeIndex > 0 && sentences[activeIndex - 1] ? sentences[activeIndex - 1].text : '\u00A0'}
                                </div>
                                <div className="teleprompter-line line-curr">
                                    {activeIndex >= 0 && sentences[activeIndex] ? sentences[activeIndex].text : '準備播放...'}
                                </div>
                                <div className="teleprompter-line line-next">
                                    {activeIndex >= 0 && activeIndex < sentences.length - 1 && sentences[activeIndex + 1] ? sentences[activeIndex + 1].text : '\u00A0'}
                                </div>
                            </div>
                        </div>

                        {/* 字幕列表面板 */}
                        <div className="subsync-subtitle-panel" ref={subtitlePanelRef}>
                            <div className="subsync-subtitle-header" style={{ flexDirection: 'column', alignItems: 'stretch', gap: '8px' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                        <span className="subsync-subtitle-badge">📝 AI 字幕 (可雙擊微調)</span>
                                        {taskId && (
                                            <div style={{ display: 'flex', gap: '4px' }}>
                                                <button className="btn btn-outline btn-sm" style={{ padding: '2px 8px', fontSize: '0.75rem' }} onClick={() => handleExport('srt')} title="匯出 SRT 格式">
                                                    ⬇️ .srt
                                                </button>
                                                <button className="btn btn-outline btn-sm" style={{ padding: '2px 8px', fontSize: '0.75rem' }} onClick={() => handleExport('txt')} title="匯出 TXT 格式">
                                                    ⬇️ .txt
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                    <span className="subsync-subtitle-count">
                                        共 {sentences.length} 句
                                    </span>
                                </div>

                                {/* ── LLM 工具列 ── */}
                                <div className="subsync-llm-toolbar">
                                    <div className="subsync-llm-group">
                                        <label className="form-label" style={{ marginBottom: 2 }}>供應商</label>
                                        <select className="form-select" style={{ padding: '4px 8px', fontSize: '0.8rem' }}
                                            value={llmProvider} onChange={e => {
                                                setLlmProvider(e.target.value)
                                                setLlmModel(llmProviders[e.target.value]?.[0] || '')
                                            }}>
                                            {Object.keys(llmProviders).map(p => <option key={p} value={p}>{p}</option>)}
                                        </select>
                                    </div>
                                    <div className="subsync-llm-group">
                                        <label className="form-label" style={{ marginBottom: 2 }}>模型</label>
                                        <select className="form-select" style={{ padding: '4px 8px', fontSize: '0.8rem' }}
                                            value={llmModel} onChange={e => setLlmModel(e.target.value)}>
                                            {(llmProviders[llmProvider] || []).map(m => <option key={m} value={m}>{m}</option>)}
                                        </select>
                                    </div>
                                    <div className="subsync-llm-group">
                                        <label className="form-label" style={{ marginBottom: 2 }}>動作</label>
                                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                            <select className="form-select" style={{ padding: '4px 8px', fontSize: '0.8rem', width: 'auto' }}
                                                value={llmAction} onChange={e => setLlmAction(e.target.value)}>
                                                <option value="polish">✨ 語意潤飾</option>
                                                <option value="translate">🌐 翻譯繁中</option>
                                            </select>
                                            <button
                                                className={`btn btn-sm ${showCustomPrompt ? 'btn-accent' : 'btn-outline'}`}
                                                style={{ padding: '4px 8px', fontSize: '0.75rem' }}
                                                onClick={() => setShowCustomPrompt(!showCustomPrompt)}
                                                title="自訂提示詞"
                                            >
                                                ✍️ 自訂
                                            </button>
                                        </div>
                                    </div>
                                    <div className="subsync-llm-group">
                                        <label className="form-label" style={{ marginBottom: 2 }} title="數值越高越有創意 (0.0~2.0)">溫度</label>
                                        <input type="number" step="0.1" min="0" max="2" className="form-input" style={{ width: '60px', padding: '4px 8px', fontSize: '0.8rem' }}
                                            value={temperature} onChange={e => setTemperature(e.target.value)} />
                                    </div>
                                    <div className="subsync-llm-group">
                                        <label className="form-label" style={{ marginBottom: 2 }} title="長度限制或思考長度">最大輸出</label>
                                        <input type="number" step="100" min="10" className="form-input" style={{ width: '70px', padding: '4px 8px', fontSize: '0.8rem' }}
                                            value={maxTokens} onChange={e => setMaxTokens(e.target.value)} />
                                    </div>
                                    <div className="subsync-llm-group">
                                        <label className="form-label" style={{ marginBottom: 2 }} title="支援思考等級之模型 (如 o1) 或傳給 Ollama 做參考">思考等級</label>
                                        <select className="form-select" style={{ padding: '4px 8px', fontSize: '0.8rem' }}
                                            value={thinkingLevel} onChange={e => setThinkingLevel(e.target.value)}>
                                            <option value="low">低</option>
                                            <option value="medium">中</option>
                                            <option value="high">高</option>
                                        </select>
                                    </div>
                                    <div className="subsync-llm-actions">
                                        <button className="btn btn-accent btn-sm"
                                            disabled={llmProgress >= 0 || !llmModel}
                                            onClick={handleLlmProcess}>
                                            執行
                                        </button>
                                        {llmProgress >= 0 && (
                                            <button className="btn btn-danger btn-sm"
                                                onClick={handleLlmCancel}>
                                                ⏹ 中斷
                                            </button>
                                        )}
                                        {sentences.some(s => s.original_text !== undefined) && (
                                            <button className="btn btn-outline btn-sm"
                                                onClick={handleRevertAll}
                                                title="復原所有修改到原始字幕">
                                                ↩ 全部復原
                                            </button>
                                        )}
                                        <button className="btn btn-primary btn-sm"
                                            onClick={handleSaveSentences}
                                            disabled={llmProgress >= 0}>
                                            💾 儲存
                                        </button>
                                    </div>

                                    {/* 自訂提示詞輸入區 */}
                                    {showCustomPrompt && (
                                        <div style={{ width: '100%', marginTop: '4px' }}>
                                            <textarea
                                                className="form-input"
                                                placeholder={llmAction === 'polish' ?
                                                    "請輸入自訂潤飾提示詞，例如：請幫我將這段字幕改成幽默的語氣，保留原意。" :
                                                    "請輸入自訂翻譯提示詞，例如：將以下字幕翻譯成繁體中文，請使用台灣本土流行語。"
                                                }
                                                style={{ width: '100%', minHeight: '60px', fontSize: '0.85rem', resize: 'vertical' }}
                                                value={llmCustomPrompt}
                                                onChange={e => setLlmCustomPrompt(e.target.value)}
                                            />
                                        </div>
                                    )}

                                    {llmProgress >= 0 && (
                                        <div className="subsync-llm-progress-bar">
                                            <div className="subsync-llm-progress-fill" style={{ width: `${llmProgress}%` }}></div>
                                        </div>
                                    )}
                                    {llmError && <div style={{ color: 'var(--color-danger)', fontSize: '0.8rem', width: '100%' }}>{llmError}</div>}
                                </div>

                                {/* ── 去除標點工具列 ── */}
                                <div style={{ display: 'flex', gap: '8px', alignItems: 'center', padding: '6px 10px', background: 'var(--color-bg)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--color-border)', flexWrap: 'wrap' }}>
                                    <span style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--color-text-muted)' }}>標點</span>
                                    <button className="btn btn-outline btn-sm" style={{ fontSize: '0.72rem', padding: '2px 6px' }}
                                        onClick={() => handleRemovePunctuation('all')}
                                        disabled={llmProgress >= 0}>
                                        去除全部
                                    </button>
                                    <button className="btn btn-outline btn-sm" style={{ fontSize: '0.72rem', padding: '2px 6px' }}
                                        onClick={() => handleRemovePunctuation('sentence_end')}
                                        disabled={llmProgress >= 0}>
                                        去除句末
                                    </button>
                                    <label style={{ fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '4px', color: 'var(--color-text-muted)', cursor: 'pointer' }}>
                                        <input type="checkbox" checked={punctSpaceReplace} onChange={e => setPunctSpaceReplace(e.target.checked)} />
                                        以空格替換
                                    </label>
                                    {sentences.some(s => s.original_text !== undefined) && (
                                        <button className="btn btn-outline btn-sm" style={{ fontSize: '0.72rem', padding: '2px 6px', color: 'var(--color-primary)', borderColor: 'var(--color-primary)' }}
                                            onClick={handleRevertPunctuation}
                                            disabled={llmProgress >= 0}>
                                            ↩ 復原標點
                                        </button>
                                    )}
                                </div>
                            </div>

                            <div className="subsync-subtitle-list">
                                {sentences.map((s, i) => {
                                    const isModified = s.original_text !== undefined && s.original_text !== s.text

                                    return (
                                        <div
                                            key={i}
                                            ref={i === activeIndex ? activeLineRef : null}
                                            className={`subsync-subtitle-line ${i === activeIndex ? 'active' : ''}`}
                                            onClick={() => handleSubtitleClick(s.start)}
                                            onDoubleClick={() => startEdit(i, s.text)}
                                        >
                                            <span className="subsync-subtitle-time">
                                                {formatTime(s.start)}
                                            </span>

                                            {editingIndex === i ? (
                                                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
                                                    <textarea
                                                        className="subsync-subtitle-text-editor"
                                                        value={editValue}
                                                        onChange={e => setEditValue(e.target.value)}
                                                        onBlur={() => saveEdit(i)}
                                                        autoFocus
                                                        onClick={e => e.stopPropagation()}
                                                    />
                                                </div>
                                            ) : (
                                                <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                                                    {isModified && (
                                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                                            <span className="subsync-subtitle-text-original">
                                                                {s.original_text}
                                                            </span>
                                                            <button
                                                                className="btn btn-sm btn-outline"
                                                                style={{ padding: '2px 6px', fontSize: '0.7rem', color: 'var(--color-primary)', borderColor: 'var(--color-primary)' }}
                                                                onClick={(e) => handleRevertEdit(e, i)}
                                                                title="復原為原始字幕"
                                                            >
                                                                ↩ 復原
                                                            </button>
                                                        </div>
                                                    )}
                                                    <span className="subsync-subtitle-text" style={{ marginTop: isModified ? '2px' : '0' }}>
                                                        {s.text}
                                                    </span>
                                                </div>
                                            )}
                                        </div>
                                    )
                                })}
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}
