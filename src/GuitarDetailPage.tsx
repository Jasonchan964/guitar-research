import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { GuitarDetailApi } from './mockGuitars'
import FavoriteHeart from './components/FavoriteHeart'
import NavUserMenu from './components/NavUserMenu'

const PLACEHOLDER_IMG =
  'data:image/svg+xml,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480"><rect fill="#f1f5f9" width="640" height="480"/><text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#94a3b8" font-family="system-ui" font-size="18">No image</text></svg>',
  )

const SOURCE_PILL_CLASS: Record<string, string> = {
  Reverb: 'border-[#E07A5F]/25 bg-[#FFF0EB] text-[#E07A5F] dark:border-[#E07A5F]/30 dark:bg-[#FFF0EB]/95',
  Digimart: 'border-[#D15C7D]/25 bg-[#FFF0F5] text-[#D15C7D] dark:border-[#D15C7D]/30 dark:bg-[#FFF0F5]/95',
  GuitarGuitar: 'border-[#2D9B75]/25 bg-[#E6F7F0] text-[#2D9B75] dark:border-[#2D9B75]/30 dark:bg-[#E6F7F0]/95',
  Ishibashi: 'border-[#2B7BC6]/25 bg-[#E6F2FF] text-[#2B7BC6] dark:border-[#2B7BC6]/30 dark:bg-[#E6F2FF]/95',
  'Swee Lee': 'border-[#B8860B]/25 bg-[#FFF9E6] text-[#B8860B] dark:border-[#B8860B]/30 dark:bg-[#FFF9E6]/95',
}
const SOURCE_PILL_FALLBACK =
  'border-[#CCCCCC]/40 bg-[#FAFAFA] text-[#CCCCCC] dark:border-slate-600 dark:bg-slate-800 dark:text-slate-400'

function formatCnyPretty(amount: number): string {
  return `¥${amount.toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })}`
}

const DETAIL_BACK_BTN_CLASS =
  'inline-flex shrink-0 items-center gap-1 text-sm font-medium text-slate-600 transition-colors hover:text-slate-900 dark:text-slate-400 dark:hover:text-white'

function DetailBackButton() {
  const navigate = useNavigate()
  const handleBack = () => {
    if (window.history.length > 1) {
      navigate(-1)
    } else {
      navigate('/')
    }
  }
  return (
    <button type="button" onClick={handleBack} className={DETAIL_BACK_BTN_CLASS}>
      <ChevronLeft className="h-4 w-4" strokeWidth={2.5} aria-hidden />
      返回
    </button>
  )
}

function GuitarDetailInvalidParams() {
  return (
    <div className="min-h-svh bg-slate-50 text-slate-900 antialiased dark:bg-slate-950 dark:text-slate-100">
      <header className="border-b border-slate-200/80 bg-white/95 px-4 py-3 backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/95">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3">
          <DetailBackButton />
          <NavUserMenu compact />
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-10 text-center text-sm text-slate-600 dark:text-slate-400">
        链接无效：缺少商品地址或平台信息
      </main>
    </div>
  )
}

function GuitarDetailLoaded({ url, platform }: { url: string; platform: string }) {
  const [data, setData] = useState<GuitarDetailApi | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [slideIndex, setSlideIndex] = useState(0)
  const scrollerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false

    const qs = new URLSearchParams({ url, platform })
    fetch(`/api/guitar/detail?${qs.toString()}`)
      .then(async (res) => {
        if (!res.ok) {
          const text = await res.text()
          let msg = text || `请求失败 (${res.status})`
          try {
            const j = JSON.parse(text) as { detail?: unknown }
            if (typeof j.detail === 'string') msg = j.detail
            else if (Array.isArray(j.detail) && typeof j.detail[0]?.msg === 'string')
              msg = j.detail.map((x: { msg: string }) => x.msg).join('；')
          } catch {
            /* keep msg */
          }
          throw new Error(msg)
        }
        return res.json() as Promise<GuitarDetailApi>
      })
      .then((body) => {
        if (!cancelled) setData(body)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : '加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [url, platform])

  const images = data?.images?.length ? data.images : data ? [PLACEHOLDER_IMG] : []

  const scrollToSlide = useCallback(
    (i: number) => {
      const el = scrollerRef.current
      if (!el || images.length === 0) return
      const next = (i + images.length) % images.length
      const w = el.clientWidth || 1
      el.scrollTo({ left: next * w, behavior: 'smooth' })
      setSlideIndex(next)
    },
    [images.length],
  )

  const onScrollSnap = useCallback(() => {
    const el = scrollerRef.current
    if (!el) return
    const w = el.clientWidth || 1
    setSlideIndex(Math.min(images.length - 1, Math.max(0, Math.round(el.scrollLeft / w))))
  }, [images.length])

  useEffect(() => {
    const el = scrollerRef.current
    if (el && data) el.scrollLeft = 0
  }, [data])

  const pillClass = data ? (SOURCE_PILL_CLASS[data.platform] ?? SOURCE_PILL_FALLBACK) : SOURCE_PILL_FALLBACK

  return (
    <div className="min-h-svh bg-slate-50 text-slate-900 antialiased dark:bg-slate-950 dark:text-slate-100">
      <header className="sticky top-0 z-40 border-b border-slate-200/80 bg-white/95 px-4 py-3 backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/95">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-x-3 gap-y-2">
          <DetailBackButton />
          {data ? (
            <span
              className={`inline-flex max-w-[min(100%,12rem)] truncate rounded-full border px-3 py-1 text-xs font-semibold sm:max-w-xs sm:text-sm ${pillClass}`}
            >
              {data.platform}
            </span>
          ) : null}
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <NavUserMenu compact />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 pb-28 pt-6 sm:px-6">
        {loading && (
          <div className="space-y-4" aria-busy="true">
            <div className="aspect-[4/3] w-full animate-pulse rounded-2xl bg-slate-200 dark:bg-slate-800" />
            <div className="h-6 w-3/4 animate-pulse rounded bg-slate-200 dark:bg-slate-800" />
            <div className="h-24 w-full animate-pulse rounded-xl bg-slate-200 dark:bg-slate-800" />
          </div>
        )}

        {!loading && error && (
          <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-6 text-center text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-300">
            {error}
          </div>
        )}

        {!loading && data && (
          <>
            <div className="relative overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-sm dark:border-slate-700/90 dark:bg-slate-900">
              <div
                ref={scrollerRef}
                onScroll={onScrollSnap}
                className="flex w-full snap-x snap-mandatory overflow-x-auto scroll-smooth [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
                tabIndex={0}
                role="region"
                aria-label="商品图片，可横向滑动"
              >
                {images.map((src, i) => (
                  <div
                    key={`${src}-${i}`}
                    className="w-full shrink-0 snap-center snap-always"
                  >
                    <div className="flex aspect-[4/3] max-h-[75vh] items-center justify-center bg-slate-100 dark:bg-slate-800">
                      <img
                        src={src}
                        alt=""
                        className="max-h-[75vh] max-w-full object-contain"
                        width={1600}
                        height={1200}
                        decoding="async"
                      />
                    </div>
                  </div>
                ))}
              </div>

              {images.length > 1 ? (
                <>
                  <button
                    type="button"
                    aria-label="上一张"
                    onClick={() => scrollToSlide(slideIndex - 1)}
                    className="absolute left-2 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full border border-white/50 bg-white/90 text-slate-800 shadow-md backdrop-blur-sm dark:border-slate-600/60 dark:bg-slate-900/90 dark:text-slate-100"
                  >
                    <ChevronLeft className="h-6 w-6" strokeWidth={2} />
                  </button>
                  <button
                    type="button"
                    aria-label="下一张"
                    onClick={() => scrollToSlide(slideIndex + 1)}
                    className="absolute right-2 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full border border-white/50 bg-white/90 text-slate-800 shadow-md backdrop-blur-sm dark:border-slate-600/60 dark:bg-slate-900/90 dark:text-slate-100"
                  >
                    <ChevronRight className="h-6 w-6" strokeWidth={2} />
                  </button>
                  <div className="pointer-events-none absolute bottom-3 left-0 right-0 flex justify-center gap-1.5">
                    {images.map((_, i) => (
                      <span
                        key={i}
                        className={`h-1.5 rounded-full transition-all ${
                          i === slideIndex ? 'w-6 bg-white shadow' : 'w-1.5 bg-white/50'
                        }`}
                      />
                    ))}
                  </div>
                </>
              ) : null}
            </div>

            <div className="relative mt-6 pr-11 sm:pr-12">
              <h1 className="text-xl font-semibold leading-snug text-slate-900 dark:text-slate-50 sm:text-2xl">
                {data.title}
              </h1>
              {data.buy_url ? (
                <FavoriteHeart
                  className="right-0 top-0"
                  item={{
                    title: data.title,
                    price_cny: data.price_cny,
                    image: data.images[0] ?? null,
                    original_url: data.buy_url,
                    platform: data.platform,
                  }}
                />
              ) : null}
            </div>

            <dl className="mt-4 space-y-2 text-sm">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <dt className="text-slate-500 dark:text-slate-400">人民币参考</dt>
                <dd className="text-lg font-bold tabular-nums text-[#a91b16] dark:text-red-400">
                  {data.price_cny != null ? formatCnyPretty(data.price_cny) : '—'}
                </dd>
              </div>
              {data.price_original ? (
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <dt className="text-slate-500 dark:text-slate-400">原站标价</dt>
                  <dd className="font-medium tabular-nums text-slate-800 dark:text-slate-100">
                    {data.price_original}
                  </dd>
                </div>
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                <dt className="text-slate-500 dark:text-slate-400">成色</dt>
                <dd>
                  <span
                    className={`inline-flex rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                      data.condition === '全新'
                        ? 'border-emerald-200/90 bg-emerald-50 text-emerald-700 dark:border-emerald-800/60 dark:bg-emerald-950/45 dark:text-emerald-400'
                        : 'border-slate-200/90 bg-slate-100 text-slate-600 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-400'
                    }`}
                  >
                    {data.condition === '全新' ? '全新' : '二手'}
                  </span>
                </dd>
              </div>
            </dl>

            {Object.keys(data.specs).length > 0 ? (
              <section className="mt-8">
                <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  参数规格
                </h2>
                <div className="overflow-hidden rounded-xl border border-slate-200/90 dark:border-slate-700/90">
                  <table className="w-full text-left text-sm">
                    <tbody>
                      {Object.entries(data.specs).map(([k, v]) => (
                        <tr
                          key={k}
                          className="border-b border-slate-100 last:border-0 dark:border-slate-800"
                        >
                          <th className="w-[36%] max-w-[10rem] whitespace-normal break-words bg-slate-50/80 px-3 py-2.5 font-medium text-slate-600 dark:bg-slate-900/80 dark:text-slate-300">
                            {k}
                          </th>
                          <td className="px-3 py-2.5 text-slate-800 dark:text-slate-100">{v}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}

            {data.description_html.trim() ? (
              <section className="mt-8">
                <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  商品描述
                </h2>
                <div
                  className="prose prose-sm prose-slate max-w-none dark:prose-invert prose-headings:scroll-mt-24 prose-img:max-w-full prose-img:rounded-lg prose-a:text-blue-600 prose-table:text-sm dark:prose-a:text-blue-400"
                  dangerouslySetInnerHTML={{ __html: data.description_html }}
                />
              </section>
            ) : null}

            <div className="fixed bottom-0 left-0 right-0 border-t border-slate-200/90 bg-white/95 p-4 backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/95">
              <div className="mx-auto max-w-3xl">
                {data.buy_url ? (
                  <a
                    href={data.buy_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex w-full items-center justify-center rounded-2xl bg-gradient-to-r from-blue-600 to-indigo-600 px-4 py-3.5 text-center text-sm font-semibold text-white shadow-lg ring-2 ring-blue-500/25 transition-[filter] hover:brightness-110 active:scale-[0.99] dark:from-blue-500 dark:to-indigo-500"
                  >
                    前往 {data.platform} 官网下单 ↗
                  </a>
                ) : null}
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  )
}

export default function GuitarDetailPage() {
  const [params] = useSearchParams()
  const url = (params.get('url') ?? '').trim()
  const platform = (params.get('platform') ?? '').trim()
  if (!url || !platform) {
    return <GuitarDetailInvalidParams />
  }
  return <GuitarDetailLoaded key={`${url}|${platform}`} url={url} platform={platform} />
}
