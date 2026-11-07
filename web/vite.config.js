import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, the API runs separately: uv run uvicorn hole_cam.api:app --reload
const api = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/jobs': api, '/materials': api } },
})
