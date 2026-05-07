import { useState } from 'react'
import { Heart, LogOut, Sparkles } from 'lucide-react'
import { useAuth } from '../authContext'
import FavoritesDrawer from './FavoritesDrawer'

type Props = {
  compact?: boolean
}

export default function NavUserMenu({ compact }: Props) {
  const { user, openAuth, logout, favorites, favoritesLoading, refreshFavorites } = useAuth()
  const [favOpen, setFavOpen] = useState(false)

  const openFavorites = () => {
    setFavOpen(true)
    void refreshFavorites()
  }

  const initial = (user?.email?.[0] ?? '?').toUpperCase()

  return (
    <>
      <div
        className={`flex shrink-0 items-center gap-2 ${compact ? 'scale-95' : ''}`}
        aria-label="用户与收藏"
      >
        {!user ? (
          <button
            type="button"
            onClick={() => openAuth('login')}
            className="inline-flex items-center gap-1.5 rounded-full border border-rose-100/90 bg-gradient-to-r from-rose-50/95 to-pink-50/90 px-3.5 py-1.5 text-xs font-semibold text-rose-700 shadow-sm shadow-rose-200/40 transition-[transform,box-shadow] hover:shadow-md active:scale-[0.98] dark:border-rose-900/50 dark:from-rose-950/60 dark:to-pink-950/50 dark:text-rose-200"
          >
            <Sparkles className="h-3.5 w-3.5 opacity-80" strokeWidth={2.5} />
            注册 / 登录
          </button>
        ) : (
          <>
            <div
              className="inline-flex max-w-[min(46vw,12rem)] items-center gap-1.5 rounded-full border border-slate-200/80 bg-white/90 px-2 py-1 text-xs font-medium text-slate-600 shadow-sm dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-300"
              title={user.email}
            >
              <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-rose-200/90 to-pink-200/90 text-[11px] font-bold text-rose-900 shadow-inner dark:from-rose-600/40 dark:to-pink-600/40 dark:text-rose-50">
                {initial}
              </span>
              <span className="hidden min-w-0 truncate sm:inline">{user.email}</span>
            </div>
            <button
              type="button"
              onClick={openFavorites}
              className="inline-flex items-center gap-1 rounded-full border border-rose-100/90 bg-white/90 px-3 py-1.5 text-xs font-semibold text-rose-700 shadow-sm transition-[transform,box-shadow] hover:bg-rose-50 hover:shadow-md active:scale-[0.98] dark:border-rose-900/50 dark:bg-slate-900/85 dark:text-rose-200 dark:hover:bg-rose-950/50"
            >
              <Heart className="h-3.5 w-3.5" strokeWidth={2.5} />
              我的收藏
            </button>
            <button
              type="button"
              onClick={logout}
              className="inline-flex items-center gap-1 rounded-full px-2 py-1.5 text-xs font-medium text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
              title="退出登录"
            >
              <LogOut className="h-3.5 w-3.5" strokeWidth={2.5} />
              <span className="hidden sm:inline">退出</span>
            </button>
          </>
        )}
      </div>

      <FavoritesDrawer
        open={favOpen}
        onClose={() => setFavOpen(false)}
        items={favorites}
        loading={favoritesLoading}
      />
    </>
  )
}
