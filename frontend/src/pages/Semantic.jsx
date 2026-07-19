import React, { useState } from 'react';
import { fetchWithAuth } from '../utils/api.js';
import { ModelRequirement, useModelRequirement } from '../components/ModelStatus.jsx';
import './Semantic.css';

export default function Semantic() {
    const [activeTab, setActiveTab] = useState('embed'); // 'embed' or 'rerank'
    const requiredModels = [activeTab === 'embed' ? 'bge_embedding' : 'bge_reranker'];
    const { blocked: modelsBlocked } = useModelRequirement(requiredModels);

    // Embedding State
    const [embedInput, setEmbedInput] = useState('');
    const [embedResult, setEmbedResult] = useState(null);
    const [embedLoading, setEmbedLoading] = useState(false);
    const [embedError, setEmbedError] = useState('');

    // Rerank State
    const [query, setQuery] = useState('');
    const [documents, setDocuments] = useState(['', '']);
    const [rerankResult, setRerankResult] = useState(null);
    const [rerankLoading, setRerankLoading] = useState(false);
    const [rerankError, setRerankError] = useState('');

    const handleEmbed = async () => {
        setEmbedError('');
        setEmbedResult(null);
        if (!embedInput.trim()) {
            setEmbedError('請輸入文本');
            return;
        }
        if (modelsBlocked) {
            setEmbedError('Embedding 模型尚未就緒，請先透過 Manager 下載');
            return;
        }

        const sentences = embedInput.split('\n').filter(s => s.trim() !== '');
        setEmbedLoading(true);

        try {
            const res = await fetchWithAuth('/semantic/embed', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ sentences })
            });
            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || 'Embedding request failed');
            }
            const data = await res.json();
            setEmbedResult({
                sentences,
                embeddings: data.embeddings
            });
        } catch (err) {
            setEmbedError(err.message);
        } finally {
            setEmbedLoading(false);
        }
    };

    const handleRerank = async () => {
        setRerankError('');
        setRerankResult(null);
        if (!query.trim()) {
            setRerankError('請輸入查詢字串 (Query)');
            return;
        }
        if (modelsBlocked) {
            setRerankError('Reranker 模型尚未就緒，請先透過 Manager 下載');
            return;
        }

        const validDocs = documents.filter(d => d.trim() !== '');
        if (validDocs.length === 0) {
            setRerankError('請至少輸入一個參考文檔');
            return;
        }

        const pairs = validDocs.map(doc => [query, doc]);
        setRerankLoading(true);

        try {
            const res = await fetchWithAuth('/semantic/rerank', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ pairs, normalize: true })
            });
            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || 'Reranking request failed');
            }
            const data = await res.json();

            const results = validDocs.map((doc, idx) => ({
                document: doc,
                score: data.scores[idx]
            })).sort((a, b) => b.score - a.score);

            setRerankResult({
                query,
                results
            });
        } catch (err) {
            setRerankError(err.message);
        } finally {
            setRerankLoading(false);
        }
    };

    return (
        <div className="semantic-page">
            <header className="page-header">
                <h2>Semantic Workshop (語意工坊)</h2>
                <p>提供基於 BGE 模型的文本向量化 (Embedding) 與 相關度重排序 (Reranking)。</p>
            </header>

            <ModelRequirement modelKeys={requiredModels} title="語意模型尚未就緒" />

            <div style={{ marginTop: 'var(--space-md)' }}>
                <div className="tab-buttons" role="tablist" aria-label="語意工具">
                    <button
                        type="button"
                        role="tab"
                        aria-selected={activeTab === 'embed'}
                        className={`tab-btn ${activeTab === 'embed' ? 'active' : ''}`}
                        onClick={() => setActiveTab('embed')}
                    >
                        Embedding 向量化
                    </button>
                    <button
                        type="button"
                        role="tab"
                        aria-selected={activeTab === 'rerank'}
                        className={`tab-btn ${activeTab === 'rerank' ? 'active' : ''}`}
                        onClick={() => setActiveTab('rerank')}
                    >
                        Reranking 語意重排序
                    </button>
                </div>
            </div>

            <div className="semantic-content">
                {activeTab === 'embed' && (
                    <div className="card">
                        <h3>文本向量化 (Embedding)</h3>
                        <div className="form-group">
                            <label>請輸入要向量化的句子 (每行一句)</label>
                            <textarea
                                value={embedInput}
                                onChange={e => setEmbedInput(e.target.value)}
                                rows={6}
                                placeholder="你好世界&#10;Hello World"
                                className="form-control"
                            />
                        </div>
                        {embedError && <div className="alert error">{embedError}</div>}
                        <button
                            className="btn btn-primary"
                            onClick={handleEmbed}
                            disabled={embedLoading || modelsBlocked}
                        >
                            {embedLoading ? '運算中...' : '生成向量'}
                        </button>

                        {embedResult && (
                            <div className="result-section">
                                <h4>結果預覽</h4>
                                <p>共 {embedResult.sentences.length} 句，每句向量維度 {embedResult.embeddings[0].length}</p>
                                <div className="json-preview">
                                    {embedResult.sentences.map((s, i) => (
                                        <div key={i} className="embed-item">
                                            <strong>{s}</strong>
                                            <div className="vector-snippet">
                                                [{embedResult.embeddings[i].slice(0, 5).map(v => v.toFixed(4)).join(', ')} ... ]
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {activeTab === 'rerank' && (
                    <div className="card">
                        <h3>語意重排序 (Reranking)</h3>
                        <div className="form-group">
                            <label>Query (查詢問題)</label>
                            <input
                                type="text"
                                className="form-control"
                                value={query}
                                onChange={e => setQuery(e.target.value)}
                                placeholder="例如：什麼是大貓熊？"
                            />
                        </div>

                        <div className="form-group document-list">
                            <label>Documents (參考文檔) <button className="btn-icon" onClick={() => setDocuments([...documents, ''])}>➕ 新增文檔</button></label>
                            {documents.map((doc, idx) => (
                                <div key={idx} className="doc-input-row">
                                    <span className="doc-idx">Doc {idx + 1}</span>
                                    <input
                                        type="text"
                                        className="form-control"
                                        value={doc}
                                        onChange={e => {
                                            const newDocs = [...documents];
                                            newDocs[idx] = e.target.value;
                                            setDocuments(newDocs);
                                        }}
                                        placeholder={`參考文檔 ${idx + 1}`}
                                    />
                                    {documents.length > 2 && (
                                        <button
                                            className="btn-icon danger"
                                            onClick={() => setDocuments(documents.filter((_, i) => i !== idx))}
                                        >
                                            ❌
                                        </button>
                                    )}
                                </div>
                            ))}
                        </div>

                        {rerankError && <div className="alert error">{rerankError}</div>}

                        <button
                            className="btn btn-primary"
                            onClick={handleRerank}
                            disabled={rerankLoading || modelsBlocked}
                        >
                            {rerankLoading ? '計算中...' : '計算相關度'}
                        </button>

                        {rerankResult && (
                            <div className="result-section">
                                <h4>重排序結果 (由高到低)</h4>
                                <ul className="rerank-list">
                                    {rerankResult.results.map((res, i) => (
                                        <li key={i} className="rerank-item">
                                            <div className="score" style={{ backgroundColor: `rgba(46, 204, 113, ${Math.max(0.1, res.score)})` }}>
                                                {res.score.toFixed(4)}
                                            </div>
                                            <div className="doc-text">{res.document}</div>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
