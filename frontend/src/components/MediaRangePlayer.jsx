import { useEffect, useRef, useState } from 'react'

const WAVEFORM_MAX_BYTES = 128 * 1024 * 1024
const WAVEFORM_MAX_DURATION_SECONDS = 30 * 60
const WAVEFORM_BARS = 180

function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max)
}

function formatTime(seconds) {
    if (!Number.isFinite(seconds)) return '00:00.0'
    const safeSeconds = Math.max(0, seconds)
    const hours = Math.floor(safeSeconds / 3600)
    const minutes = Math.floor((safeSeconds % 3600) / 60)
    const secs = safeSeconds % 60
    const prefix = hours > 0 ? `${String(hours).padStart(2, '0')}:` : ''
    return `${prefix}${String(minutes).padStart(2, '0')}:${secs.toFixed(1).padStart(4, '0')}`
}

export default function MediaRangePlayer({ file, range, onRangeChange }) {
    const mediaRef = useRef(null)
    const waveformRef = useRef(null)
    const draggingBoundaryRef = useRef(null)
    const [duration, setDuration] = useState(0)
    const [currentTime, setCurrentTime] = useState(0)
    const [isPlaying, setIsPlaying] = useState(false)
    const [playingSelection, setPlayingSelection] = useState(false)
    const [volume, setVolume] = useState(1)
    const [muted, setMuted] = useState(false)
    const [playbackRate, setPlaybackRate] = useState(1)
    const [waveform, setWaveform] = useState([])
    const [waveformStatus, setWaveformStatus] = useState('loading')
    const [draggingBoundary, setDraggingBoundary] = useState(null)

    const [objectUrl, setObjectUrl] = useState('')
    const isVideo = file.type.startsWith('video/') || /\.mp4$/i.test(file.name)
    const MediaElement = isVideo ? 'video' : 'audio'
    const rangeStart = clamp(Number(range.start) || 0, 0, duration || 0)
    const rangeEnd = clamp(Number.isFinite(range.end) ? range.end : duration, 0, duration || 0)
    const minimumSelection = Math.min(0.1, duration)

    useEffect(() => {
        const nextUrl = URL.createObjectURL(file)
        setDuration(0)
        setCurrentTime(0)
        setIsPlaying(false)
        setPlayingSelection(false)
        setObjectUrl(nextUrl)
        return () => URL.revokeObjectURL(nextUrl)
    }, [file])

    useEffect(() => {
        let cancelled = false
        let audioContext = null
        setWaveform([])

        if (!duration) {
            setWaveformStatus('loading')
            return () => { cancelled = true }
        }
        if (file.size > WAVEFORM_MAX_BYTES) {
            setWaveformStatus('large')
            return () => { cancelled = true }
        }
        if (duration > WAVEFORM_MAX_DURATION_SECONDS) {
            setWaveformStatus('long')
            return () => { cancelled = true }
        }

        setWaveformStatus('loading')
        const buildWaveform = async () => {
            try {
                const AudioContextClass = window.AudioContext || window.webkitAudioContext
                if (!AudioContextClass) throw new Error('Web Audio API unavailable')
                audioContext = new AudioContextClass()
                const arrayBuffer = await file.arrayBuffer()
                const audioBuffer = await audioContext.decodeAudioData(arrayBuffer.slice(0))
                const channel = audioBuffer.getChannelData(0)
                const blockSize = Math.max(1, Math.floor(channel.length / WAVEFORM_BARS))
                const peaks = []
                for (let index = 0; index < WAVEFORM_BARS; index += 1) {
                    const start = index * blockSize
                    const end = Math.min(channel.length, start + blockSize)
                    const sampleStride = Math.max(1, Math.floor(blockSize / 128))
                    let peak = 0
                    for (let sample = start; sample < end; sample += sampleStride) {
                        peak = Math.max(peak, Math.abs(channel[sample]))
                    }
                    peaks.push(peak)
                }
                const maximum = Math.max(...peaks, 0.001)
                if (!cancelled) {
                    setWaveform(peaks.map(peak => peak / maximum))
                    setWaveformStatus('ready')
                }
            } catch {
                if (!cancelled) setWaveformStatus('unsupported')
            } finally {
                if (audioContext) audioContext.close().catch(() => {})
            }
        }

        buildWaveform()
        return () => { cancelled = true }
    }, [file, duration])

    useEffect(() => {
        const media = mediaRef.current
        if (!media) return
        media.volume = volume
        media.muted = muted
        media.playbackRate = playbackRate
    }, [volume, muted, playbackRate])

    useEffect(() => {
        if (!isPlaying || !playingSelection) return undefined
        let animationFrame
        const stopAtSelectionEnd = () => {
            const media = mediaRef.current
            if (!media) return
            if (media.currentTime >= rangeEnd - 0.015) {
                media.pause()
                media.currentTime = rangeEnd
                setCurrentTime(rangeEnd)
                setPlayingSelection(false)
                return
            }
            animationFrame = window.requestAnimationFrame(stopAtSelectionEnd)
        }
        animationFrame = window.requestAnimationFrame(stopAtSelectionEnd)
        return () => window.cancelAnimationFrame(animationFrame)
    }, [isPlaying, playingSelection, rangeEnd])

    const updateRange = (start, end) => {
        if (!duration) return
        const nextStart = clamp(start, 0, Math.max(0, end - minimumSelection))
        const nextEnd = clamp(end, nextStart + minimumSelection, duration)
        onRangeChange({ start: nextStart, end: nextEnd, duration })
    }

    const updateBoundaryFromPointer = (event) => {
        const boundary = draggingBoundaryRef.current
        const container = waveformRef.current
        if (!boundary || !container || !duration) return
        const rect = container.getBoundingClientRect()
        const time = clamp(((event.clientX - rect.left) / rect.width) * duration, 0, duration)
        if (boundary === 'start') updateRange(time, rangeEnd)
        else updateRange(rangeStart, time)
    }

    useEffect(() => {
        const handleMove = event => updateBoundaryFromPointer(event)
        const handleUp = () => {
            draggingBoundaryRef.current = null
            setDraggingBoundary(null)
        }
        window.addEventListener('pointermove', handleMove)
        window.addEventListener('pointerup', handleUp)
        return () => {
            window.removeEventListener('pointermove', handleMove)
            window.removeEventListener('pointerup', handleUp)
        }
    })

    const handleMetadata = () => {
        const mediaDuration = mediaRef.current?.duration
        if (!Number.isFinite(mediaDuration) || mediaDuration <= 0 || mediaDuration === duration) return
        setDuration(mediaDuration)
        setCurrentTime(0)
        onRangeChange({ start: 0, end: mediaDuration, duration: mediaDuration })
    }

    const seek = (time) => {
        const media = mediaRef.current
        if (!media || !duration) return
        const nextTime = clamp(time, 0, duration)
        media.currentTime = nextTime
        setCurrentTime(nextTime)
    }

    const play = async (selectionOnly = false) => {
        const media = mediaRef.current
        if (!media) return
        if (selectionOnly) seek(rangeStart)
        else if (media.currentTime >= duration - 0.05) seek(0)
        setPlayingSelection(selectionOnly)
        try {
            await media.play()
        } catch {
            setPlayingSelection(false)
        }
    }

    const togglePlayback = () => {
        const media = mediaRef.current
        if (!media) return
        if (media.paused) play(false)
        else media.pause()
    }

    const handleTimeUpdate = () => {
        const media = mediaRef.current
        if (!media) return
        const nextTime = media.currentTime
        setCurrentTime(nextTime)
        if (playingSelection && nextTime >= rangeEnd - 0.03) {
            media.pause()
            media.currentTime = rangeEnd
            setCurrentTime(rangeEnd)
            setPlayingSelection(false)
        }
    }

    const handleWaveformClick = (event) => {
        if (!duration || draggingBoundaryRef.current) return
        const rect = event.currentTarget.getBoundingClientRect()
        seek(((event.clientX - rect.left) / rect.width) * duration)
    }

    const startBoundaryDrag = (boundary, event) => {
        event.preventDefault()
        event.stopPropagation()
        draggingBoundaryRef.current = boundary
        setDraggingBoundary(boundary)
    }

    return (
        <section className="media-range-player" aria-label="媒體片段選取播放器">
            <MediaElement
                ref={mediaRef}
                src={objectUrl}
                preload="metadata"
                className={isVideo ? 'media-range-video' : 'media-range-audio'}
                onLoadedMetadata={handleMetadata}
                onDurationChange={handleMetadata}
                onTimeUpdate={handleTimeUpdate}
                onPlay={() => setIsPlaying(true)}
                onPause={() => setIsPlaying(false)}
                onEnded={() => { setIsPlaying(false); setPlayingSelection(false) }}
            />

            <div
                ref={waveformRef}
                className={`media-waveform ${waveformStatus !== 'ready' ? 'media-waveform-fallback' : ''}`}
                onClick={handleWaveformClick}
                role="slider"
                aria-label="音訊波形與播放位置"
                aria-valuemin="0"
                aria-valuemax={duration}
                aria-valuenow={currentTime}
                tabIndex="0"
                onKeyDown={event => {
                    if (event.key === 'ArrowLeft') seek(currentTime - 1)
                    if (event.key === 'ArrowRight') seek(currentTime + 1)
                }}
            >
                {waveformStatus === 'ready' ? (
                    <div className="media-waveform-bars" aria-hidden="true">
                        {waveform.map((peak, index) => (
                            <span key={index} style={{ height: `${Math.max(4, peak * 100)}%` }} />
                        ))}
                    </div>
                ) : (
                    <div className="media-waveform-message">
                        {waveformStatus === 'loading' && '正在產生聲音波形…'}
                        {waveformStatus === 'large' && '檔案較大，已略過波形分析（播放器仍可正常使用）'}
                        {waveformStatus === 'long' && '音訊超過 30 分鐘，已略過波形分析以節省瀏覽器記憶體'}
                        {waveformStatus === 'unsupported' && '瀏覽器無法解析此格式的波形（播放器仍可正常使用）'}
                    </div>
                )}
                {duration > 0 && (
                    <>
                        <div className="media-range-mask media-range-mask-start" style={{ width: `${(rangeStart / duration) * 100}%` }} />
                        <div className="media-range-mask media-range-mask-end" style={{ width: `${((duration - rangeEnd) / duration) * 100}%` }} />
                        <div className="media-playhead" style={{ left: `${(currentTime / duration) * 100}%` }} />
                        <button type="button" className={`media-range-handle media-range-handle-start ${draggingBoundary === 'start' ? 'active' : ''}`} style={{ left: `${(rangeStart / duration) * 100}%` }} onPointerDown={event => startBoundaryDrag('start', event)} aria-label={`拖曳片段開頭，目前 ${formatTime(rangeStart)}`} />
                        <button type="button" className={`media-range-handle media-range-handle-end ${draggingBoundary === 'end' ? 'active' : ''}`} style={{ left: `${(rangeEnd / duration) * 100}%` }} onPointerDown={event => startBoundaryDrag('end', event)} aria-label={`拖曳片段結尾，目前 ${formatTime(rangeEnd)}`} />
                    </>
                )}
            </div>

            <div className="media-timeline-row">
                <span>{formatTime(currentTime)}</span>
                <input type="range" min="0" max={duration || 0} step="0.01" value={Math.min(currentTime, duration || 0)} onChange={event => seek(Number(event.target.value))} aria-label="播放進度" disabled={!duration} />
                <span>{formatTime(duration)}</span>
            </div>

            <div className="media-controls">
                <div className="media-controls-primary">
                    <button type="button" className="btn btn-outline media-control-button" onClick={() => seek(currentTime - 5)} disabled={!duration} aria-label="倒退 5 秒">−5s</button>
                    <button type="button" className="btn btn-primary media-play-button" onClick={togglePlayback} disabled={!duration}>{isPlaying ? '❚❚ 暫停' : '▶ 播放'}</button>
                    <button type="button" className="btn btn-outline media-control-button" onClick={() => seek(currentTime + 5)} disabled={!duration} aria-label="快進 5 秒">+5s</button>
                    <button type="button" className="btn btn-outline" onClick={() => play(true)} disabled={!duration || rangeEnd <= rangeStart}>▶ 播放選取片段</button>
                </div>
                <div className="media-controls-secondary">
                    <button type="button" className="media-volume-button" onClick={() => setMuted(value => !value)} aria-label={muted ? '取消靜音' : '靜音'}>{muted || volume === 0 ? '🔇' : '🔊'}</button>
                    <input type="range" min="0" max="1" step="0.05" value={volume} onChange={event => setVolume(Number(event.target.value))} aria-label="音量" />
                    <select className="form-select media-rate-select" value={playbackRate} onChange={event => setPlaybackRate(Number(event.target.value))} aria-label="播放速度">
                        {[0.5, 0.75, 1, 1.25, 1.5, 2].map(rate => <option key={rate} value={rate}>{rate}×</option>)}
                    </select>
                </div>
            </div>

            <div className="media-range-editor">
                <div className="media-range-field">
                    <label htmlFor="media-range-start">片段開頭</label>
                    <div className="media-time-input-row">
                        <input id="media-range-start" className="form-input" type="number" min="0" max={Math.max(0, rangeEnd - minimumSelection)} step="0.1" value={rangeStart.toFixed(1)} onChange={event => updateRange(Number(event.target.value), rangeEnd)} disabled={!duration} />
                        <span>秒</span>
                    </div>
                    <input type="range" min="0" max={duration || 0} step="0.1" value={rangeStart} onChange={event => updateRange(Number(event.target.value), rangeEnd)} disabled={!duration} aria-label="片段開頭" />
                </div>
                <div className="media-range-summary">
                    <span>選取長度</span>
                    <strong>{formatTime(Math.max(0, rangeEnd - rangeStart))}</strong>
                    <button type="button" className="btn btn-outline" onClick={() => updateRange(0, duration)} disabled={!duration}>重設完整範圍</button>
                </div>
                <div className="media-range-field">
                    <label htmlFor="media-range-end">片段結尾</label>
                    <div className="media-time-input-row">
                        <input id="media-range-end" className="form-input" type="number" min={rangeStart + minimumSelection} max={duration || 0} step="0.1" value={rangeEnd.toFixed(1)} onChange={event => updateRange(rangeStart, Number(event.target.value))} disabled={!duration} />
                        <span>秒</span>
                    </div>
                    <input type="range" min="0" max={duration || 0} step="0.1" value={rangeEnd} onChange={event => updateRange(rangeStart, Number(event.target.value))} disabled={!duration} aria-label="片段結尾" />
                </div>
            </div>
        </section>
    )
}
