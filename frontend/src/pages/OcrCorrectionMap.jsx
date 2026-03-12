import { useState, useEffect, useCallback } from 'react'
import { fetchWithAuth } from '../utils/api.js'

export default function OcrCorrectionMap() {
    const [entries, setEntries] = useState([])
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')
    const [newWrong, setNewWrong] = useState('')
    const [newRight, setNewRight] = useState('')
    const [filterText, setFilterText] = useState('')

    const loadMap = useCallback(async () => {
        setLoading(true)
        setError('')
        try {
            const res = await fetchWithAuth('/api/ocr/correction-map')
            if (!res.ok) throw new Error(`HTTP ${res.status}`)
            const data = await res.json()
            const list = Object.entries(data).map(([wrong, right]) => ({ wrong, right }))
            setEntries(list)
        } catch (e) {
            setError(`載入失敗: ${e.message}`)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { loadMap() }, [loadMap])

    const saveMap = async () => {
        setSaving(true)
        setError('')
        setSuccess('')
        try {
            const map = {}
            for (const { wrong, right } of entries) {
                if (wrong.trim() && right.trim()) {
                    map[wrong.trim()] = right.trim()
                }
            }
            const res = await fetchWithAuth('/api/ocr/correction-map', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(map),
            })
            if (!res.ok) throw new Error(`HTTP ${res.status}`)
            const result = await res.json()
            setSuccess(`已儲存 ${result.count} 筆修正規則`)
            setTimeout(() => setSuccess(''), 3000)
        } catch (e) {
            setError(`儲存失敗: ${e.message}`)
        } finally {
            setSaving(false)
        }
    }

    const addEntry = () => {
        if (!newWrong.trim() || !newRight.trim()) return
        if (entries.some(e => e.wrong === newWrong.trim())) {
            setError(`「${newWrong.trim()}」已存在`)
            setTimeout(() => setError(''), 3000)
            return
        }
        setEntries(prev => [...prev, { wrong: newWrong.trim(), right: newRight.trim() }])
        setNewWrong('')
        setNewRight('')
    }

    const removeEntry = (index) => {
        setEntries(prev => prev.filter((_, i) => i !== index))
    }

    const updateEntry = (index, field, value) => {
        setEntries(prev => {
            const updated = [...prev]
            updated[index] = { ...updated[index], [field]: value }
            return updated
        })
    }

    const filtered = filterText
        ? entries.filter(e =>
            e.wrong.includes(filterText) || e.right.includes(filterText)
        )
        : entries

    const groupedEntries = {}
    for (const entry of filtered) {
        const group = entry.right
        if (!groupedEntries[group]) groupedEntries[group] = []
        groupedEntries[group].push(entry)
    }

    return (
        <div className="fade-in">
            <div className="page-header">
                <h2>🔤 OCR 形近字修正字典</h2>
                <p>管理 OCR 辨識後自動替換的錯別字對照表，修改後需點擊儲存才會生效</p>
            </div>

            {error && (
                <div className="ocr-error" style={{ marginBottom: 'var(--space-md)' }}>
                    <span>⚠️</span> {error}
                </div>
            )}

            {success && (
                <div className="corr-success">
                    <span>✅</span> {success}
                </div>
            )}

            {loading ? (
                <div className="card" style={{ textAlign: 'center', padding: 'var(--space-2xl)' }}>
                    <div className="spinner-lg" style={{ margin: '0 auto var(--space-md)' }}></div>
                    <p className="text-muted">載入字典中...</p>
                </div>
            ) : (
                <>
                    {/* 新增 & 搜尋列 */}
                    <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
                        <h3 className="ocr-section-title">➕ 新增修正規則</h3>
                        <div className="corr-add-row">
                            <input
                                type="text"
                                className="form-input"
                                placeholder="錯誤字詞（如：公嚬）"
                                value={newWrong}
                                onChange={e => setNewWrong(e.target.value)}
                                onKeyDown={e => e.key === 'Enter' && addEntry()}
                            />
                            <span className="corr-arrow">→</span>
                            <input
                                type="text"
                                className="form-input"
                                placeholder="正確字詞（如：公噸）"
                                value={newRight}
                                onChange={e => setNewRight(e.target.value)}
                                onKeyDown={e => e.key === 'Enter' && addEntry()}
                            />
                            <button className="btn btn-primary btn-sm" onClick={addEntry}>
                                新增
                            </button>
                        </div>
                    </div>

                    {/* 工具列 */}
                    <div className="corr-toolbar">
                        <div className="corr-toolbar-left">
                            <input
                                type="text"
                                className="form-input corr-filter-input"
                                placeholder="搜尋字詞..."
                                value={filterText}
                                onChange={e => setFilterText(e.target.value)}
                            />
                            <span className="text-muted" style={{ fontSize: '0.85rem' }}>
                                共 {entries.length} 筆規則{filterText && `，篩選顯示 ${filtered.length} 筆`}
                            </span>
                        </div>
                        <div className="corr-toolbar-right">
                            <button className="btn btn-outline btn-sm" onClick={loadMap}>
                                🔄 重新載入
                            </button>
                            <button className="btn btn-primary" onClick={saveMap} disabled={saving}>
                                {saving ? '儲存中...' : '💾 儲存變更'}
                            </button>
                        </div>
                    </div>

                    {/* 字典表格 */}
                    <div className="card corr-table-card">
                        <div className="corr-table-header">
                            <span className="corr-col-wrong">錯誤字詞</span>
                            <span className="corr-col-arrow"></span>
                            <span className="corr-col-right">正確字詞</span>
                            <span className="corr-col-action"></span>
                        </div>
                        <div className="corr-table-body">
                            {Object.entries(groupedEntries).map(([group, items]) => (
                                <div key={group} className="corr-group">
                                    <div className="corr-group-label">{group}</div>
                                    {items.map((entry) => {
                                        const realIndex = entries.findIndex(e => e.wrong === entry.wrong && e.right === entry.right)
                                        return (
                                            <div key={entry.wrong} className="corr-table-row">
                                                <input
                                                    type="text"
                                                    className="form-input corr-col-wrong"
                                                    value={entry.wrong}
                                                    onChange={e => updateEntry(realIndex, 'wrong', e.target.value)}
                                                />
                                                <span className="corr-col-arrow">→</span>
                                                <input
                                                    type="text"
                                                    className="form-input corr-col-right"
                                                    value={entry.right}
                                                    onChange={e => updateEntry(realIndex, 'right', e.target.value)}
                                                />
                                                <button
                                                    className="btn btn-outline btn-sm ocr-field-remove"
                                                    onClick={() => removeEntry(realIndex)}
                                                    title="刪除此規則"
                                                >
                                                    ✕
                                                </button>
                                            </div>
                                        )
                                    })}
                                </div>
                            ))}
                            {filtered.length === 0 && (
                                <div className="corr-empty">
                                    {filterText ? '沒有符合的結果' : '尚無修正規則，請在上方新增'}
                                </div>
                            )}
                        </div>
                    </div>
                </>
            )}
        </div>
    )
}
