import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

const ModelContext = createContext(null)
const STATUS_ENDPOINT = '/api/system/status'
const REFRESH_INTERVAL_MS = 30_000
const REQUEST_TIMEOUT_MS = 8_000

export function ModelProvider({ children }) {
    const [models, setModels] = useState({})
    const [status, setStatus] = useState('loading')
    const [error, setError] = useState('')
    const [lastUpdated, setLastUpdated] = useState(null)

    const refreshModels = useCallback(async () => {
        const controller = new AbortController()
        const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
        try {
            const response = await fetch(STATUS_ENDPOINT, {
                cache: 'no-store',
                signal: controller.signal,
            })
            if (!response.ok) throw new Error(`HTTP ${response.status}`)
            const payload = await response.json()
            if (!payload?.models || typeof payload.models !== 'object') {
                throw new Error('模型狀態格式不正確')
            }
            setModels(payload.models)
            setStatus('ready')
            setError('')
            setLastUpdated(new Date())
        } catch (reason) {
            setStatus('error')
            setError(
                reason?.name === 'AbortError'
                    ? '模型狀態查詢逾時'
                    : `無法取得模型狀態：${reason?.message || '未知錯誤'}`,
            )
        } finally {
            window.clearTimeout(timeoutId)
        }
    }, [])

    useEffect(() => {
        refreshModels()
        const intervalId = window.setInterval(refreshModels, REFRESH_INTERVAL_MS)
        const handleFocus = () => refreshModels()
        window.addEventListener('focus', handleFocus)
        return () => {
            window.clearInterval(intervalId)
            window.removeEventListener('focus', handleFocus)
        }
    }, [refreshModels])

    const value = useMemo(() => {
        const isModelReady = modelKey => (
            status === 'ready' && models[modelKey]?.cached === true
        )
        const findModelKey = modelId => (
            Object.keys(models).find(key => models[key]?.model_id === modelId) || null
        )
        const getMissingModels = modelKeys => (
            [...new Set((modelKeys || []).filter(Boolean))]
                .filter(key => !isModelReady(key))
                .map(key => ({ key, ...(models[key] || {}) }))
        )

        return {
            models,
            status,
            loading: status === 'loading',
            error,
            lastUpdated,
            isModelReady,
            findModelKey,
            getMissingModels,
            refreshModels,
        }
    }, [error, lastUpdated, models, refreshModels, status])

    return <ModelContext.Provider value={value}>{children}</ModelContext.Provider>
}

export function useModels() {
    const context = useContext(ModelContext)
    if (!context) throw new Error('useModels 必須在 ModelProvider 內使用')
    return context
}
