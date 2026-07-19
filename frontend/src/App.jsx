import { useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import LoginModal from './components/LoginModal.jsx'
import { ModelStatusSummary } from './components/ModelStatus.jsx'
import { useAuth } from './context/AuthContext.jsx'
import ClipOcrWorkflow from './pages/ClipOcrWorkflow.jsx'
import ClipSearch from './pages/ClipSearch.jsx'
import NewTask from './pages/NewTask.jsx'
import OCR from './pages/OCR.jsx'
import OcrCorrectionMap from './pages/OcrCorrectionMap.jsx'
import Semantic from './pages/Semantic.jsx'
import SubSync from './pages/SubSync.jsx'
import TaskDetail from './pages/TaskDetail.jsx'
import TaskList from './pages/TaskList.jsx'
import VectorExtractor from './pages/VectorExtractor.jsx'

const NAV_GROUPS = [
    {
        label: '語音工坊',
        items: [
            { to: '/', end: true, icon: '▤', label: '任務清單' },
            { to: '/new', icon: '+', label: '新增任務' },
            { to: '/subsync', icon: '▶', label: 'SubSync' },
        ],
    },
    {
        label: '視覺工坊',
        items: [
            { to: '/ocr', icon: '▧', label: 'OCR 辨識' },
            { to: '/ocr-correction', icon: 'Aa', label: '形近字修正' },
            { to: '/clip-search', icon: '⌕', label: '以圖搜頁' },
            { to: '/clip-ocr', icon: '◇', label: 'PDF 擷取資料' },
            { to: '/vector-extractor', icon: '↗', label: '向量提取' },
        ],
    },
    {
        label: '語意工坊',
        items: [{ to: '/semantic', icon: '◎', label: '語意與重排序' }],
    },
]

function Layout({ children }) {
    const { role, ownerId, logout } = useAuth()
    const [menuOpen, setMenuOpen] = useState(false)
    const closeMenu = () => setMenuOpen(false)

    return (
        <div className="app-layout">
            <a className="skip-link" href="#main-content">跳到主要內容</a>
            <header className="mobile-header">
                <button
                    type="button"
                    className="mobile-menu-button"
                    onClick={() => setMenuOpen(open => !open)}
                    aria-expanded={menuOpen}
                    aria-controls="app-sidebar"
                    aria-label={menuOpen ? '關閉導覽選單' : '開啟導覽選單'}
                >
                    {menuOpen ? '×' : '☰'}
                </button>
                <div>
                    <strong>Omni AI</strong>
                    <span>多模態工作台</span>
                </div>
            </header>

            {menuOpen && <button type="button" className="sidebar-backdrop" onClick={closeMenu} aria-label="關閉導覽選單" />}
            <aside id="app-sidebar" className={`sidebar ${menuOpen ? 'is-open' : ''}`}>
                <div className="sidebar-brand">
                    <div className="brand-mark" aria-hidden="true">O</div>
                    <div>
                        <h1>Omni AI</h1>
                        <span>多模態 AI 工作台</span>
                    </div>
                </div>

                <nav className="sidebar-nav" aria-label="主要導覽">
                    {NAV_GROUPS.map(group => (
                        <div className="nav-group" key={group.label}>
                            <div className="nav-group-label">{group.label}</div>
                            {group.items.map(item => (
                                <NavLink
                                    key={item.to}
                                    to={item.to}
                                    end={item.end}
                                    onClick={closeMenu}
                                    className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
                                >
                                    <span className="icon" aria-hidden="true">{item.icon}</span>
                                    <span>{item.label}</span>
                                </NavLink>
                            ))}
                        </div>
                    ))}
                </nav>

                <div className="sidebar-footer">
                    <ModelStatusSummary />
                    <div className="user-info">
                        <div className="user-avatar" aria-hidden="true">{role === 'guest' ? '訪' : '用'}</div>
                        <div className="user-meta">
                            <strong>{role === 'guest' ? '訪客工作區' : '個人工作區'}</strong>
                            <span title={ownerId}>{ownerId}</span>
                        </div>
                        <button type="button" onClick={logout} className="icon-button" aria-label="登出" title="登出">↪</button>
                    </div>
                </div>
            </aside>
            <main id="main-content" className="main-content" tabIndex="-1">
                {children}
            </main>
        </div>
    )
}

function NotFound() {
    return (
        <div className="empty-state page-state fade-in">
            <div className="page-state-code">404</div>
            <h2>找不到這個頁面</h2>
            <p>網址可能已變更，請從左側導覽重新選擇功能。</p>
            <NavLink className="btn btn-primary" to="/">返回任務清單</NavLink>
        </div>
    )
}

export default function App() {
    const { token, isLoading } = useAuth()

    if (isLoading) {
        return (
            <div className="app-loading" role="status" aria-live="polite">
                <div className="brand-mark is-large">O</div>
                <span className="spinner" />
                <p>正在準備工作區</p>
            </div>
        )
    }

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
                        <Route path="*" element={<NotFound />} />
                    </Routes>
                </Layout>
            )}
        </BrowserRouter>
    )
}
