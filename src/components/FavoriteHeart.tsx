import { useCallback, useState } from 'react'
import { Heart } from 'lucide-react'
import { useAuth } from '../authContext'

const PLACEHOLDER_IMG =
  'data:image/svg+xml,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48" viewBox="0 0 64 48"><rect fill="#fce7f3" width="64" height="48"/></svg>',
  )

export type FavoriteTarget = {
  title: string
  price_cny: number | null
  image: string | null
  original_url: string
  platform: string
}

type Props = {
  item: FavoriteTarget
  className?: string
}

export default function FavoriteHeart({ item, className = '' }: Props) {
  const {
    user,
    openAuth,
    showToast,
    isFavorite,
    addFavorite,
    removeFavorite,
  } = useAuth()
  const [busy, setBusy] = useState(false)
  const [pop, setPop] = useState(false)

  const saved = isFavorite(item.original_url)

  const onClick = useCallback(
    async (e: React.MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (busy) return
      if (!user) {
        showToast('请先登录以收藏吉他')
        openAuth('login')
        return
      }
      const url = item.original_url.trim()
      if (!url) {
        showToast('缺少商品链接，无法收藏')
        return
      }
      setBusy(true)
      try {
        if (saved) {
          await removeFavorite({ originalUrl: url })
        } else {
          await addFavorite({
            title: item.title,
            price_cny: item.price_cny ?? 0,
            image_url: (item.image && item.image.trim()) || PLACEHOLDER_IMG,
            original_url: url,
            platform: item.platform,
          })
          setPop(true)
          window.setTimeout(() => setPop(false), 380)
        }
      } catch (err) {
        showToast(err instanceof Error ? err.message : '操作失败')
      } finally {
        setBusy(false)
      }
    },
    [addFavorite, busy, item, openAuth, removeFavorite, saved, showToast, user],
  )

  return (
    <button
      type="button"
      aria-label={saved ? '取消收藏' : '加入收藏'}
      aria-pressed={saved}
      disabled={busy}
      onClick={onClick}
      className={`group absolute z-10 inline-flex h-9 w-9 items-center justify-center rounded-full border border-white/80 bg-white/90 text-slate-500 shadow-md shadow-slate-900/10 backdrop-blur-sm transition-[transform,opacity] hover:scale-105 disabled:opacity-60 dark:border-slate-600/80 dark:bg-slate-900/85 dark:text-slate-300 dark:shadow-black/30 ${pop ? 'animate-heart-pop' : ''} ${className}`}
    >
      <Heart
        className={`h-[1.15rem] w-[1.15rem] transition-colors ${
          saved
            ? 'fill-rose-400 text-rose-500 drop-shadow-sm dark:fill-rose-400 dark:text-rose-300'
            : 'fill-transparent text-slate-400 group-hover:text-rose-400 dark:text-slate-500'
        }`}
        strokeWidth={saved ? 0 : 2}
      />
    </button>
  )
}
