import { useAuth } from '../authContext'

export default function AuthToasts() {
  const { toast } = useAuth()
  if (!toast) return null
  return (
    <div
      className="pointer-events-none fixed left-1/2 top-4 z-[100] max-w-[min(92vw,24rem)] -translate-x-1/2 px-3"
      role="status"
    >
      <div className="rounded-2xl border border-rose-100/90 bg-gradient-to-r from-rose-50/95 to-pink-50/95 px-4 py-2.5 text-center text-sm font-medium text-rose-900 shadow-lg shadow-rose-200/50 ring-1 ring-rose-200/60 backdrop-blur-sm dark:from-rose-950/90 dark:to-pink-950/80 dark:text-rose-100 dark:ring-rose-800/50">
        {toast}
      </div>
    </div>
  )
}
