import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ChevronLeft, Loader2 } from 'lucide-react'
import type { FavoriteRow } from './authContext'
import { useAuth } from './authContext'
import type { UnifiedListing } from './mockGuitars'
import CurrencyToggle from './components/CurrencyToggle'
import NavUserMenu from './components/NavUserMenu'
import SearchResultListingCard from './components/SearchResultListingCard'

function favoriteRowToListing(f: FavoriteRow): UnifiedListing {
  return {
    title: f.title,
    image: f.image_url,
    price_usd: null,
    price_cny: f.price_cny,
    source: f.platform,
    url: f.original_url,
    condition: '二手',
  }
}

function EmptyGuitarIllustration() {
  return (
    <svg
      className="mx-auto h-40 w-40 text-rose-300/90 dark:text-rose-400/50"
      viewBox="0 0 200 160"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <ellipse cx="100" cy="140" rx="72" ry="10" fill="currentColor" opacity="0.15" />
      <path
        d="M42 78c0-22 18-40 40-40h36c22 0 40 18 40 40v24c0 8-6 14-14 14H56c-8 0-14-6-14-14V78z"
        fill="#FFF5F7"
        stroke="currentColor"
        strokeWidth="2.5"
      />
      <circle cx="72" cy="72" r="28" fill="#FFE4EC" stroke="currentColor" strokeWidth="2" />
      <circle cx="72" cy="72" r="10" fill="#FDA4AF" opacity="0.6" />
      <path
        d="M104 58h48c12 0 22 10 22 22v12c0 6-5 11-11 11h-42c-8 0-14-6-14-14V72c0-8 6-14 14-14z"
        fill="#FFF1F2"
        stroke="currentColor"
        strokeWidth="2"
      />
      <path d="M118 70h36M118 82h28" stroke="currentColor" strokeWidth="2" strokeLinecap="round" opacity="0.35" />
      <rect x="46" y="104" width="108" height="14" rx="7" fill="#FBCFE8" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}

export default function FavoritesPage() {
  const navigate = useNavigate()
  const { user, openAuth, favorites, favoritesLoading, refreshFavorites } = useAuth()
  const [currency, setCurrency] = useState<'USD' | 'CNY'>('USD')

  const handleBack = () => {
    if (window.history.length > 1) {
      navigate(-1)
    } else {
      navigate('/')
    }
  }

  useEffect(() => {
    void refreshFavorites()
  }, [refreshFavorites])

  const listings: UnifiedListing[] = favorites.map(favoriteRowToListing)

  return (
    <div className="min-h-svh bg-gradient-to-b from-slate-50 via-rose-50/25 to-amber-50/15 text-slate-900 antialiased [-webkit-overflow-scrolling:touch] dark:from-slate-950 dark:via-rose-950/15 dark:to-slate-950 dark:text-slate-100">
      <header className="sticky top-0 z-40 border-b border-rose-100/60 bg-white/85 px-4 py-3 shadow-sm shadow-rose-100/30 backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/90 dark:shadow-none">
        <div className="mx-auto grid max-w-7xl grid-cols-[1fr_auto_1fr] items-center gap-3">
          <button
            type="button"
            onClick={handleBack}
            className="inline-flex shrink-0 items-center gap-1 justify-self-start text-sm font-medium text-slate-600 transition-colors hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
          >
            <ChevronLeft className="h-4 w-4" strokeWidth={2.5} aria-hidden />
            返回
          </button>
          <h1 className="justify-self-center text-center text-base font-semibold tracking-tight text-slate-900 dark:text-slate-50 sm:text-lg">
            我的收藏
          </h1>
          <div className="flex shrink-0 items-center justify-end gap-2 justify-self-end">
            <NavUserMenu compact />
            <CurrencyToggle currency={currency} onChange={setCurrency} compact />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 pb-16 pt-8 sm:px-6">
        {!user ? (
          <div className="mx-auto flex max-w-md flex-col items-center rounded-3xl border border-rose-100/80 bg-white/80 px-6 py-14 text-center shadow-sm dark:border-slate-700 dark:bg-slate-900/80">
            <EmptyGuitarIllustration />
            <p className="mt-6 text-sm font-medium text-slate-700 dark:text-slate-200">登录后可查看与管理收藏</p>
            <button
              type="button"
              onClick={() => openAuth('login')}
              className="mt-6 inline-flex items-center justify-center rounded-full bg-gradient-to-r from-rose-500 to-pink-500 px-8 py-3 text-sm font-semibold text-white shadow-md shadow-rose-300/40 transition-[transform,box-shadow] hover:shadow-lg active:scale-[0.98] dark:shadow-rose-900/40"
            >
              注册 / 登录
            </button>
          </div>
        ) : favoritesLoading ? (
          <div className="flex flex-col items-center justify-center gap-3 py-24 text-sm text-slate-500 dark:text-slate-400">
            <Loader2 className="h-8 w-8 animate-spin text-rose-400" strokeWidth={2} aria-hidden />
            加载收藏…
          </div>
        ) : listings.length === 0 ? (
          <div className="mx-auto flex max-w-lg flex-col items-center px-4 py-12 text-center">
            <EmptyGuitarIllustration />
            <p className="mt-8 text-base font-medium text-slate-700 dark:text-slate-200">
              目前还没有收藏任何吉他
            </p>
            <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">去搜索页点亮爱心，把好琴存进这里吧</p>
            <Link
              to="/"
              className="mt-8 inline-flex items-center justify-center rounded-full border border-rose-200/90 bg-white px-8 py-3 text-sm font-semibold text-rose-700 shadow-sm transition-[transform,box-shadow] hover:bg-rose-50 hover:shadow-md active:scale-[0.98] dark:border-rose-800 dark:bg-slate-900 dark:text-rose-200 dark:hover:bg-rose-950/40"
            >
              去逛逛
            </Link>
          </div>
        ) : (
          <section aria-label="收藏列表">
            <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-5 lg:grid-cols-4 lg:gap-6">
              {listings.map((item, index) => (
                <SearchResultListingCard
                  key={item.url ? `${item.url}-${index}` : `fav-${index}`}
                  item={item}
                  currency={currency}
                />
              ))}
            </ul>
          </section>
        )}
      </main>
    </div>
  )
}
