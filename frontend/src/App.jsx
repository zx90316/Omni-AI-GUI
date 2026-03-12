import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import TaskList from './pages/TaskList.jsx'
import NewTask from './pages/NewTask.jsx'
import TaskDetail from './pages/TaskDetail.jsx'
import SubSync from './pages/SubSync.jsx'
import OCR from './pages/OCR.jsx'
import OcrCorrectionMap from './pages/OcrCorrectionMap.jsx'
import ClipSearch from './pages/ClipSearch.jsx'
import ClipOcrWorkflow from './pages/ClipOcrWorkflow.jsx'
import VectorExtractor from './pages/VectorExtractor.jsx'
import Semantic from './pages/Semantic.jsx'
import LoginModal from './components/LoginModal.jsx'
import { useAuth } from './context/AuthContext.jsx'

function Layout({ children }) {
    const { token, role, ownerId, logout } = useAuth()

    return (
        <div className="app-layout">
            <aside className="sidebar">
                <div className="sidebar-brand">
                    <h1>Omni AI</h1>
                    <span>多模態 AI 處理工具</span>
                </div>
                {token && (
                    <div className="user-info" style={{ padding: '0 1.5rem', marginBottom: '1rem', fontSize: '0.85rem', color: 'var(--color-text-muted)' }}>
                        <div style={{ marginBottom: '4px' }}>身份: {role === 'guest' ? '訪客' : '一般用戶'}</div>
                        <div style={{ wordBreak: 'break-all' }}>ID: {ownerId}</div>
                        <button onClick={logout} className="btn btn-text btn-sm" style={{ marginTop: '8px', padding: '0', border: 'none', background: 'transparent', color: 'var(--color-primary)' }}>登出</button>
                    </div>
                )}
                <nav className="sidebar-nav">
                    <div className="nav-group">
                        <div className="nav-group-label">語音工坊</div>
                        <NavLink
                            to="/"
                            end
                            className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                        >
                            <span className="icon">📋</span>
                            任務清單
                        </NavLink>
                        <NavLink
                            to="/new"
                            className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                        >
                            <span className="icon">➕</span>
                            新增任務
                        </NavLink>
                        <NavLink
                            to="/subsync"
                            className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                        >
                            <span className="icon">🎬</span>
                            SubSync
                        </NavLink>
                    </div>

                    <div className="nav-group">
                        <div className="nav-group-label">視覺工坊</div>
                        <NavLink
                            to="/ocr"
                            className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                        >
                            <span className="icon">📄</span>
                            OCR 辨識
                        </NavLink>
                        <NavLink
                            to="/ocr-correction"
                            className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                        >
                            <span className="icon">🔤</span>
                            形近字修正
                        </NavLink>
                        <NavLink
                            to="/clip-search"
                            className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                        >
                            <span className="icon">🔍</span>
                            以圖搜頁
                        </NavLink>
                        <NavLink
                            to="/clip-ocr"
                            className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                        >
                            <span className="icon">🛠️</span>
                            PDF擷取資料
                        </NavLink>
                        <NavLink
                            to="/vector-extractor"
                            className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                        >
                            <span className="icon">📊</span>
                            向量提取
                        </NavLink>
                    </div>

                    <div className="nav-group">
                        <div className="nav-group-label">語意工坊</div>
                        <NavLink
                            to="/semantic"
                            className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                        >
                            <span className="icon">🧠</span>
                            語意與重排序
                        </NavLink>
                    </div>
                </nav>
            </aside>
            <main className="main-content">
                {children}
            </main>
        </div>
    )
}

export default function App() {
    const { token, isLoading } = useAuth()

    if (isLoading) return <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>載入中...</div>

    return (
        <BrowserRouter>
            {!token ? (
                <LoginModal />
            ) : (
                <Layout>
                    <Routes>
                        <Route path="/" element={<TaskList />} />
                        <Route path="/new" element={<NewTask />} />
                        <Route path="/tasks/:id" element={<TaskDetail />} />
                        <Route path="/subsync" element={<SubSync />} />
                        <Route path="/ocr" element={<OCR />} />
                        <Route path="/ocr-correction" element={<OcrCorrectionMap />} />
                        <Route path="/clip-search" element={<ClipSearch />} />
                        <Route path="/clip-ocr" element={<ClipOcrWorkflow />} />
                        <Route path="/vector-extractor" element={<VectorExtractor />} />
                        <Route path="/semantic" element={<Semantic />} />
                    </Routes>
                </Layout>
            )}
        </BrowserRouter>
    )
}
