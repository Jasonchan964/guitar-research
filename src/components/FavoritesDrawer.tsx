import { Link } from 'react-router-dom'
import { Heart, Loader2, X } from 'lucide-react'
import type { FavoriteRow } from '../authContext'

type Props = {
  open: boolean
  onClose: () => void
  items: FavoriteRow[]
  loading: boolean
}

export default function FavoritesDrawer({ open, onClose, items, loading }: Props) {
  if (!open) return null

  return (
    <div className="fixed inset-0 z-[85]">
      <button
        type="button"
        aria-label="关闭收藏夹"
        className="absolute inset-0 bg-slate-900/25 backdrop-blur-[2px] dark:bg-slate-950/50"
        onClick={onClose}
      />
      <aside
        className="absolute right-0 top-0 flex h-full w-full max-w-md flex-col border-l border-rose-100/80 bg-gradient-to-b from-white/98 to-rose-50/30 shadow-2xl shadow-rose-200/20 dark:border-rose-900/40 dark:from-slate-950/98 dark:to-rose-950/20"
        role="dialog"
        aria-modal="true"
        aria-label="我的收藏"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-rose-100/70 px-4 py-3.5 dark:border-rose-900/40">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-2xl bg-rose-100/90 text-rose-500 dark:bg-rose-950/60 dark:text-rose-300">
              <Heart className="h-4 w-4 fill-rose-400/90" strokeWidth={2} />
            </span>
            <div>
              <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">我的收藏</p>
              <p className="text-[11px] text-slate-500 dark:text-slate-400">点击卡片回到站内详情</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full text-slate-500 transition-colors hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/50"
            aria-label="关闭"
          >
            <X className="h-4 w-4" strokeWidth={2.5} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
          {loading ? (
            <div className="flex flex-col items-center justify-center gap-2 py-16 text-sm text-slate-500">
              <Loader2 className="h-6 w-6 animate-spin text-rose-400" strokeWidth={2} />
              加载收藏…
            </div>
          ) : items.length === 0 ? (
            <p className="py-16 text-center text-sm text-slate-500 dark:text-slate-400">
              暂无收藏，去搜索页点亮爱心吧
            </p>
          ) : (
            <ul className="space-y-2.5">
              {items.map((f) => (
                <li key={f.id}>
                  <Link
                    to={`/guitar?${new URLSearchParams({ url: f.original_url, platform: f.platform }).toString()}`}
                    onClick={onClose}
                    className="flex gap-3 rounded-2xl border border-slate-200/70 bg-white/90 p-2.5 shadow-sm transition-[box-shadow,transform] hover:shadow-md active:scale-[0.99] dark:border-slate-700/70 dark:bg-slate-900/80"
                  >
                    <div className="relative h-[4.5rem] w-[4.5rem] shrink-0 overflow-hidden rounded-xl bg-slate-100 dark:bg-slate-800">
                      <img
                        src={f.image_url}
                        alt=""
                        className="h-full w-full object-cover"
                        loading="lazy"
                      />
                    </div>
                    <div className="min-w-0 flex-1 py-0.5">
                      <p className="line-clamp-2 text-sm font-medium leading-snug text-slate-900 dark:text-slate-50">
                        {f.title}
                      </p>
                      <p className="mt-1 text-xs font-semibold tabular-nums text-[#a91b16] dark:text-rose-400">
                        ¥{Number(f.price_cny).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}
                      </p>
                      <p className="mt-1 truncate text-[11px] text-slate-400 dark:text-slate-500">
                        {f.platform}
                      </p>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </div>
  )
}
