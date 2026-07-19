import { useModels } from '../context/ModelContext.jsx'

const MODEL_NAMES = {
    'asr_1.7b': 'Qwen3 ASR 1.7B',
    'asr_0.6b': 'Qwen3 ASR 0.6B',
    forced_aligner: 'Qwen3 Forced Aligner',
    diarization: 'Pyannote 語者分離',
    clip: 'CLIP ViT-L/14',
    bge_reranker: 'BGE Reranker',
    bge_embedding: 'BGE Embedding',
    glm_ocr: 'GLM-OCR',
    pp_doclayout: 'PP-DocLayoutV3',
}

export function modelDisplayName(model) {
    return MODEL_NAMES[model.key] || model.model_id || model.key
}

export function useModelRequirement(modelKeys) {
    const modelContext = useModels()
    const keys = [...new Set((modelKeys || []).filter(Boolean))]
    const missingModels = modelContext.getMissingModels(keys)
    return {
        ...modelContext,
        modelKeys: keys,
        missingModels,
        blocked: keys.length > 0 && (modelContext.status !== 'ready' || missingModels.length > 0),
    }
}

export function ModelRequirement({ modelKeys, title = '必要模型尚未就緒' }) {
    const { status, error, missingModels, refreshModels, modelKeys: keys } = useModelRequirement(modelKeys)
    if (keys.length === 0) return null
    if (status === 'ready' && missingModels.length === 0) return null

    const isLoading = status === 'loading'
    const isError = status === 'error'
    return (
        <section
            className={`model-requirement ${isError ? 'is-error' : ''}`}
            role={isError ? 'alert' : 'status'}
            aria-live="polite"
        >
            <div className="model-requirement-icon" aria-hidden="true">
                {isLoading ? <span className="spinner" /> : isError ? '!' : '↓'}
            </div>
            <div className="model-requirement-body">
                <strong>{isLoading ? '正在確認模型狀態' : isError ? '目前無法確認模型狀態' : title}</strong>
                {isError ? (
                    <p>{error}。為避免執行時才下載或失敗，此功能已暫時停用。</p>
                ) : !isLoading && (
                    <>
                        <p>請先在 Manager 的「安裝／更新 → 模型管理」完成下載與驗證。</p>
                        <ul>
                            {missingModels.map(model => (
                                <li key={model.key}>
                                    {modelDisplayName(model)}
                                    {model.state === 'partial' ? '（下載不完整）' : '（尚未下載）'}
                                </li>
                            ))}
                        </ul>
                    </>
                )}
            </div>
            {!isLoading && (
                <button type="button" className="btn btn-outline btn-sm" onClick={refreshModels}>
                    重新檢查
                </button>
            )}
        </section>
    )
}

export function ModelStatusSummary() {
    const { models, status, refreshModels, lastUpdated } = useModels()
    const entries = Object.values(models)
    const readyCount = entries.filter(model => model.cached).length
    const total = entries.length
    const complete = status === 'ready' && total > 0 && readyCount === total

    return (
        <div className="model-summary" aria-live="polite">
            <div className="model-summary-row">
                <span className={`status-dot ${complete ? 'is-ready' : status === 'error' ? 'is-error' : 'is-warning'}`} />
                <span>
                    {status === 'loading' && '正在檢查模型'}
                    {status === 'error' && '模型狀態無法取得'}
                    {status === 'ready' && `模型 ${readyCount}/${total} 可用`}
                </span>
                <button type="button" className="icon-button" onClick={refreshModels} aria-label="重新檢查模型狀態" title="重新檢查模型狀態">
                    ↻
                </button>
            </div>
            {lastUpdated && <small>更新於 {lastUpdated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</small>}
        </div>
    )
}
