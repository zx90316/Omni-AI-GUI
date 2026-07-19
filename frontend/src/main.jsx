import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

import { AuthProvider } from './context/AuthContext.jsx'
import { ModelProvider } from './context/ModelContext.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
        <AuthProvider>
            <ModelProvider>
                <App />
            </ModelProvider>
        </AuthProvider>
    </React.StrictMode>,
)
