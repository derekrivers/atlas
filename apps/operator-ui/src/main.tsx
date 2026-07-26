import { StrictMode } from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import { AppProviders } from '@/app-providers'
import { createOperatorRouter } from '@/router'
import './styles/index.css'

const rootElement = document.getElementById('root')

if (!rootElement) {
  throw new Error('Root element not found')
}

if (!rootElement.innerHTML) {
  const root = ReactDOM.createRoot(rootElement)
  root.render(
    <StrictMode>
      <AppProviders>
        <RouterProvider router={createOperatorRouter()} />
      </AppProviders>
    </StrictMode>
  )
}
