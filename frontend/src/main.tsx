import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import Reauthentication from './saas/Reauthentication'
import './styles.css'
import './modal.css'

createRoot(document.getElementById('root')!).render(<React.StrictMode><Reauthentication/><App /></React.StrictMode>)
