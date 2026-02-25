import { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

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
    // ── 狀態 ──
    const [url, setUrl] = useState('')
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
    const [searchParams, setSearchParams] = useSearchParams()

    const playerRef = useRef(null)
    const playerContainerRef = useRef(null)
    const timerRef = useRef(null)
    const subtitlePanelRef = useRef(null)
    const activeLineRef = useRef(null)

    // ── 避免閉包陷阱的 Ref ──
    const sentencesRef = useRef([])
    const activeIndexRef = useRef(-1)

    useEffect(() => {
        sentencesRef.current = sentences
    }, [sentences])

    useEffect(() => {
        activeIndexRef.current = activeIndex
    }, [activeIndex])

    // ── 載入配置與歷史紀錄 ──
    const fetchHistory = useCallback(async () => {
        try {
            const r = await fetch('/api/youtube')
            if (r.ok) setHistory(await r.json())
        } catch { }
    }, [])

    useEffect(() => {
        fetch('/api/config')
            .then(r => r.json())
            .then(setConfig)
            .catch(() => { })
        fetchHistory()
    }, [fetchHistory])

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
        if (!url.trim()) return
        setError('')
        setPhase('processing')
        setProgress(0)
        setProgressMessage('提交中...')

        try {
            const resp = await fetch('/api/youtube/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, model, language }),
            })
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
        const evtSource = new EventSource(`/api/youtube/${tid}/progress`)
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

    // ── 載入結果 ──
    const fetchResult = async (tid) => {
        try {
            const resp = await fetch(`/api/youtube/${tid}`)
            const data = await resp.json()
            if (data.status === 'completed' && data.sentences) {
                setSentences(data.sentences)
                setVideoTitle(data.video_title || '')
                setVideoId(data.video_id)
                setTaskId(tid)
                setPhase('ready')
                fetchHistory() // 更新歷史狀態
                // 初始化 YouTube 播放器
                initPlayer(data.video_id)
            } else if (data.status === 'failed') {
                setError(data.error_message || '處理失敗')
                setPhase('input')
            } else {
                // 仍在處理中，等待
                setTaskId(tid)
                setPhase('processing')
                setTimeout(() => fetchResult(tid), 2000)
            }
        } catch {
            setError('無法載入結果')
            setPhase('input')
        }
    }

    // ── 初始化 YouTube Player ──
    const initPlayer = async (vid) => {
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
            const idx = findActiveSubtitle(sentencesRef.current, currentTime)
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
        setPhase('input')
        setUrl('')
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

    // ── 刪除歷史紀錄 ──
    const handleDeleteHistory = async (e, id) => {
        e.stopPropagation()
        if (!confirm('確定要刪除此紀錄？')) return
        try {
            await fetch(`/api/youtube/${id}`, { method: 'DELETE' })
            fetchHistory()
            if (taskId === id) handleReset()
        } catch { }
    }

    // ============================
    // 渲染
    // ============================

    return (
        <div className="fade-in">
            <div className="page-header">
                <h2>🎬 SubSync</h2>
                <p>影音字幕同步器 — 輸入 YouTube 網址，AI 自動產生同步字幕</p>
            </div>

            {/* ── 輸入階段 ── */}
            {phase === 'input' && (
                <div className="card subsync-input-card fade-in">
                    <div className="subsync-input-header">
                        <div className="subsync-input-icon">📺</div>
                        <div>
                            <h3>輸入 YouTube 影片網址</h3>
                            <p className="text-muted">支援 youtube.com/watch、youtu.be、shorts 等格式</p>
                        </div>
                    </div>

                    <div className="subsync-url-row">
                        <input
                            id="youtube-url-input"
                            type="text"
                            className="form-input subsync-url-input"
                            placeholder="https://www.youtube.com/watch?v=..."
                            value={url}
                            onChange={e => setUrl(e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && handleSubmit()}
                        />
                        <button
                            id="subsync-start-btn"
                            className="btn btn-accent btn-lg"
                            onClick={handleSubmit}
                            disabled={!url.trim()}
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
                                                {t.video_title || t.video_id}
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
                        <button className="btn btn-outline btn-sm" onClick={handleReset}>
                            ← 新分析
                        </button>
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
                                    {activeIndex > 0 ? sentences[activeIndex - 1]?.text : '\u00A0'}
                                </div>
                                <div className="teleprompter-line line-curr">
                                    {activeIndex >= 0 && sentences[activeIndex] ? sentences[activeIndex].text : '準備播放...'}
                                </div>
                                <div className="teleprompter-line line-next">
                                    {activeIndex >= 0 && activeIndex < sentences.length - 1 ? sentences[activeIndex + 1]?.text : '\u00A0'}
                                </div>
                            </div>
                        </div>

                        {/* 字幕列表面板 */}
                        <div className="subsync-subtitle-panel" ref={subtitlePanelRef}>
                            <div className="subsync-subtitle-header">
                                <span className="subsync-subtitle-badge">📝 AI 字幕</span>
                                <span className="subsync-subtitle-count">
                                    共 {sentences.length} 句
                                </span>
                            </div>
                            <div className="subsync-subtitle-list">
                                {sentences.map((s, i) => (
                                    <div
                                        key={i}
                                        ref={i === activeIndex ? activeLineRef : null}
                                        className={`subsync-subtitle-line ${i === activeIndex ? 'active' : ''}`}
                                        onClick={() => handleSubtitleClick(s.start)}
                                    >
                                        <span className="subsync-subtitle-time">
                                            {formatTime(s.start)}
                                        </span>
                                        <span className="subsync-subtitle-text">
                                            {s.text}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}
