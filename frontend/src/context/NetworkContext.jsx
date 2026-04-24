import { createContext, useContext, useState, useEffect, useCallback } from 'react'

const NetworkContext = createContext(null)

export function NetworkProvider({ children }) {
    const [browserOnline, setBrowserOnline] = useState(navigator.onLine)
    const [systemStatus, setSystemStatus] = useState(null)
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        const handleOnline = () => setBrowserOnline(true)
        const handleOffline = () => setBrowserOnline(false)
        window.addEventListener('online', handleOnline)
        window.addEventListener('offline', handleOffline)
        return () => {
            window.removeEventListener('online', handleOnline)
            window.removeEventListener('offline', handleOffline)
        }
    }, [])

    const fetchSystemStatus = useCallback(async () => {
        try {
            const res = await fetch('/api/system/status')
            if (res.ok) {
                const data = await res.json()
                setSystemStatus(data)
            }
        } catch {
            // backend unreachable
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        fetchSystemStatus()
        const interval = setInterval(fetchSystemStatus, 60000)
        return () => clearInterval(interval)
    }, [fetchSystemStatus])

    const isModelCached = useCallback((modelKey) => {
        if (!systemStatus?.models) return true
        const m = systemStatus.models[modelKey]
        return m ? m.cached : true
    }, [systemStatus])

    const online = systemStatus ? systemStatus.online : browserOnline
    const offline = !online

    return (
        <NetworkContext.Provider value={{
            online,
            offline,
            browserOnline,
            systemStatus,
            loading,
            isModelCached,
            refreshStatus: fetchSystemStatus,
        }}>
            {children}
        </NetworkContext.Provider>
    )
}

export const useNetwork = () => useContext(NetworkContext)
