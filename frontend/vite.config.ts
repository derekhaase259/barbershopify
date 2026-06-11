import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5280, // out of the crowded 5173+ default range; shifts up if taken
    proxy: {
      '/api': 'http://localhost:8731',
    },
  },
})
