import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'
import ViewBoundary,{ViewLoading} from './components/ViewBoundary.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ViewBoundary scope="viewer"><React.Suspense fallback={<ViewLoading label="Opening viewer…"/>}><App /></React.Suspense></ViewBoundary>
  </React.StrictMode>,
)
