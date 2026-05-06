import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Check, ChevronDown, ChevronLeft, ChevronRight, Search } from 'lucide-react'
import type { UnifiedListing, UnifiedSearchApiResponse } from './mockGuitars'

const PLACEHOLDER_IMG =
  'data:image/svg+xml,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480"><rect fill="#f1f5f9" width="640" height="480"/><text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#94a3b8" font-family="system-ui" font-size="18">No image</text></svg>',
  )

function formatUsdPretty(amount: number): string {
  return `$${amount.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

function formatCnyFromServer(amount: number): string {
  return `¥${amount.toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })}`
}

type CurrencyToggleProps = {
  currency: 'USD' | 'CNY'
  onChange: (c: 'USD' | 'CNY') => void
  compact?: boolean
}

function CurrencyToggle({ currency, onChange, compact }: CurrencyToggleProps) {
  const isCny = currency === 'CNY'

  return (
    <div
      className={`flex flex-col items-end gap-1 ${compact ? 'scale-95' : ''}`}
      role="group"
      aria-label="标价货币"
    >
      <span className="text-[10px] font-medium uppercase tracking-wider text-slate-400">
        标价
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={isCny}
        aria-label={isCny ? '当前为人民币，点击切换到美元' : '当前为美元，点击切换到人民币'}
        onClick={() => onChange(isCny ? 'USD' : 'CNY')}
        className="relative h-10 w-[136px] shrink-0 rounded-full border border-slate-200/90 bg-slate-100/90 p-[3px] shadow-sm transition-shadow duration-200 hover:shadow-md dark:border-slate-600 dark:bg-slate-800"
      >
        <span
          className={`pointer-events-none absolute top-[3px] h-[calc(100%-6px)] w-[calc(50%-6px)] rounded-full bg-white shadow-sm ring-1 ring-slate-200/80 transition-[left] duration-200 ease-out dark:bg-slate-950 dark:ring-slate-700 ${
            isCny ? 'left-[calc(50%+3px)]' : 'left-[3px]'
          }`}
        />
        <span className="relative z-10 flex h-full items-stretch">
          <span
            className={`flex flex-1 items-center justify-center gap-0.5 text-[11px] font-semibold tracking-tight ${
              !isCny ? 'text-slate-900 dark:text-slate-100' : 'text-slate-500'
            }`}
          >
            USD <span className="text-xs opacity-80">$</span>
          </span>
          <span
            className={`flex flex-1 items-center justify-center gap-0.5 text-[11px] font-semibold tracking-tight ${
              isCny ? 'text-slate-900 dark:text-slate-100' : 'text-slate-500'
            }`}
          >
            CNY <span className="text-xs opacity-80">¥</span>
          </span>
        </span>
      </button>
    </div>
  )
}

type UnifiedPriceProps = {
  priceUsd: number | null
  priceCny: number | null
  currency: 'USD' | 'CNY'
}

/** 使用后端已换算的 ``price_usd`` / ``price_cny`` 切换展示 */
function UnifiedPriceDisplay({ priceUsd, priceCny, currency }: UnifiedPriceProps) {
  const usdLine = priceUsd != null ? formatUsdPretty(priceUsd) : '—'
  const cnyLine = priceCny != null ? formatCnyFromServer(priceCny) : '—'
  const showCny = currency === 'CNY'
  const showUsd = currency === 'USD'

  return (
    <span className="relative inline-grid min-h-[1.35em] place-items-start">
      <span
        className={`col-start-1 row-start-1 font-bold tabular-nums tracking-tight text-[#a91b16] transition-[opacity,transform] duration-200 ease-out dark:text-red-400 ${
          showUsd ? 'opacity-100' : 'pointer-events-none opacity-0 [transform:translateY(2px)]'
        }`}
        aria-hidden={!showUsd}
      >
        {usdLine}
      </span>
      <span
        className={`col-start-1 row-start-1 font-bold tabular-nums tracking-tight text-[#a91b16] transition-[opacity,transform] duration-200 ease-out dark:text-red-400 ${
          showCny ? 'opacity-100' : 'pointer-events-none opacity-0 [transform:translateY(-2px)]'
        }`}
        aria-hidden={!showCny}
      >
        {cnyLine}
      </span>
    </span>
  )
}

type SearchClusterProps = {
  compact?: boolean
  query: string
  setQuery: (q: string) => void
  loading: boolean
  onSubmitSearch: () => void
}

/** 当前页 ±2；无更多页时右侧不延伸 */
type SortOrder = 'default' | 'price_asc' | 'price_desc'

const SORT_LABELS: Record<SortOrder, string> = {
  default: '默认排序',
  price_asc: '价格：从低到高',
  price_desc: '价格：从高到低',
}

type SortOrderMenuProps = {
  value: SortOrder
  onChange: (order: SortOrder) => void
  disabled?: boolean
}

/** Google 搜索工具风格：无边框灰字按钮 + 浮层菜单 */
function SortOrderMenu({ value, onChange, disabled }: SortOrderMenuProps) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const listboxId = useId()

  useEffect(() => {
    if (!open) return
    const close = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', close, true)
    return () => document.removeEventListener('pointerdown', close, true)
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  useEffect(() => {
    if (disabled) setOpen(false)
  }, [disabled])

  const options = (Object.keys(SORT_LABELS) as SortOrder[]).map((key) => ({
    key,
    label: SORT_LABELS[key],
  }))

  return (
    <div ref={rootRef} className="relative flex justify-center">
      <button
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={listboxId}
        onClick={() => {
          if (!disabled) setOpen((o) => !o)
        }}
        className="inline-flex items-center gap-0.5 rounded-md px-2 py-1.5 text-sm font-normal text-slate-600 transition-colors hover:bg-slate-100/90 disabled:cursor-not-allowed disabled:opacity-40 dark:text-slate-400 dark:hover:bg-slate-800/80"
      >
        <span className="select-none">{SORT_LABELS[value]}</span>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-slate-400 transition-transform duration-200 dark:text-slate-500 ${open ? 'rotate-180' : ''}`}
          strokeWidth={2}
          aria-hidden
        />
      </button>

      {open && (
        <ul
          id={listboxId}
          role="listbox"
          aria-label="排序方式"
          className="absolute left-1/2 top-full z-50 mt-1.5 min-w-[13.5rem] -translate-x-1/2 overflow-hidden rounded-xl border border-slate-200/90 bg-white py-1 shadow-lg shadow-slate-200/50 ring-1 ring-black/[0.04] dark:border-slate-700 dark:bg-slate-900 dark:shadow-black/40 dark:ring-white/[0.06]"
        >
          {options.map(({ key, label }) => {
            const selected = value === key
            return (
              <li key={key} role="presentation">
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors ${
                    selected
                      ? 'bg-slate-50 font-medium text-slate-900 dark:bg-slate-800/80 dark:text-slate-100'
                      : 'text-slate-700 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800/50'
                  }`}
                  onClick={() => {
                    onChange(key)
                    setOpen(false)
                  }}
                >
                  <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                    {selected ? (
                      <Check className="h-3.5 w-3.5 text-slate-600 dark:text-slate-300" strokeWidth={2.5} />
                    ) : null}
                  </span>
                  <span className="min-w-0 flex-1">{label}</span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function sortListingsByOrder(items: UnifiedListing[], order: SortOrder): UnifiedListing[] {
  if (order === 'default') return items
  const copy = [...items]
  copy.sort((a, b) => {
    const pa = a.price_cny
    const pb = b.price_cny
    const na = pa == null
    const nb = pb == null
    if (na && nb) return 0
    if (na) return 1
    if (nb) return -1
    if (order === 'price_asc') return pa - pb
    return pb - pa
  })
  return copy
}

function buildPageRange(current: number, hasMore: boolean): number[] {
  const spread = 2
  const start = Math.max(1, current - spread)
  const end = Math.max(current, hasMore ? current + spread : current)
  const out: number[] = []
  for (let p = start; p <= end; p++) out.push(p)
  return out
}

type PaginationBarProps = {
  currentPage: number
  hasMore: boolean
  loading: boolean
  onPageChange: (page: number) => void
}

function PaginationBar({ currentPage, hasMore, loading, onPageChange }: PaginationBarProps) {
  const pages = buildPageRange(currentPage, hasMore)
  const canPrev = currentPage > 1 && !loading
  const canNext = hasMore && !loading

  return (
    <nav
      className="mt-12 flex flex-wrap items-center justify-center gap-1 sm:gap-2"
      aria-label="分页"
    >
      <button
        type="button"
        disabled={!canPrev}
        onClick={() => onPageChange(currentPage - 1)}
        className="inline-flex h-9 w-9 items-center justify-center rounded-full text-slate-600 transition-colors hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent dark:text-slate-300 dark:hover:bg-slate-800 dark:disabled:hover:bg-transparent"
        aria-label="上一页"
      >
        <ChevronLeft className="h-5 w-5" strokeWidth={2} />
      </button>
      {pages.map((p) => {
        const active = p === currentPage
        return (
          <button
            key={p}
            type="button"
            disabled={loading}
            onClick={() => onPageChange(p)}
            className={`inline-flex min-w-[2.25rem] items-center justify-center rounded-full px-3 py-1.5 text-sm font-medium tabular-nums transition-colors ${
              active
                ? 'bg-slate-900 text-white shadow-sm dark:bg-white dark:text-slate-900'
                : 'text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800'
            } disabled:opacity-50`}
            aria-label={`第 ${p} 页`}
            aria-current={active ? 'page' : undefined}
          >
            {p}
          </button>
        )
      })}
      <button
        type="button"
        disabled={!canNext}
        onClick={() => onPageChange(currentPage + 1)}
        className="inline-flex h-9 w-9 items-center justify-center rounded-full text-slate-600 transition-colors hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent dark:text-slate-300 dark:hover:bg-slate-800 dark:disabled:hover:bg-transparent"
        aria-label="下一页"
      >
        <ChevronRight className="h-5 w-5" strokeWidth={2} />
      </button>
    </nav>
  )
}

function ResultsGridSkeleton() {
  const placeholders = Array.from({ length: 8 }, (_, i) => i)
  return (
    <section className="mt-4" aria-busy="true" aria-live="polite" aria-label="正在加载搜索结果">
      <p className="mb-4 text-center text-sm font-medium text-slate-500 dark:text-slate-400">
        加载中…
      </p>
      <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-6 lg:grid-cols-4">
        {placeholders.map((i) => (
          <li key={i} className="min-w-0">
            <div className="flex h-full min-w-0 flex-col overflow-hidden rounded-xl border border-slate-200/90 bg-white dark:border-slate-700/90 dark:bg-slate-900 md:rounded-2xl">
              <div className="aspect-[4/3] w-full animate-pulse bg-slate-200/90 dark:bg-slate-800" />
              <div className="flex flex-1 flex-col gap-2 p-3 sm:gap-2.5 sm:p-4 md:p-5">
                <div className="h-3.5 w-full animate-pulse rounded bg-slate-200/90 dark:bg-slate-800" />
                <div className="h-3.5 w-[88%] animate-pulse rounded bg-slate-200/90 dark:bg-slate-800" />
                <div className="flex flex-wrap gap-1.5 pt-0.5">
                  <div className="h-5 w-14 animate-pulse rounded-full bg-slate-200/90 dark:bg-slate-800" />
                  <div className="h-5 w-12 animate-pulse rounded-full bg-slate-200/90 dark:bg-slate-800" />
                </div>
                <div className="mt-1 h-4 w-24 animate-pulse rounded bg-slate-200/90 dark:bg-slate-800" />
              </div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}

function SearchCluster({
  compact,
  query,
  setQuery,
  loading,
  onSubmitSearch,
}: SearchClusterProps) {
  const py = compact ? 'py-2.5 pl-11 pr-4' : 'py-3 pl-12 pr-5'
  const iconSize = compact ? 'h-4 w-4' : 'h-[1.125rem] w-[1.125rem]'
  const iconLeft = compact ? 'left-3.5' : 'left-4'

  return (
    <div className={`mx-auto w-full ${compact ? 'max-w-2xl' : 'max-w-[min(100%,36rem)]'}`}>
      <form
        className="flex flex-col items-stretch gap-6"
        onSubmit={(e) => {
          e.preventDefault()
          onSubmitSearch()
        }}
      >
        <label className="sr-only" htmlFor={compact ? 'search-compact' : 'search-main'}>
          搜索吉他
        </label>
        {/* Google 风格：整条输入条随 hover / focus 柔和升降投影 */}
        <div
          className={`group relative flex w-full items-center rounded-full border border-slate-200/90 bg-white shadow-sm transition-all duration-200 ease-out hover:border-slate-300 hover:shadow-md focus-within:border-slate-300 focus-within:shadow-md dark:border-slate-600 dark:bg-slate-900 dark:hover:border-slate-500 dark:focus-within:border-slate-500 ${
            loading ? 'opacity-80' : ''
          }`}
        >
          <Search
            className={`pointer-events-none absolute ${iconLeft} top-1/2 ${iconSize} -translate-y-1/2 text-slate-400 transition-colors duration-200 group-focus-within:text-slate-500 dark:text-slate-500`}
            strokeWidth={2}
            aria-hidden
          />
          <input
            id={compact ? 'search-compact' : 'search-main'}
            type="search"
            autoComplete="off"
            placeholder="例如：Fender Mustang"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={loading}
            className={`min-w-0 flex-1 rounded-full border-0 bg-transparent text-slate-800 outline-none ring-0 transition-colors placeholder:text-slate-400 focus:ring-0 disabled:cursor-not-allowed dark:text-slate-100 dark:placeholder:text-slate-500 ${compact ? 'text-[15px]' : 'text-base sm:text-[17px]'} ${py}`}
          />
        </div>

        {/* 单个黑色圆角按钮（Google 系：干净无衬线、适度字重与字距） */}
        <div className="flex justify-center">
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className={`inline-flex items-center justify-center rounded-full bg-zinc-950 px-7 text-white shadow-sm ring-1 ring-black/10 transition-[transform,box-shadow,background-color] duration-200 ease-out hover:bg-zinc-900 hover:shadow-md active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-zinc-300 disabled:text-zinc-500 disabled:shadow-none disabled:ring-zinc-300/40 ${compact ? 'min-h-[2.25rem] py-2 text-[13px] font-medium leading-none tracking-[0.01em]' : 'min-h-[2.75rem] py-2.5 text-sm font-medium leading-none tracking-[0.01em]'}`}
            style={{ fontFamily: 'system-ui, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif' }}
          >
            {loading ? 'Searching…' : 'Search'}
          </button>
        </div>
      </form>
    </div>
  )
}

function App() {
  const [query, setQuery] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState<string | null>(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const [listings, setListings] = useState<UnifiedListing[]>([])
  const [sortOrder, setSortOrder] = useState<SortOrder>('default')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const displayedListings = useMemo(
    () => sortListingsByOrder(listings, sortOrder),
    [listings, sortOrder],
  )

  const [currency, setCurrency] = useState<'USD' | 'CNY'>('USD')
  const [exchangeRate, setExchangeRate] = useState<number | null>(null)
  const [rateLoading, setRateLoading] = useState(true)
  const [rateError, setRateError] = useState(false)

  useEffect(() => {
    let cancelled = false
    setRateLoading(true)
    setRateError(false)
    fetch('/api/exchange-rate')
      .then(async (res) => {
        if (!res.ok) throw new Error(await res.text())
        return res.json() as Promise<{ rate: number }>
      })
      .then((data) => {
        if (!cancelled) setExchangeRate(data.rate)
      })
      .catch(() => {
        if (!cancelled) {
          setExchangeRate(null)
          setRateError(true)
        }
      })
      .finally(() => {
        if (!cancelled) setRateLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const rateReady = !rateLoading && exchangeRate != null && !rateError

  const fetchSearchPage = async (q: string, page: number) => {
    const trimmed = q.trim()
    if (!trimmed) return

    setListings([])
    setLoading(true)
    setError(null)

    try {
      const qs = new URLSearchParams({
        q: trimmed,
        page: String(page),
      })
      const res = await fetch(`/api/search?${qs.toString()}`)
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `请求失败 (${res.status})`)
      }
      const data: UnifiedSearchApiResponse = await res.json()
      setListings(data.results)
      setCurrentPage(data.page ?? page)
      setHasMore(Boolean(data.has_more))
      requestAnimationFrame(() => {
        window.scrollTo({ top: 0, behavior: 'smooth' })
      })
    } catch (e) {
      setListings([])
      setError(e instanceof Error ? e.message : '网络错误')
    } finally {
      setLoading(false)
    }
  }

  /** 新关键词搜索：回到第 1 页 */
  const runSearchWithQuery = (q: string) => {
    const trimmed = q.trim()
    if (!trimmed) return
    setSubmittedQuery(trimmed)
    setCurrentPage(1)
    setSortOrder('default')
    void fetchSearchPage(trimmed, 1)
  }

  const handleSubmitSearch = () => {
    runSearchWithQuery(query)
  }

  const handlePageChange = (page: number) => {
    if (!submittedQuery || page < 1 || loading) return
    if (page === currentPage) return
    setCurrentPage(page)
    void fetchSearchPage(submittedQuery, page)
  }

  const showResults = submittedQuery !== null

  return (
    <div className="min-h-svh bg-slate-50 text-slate-900 antialiased dark:bg-slate-950 dark:text-slate-100">
      {!showResults ? (
        <header className="border-b border-slate-200/60 bg-white/80 px-4 py-3 backdrop-blur-sm dark:border-slate-800 dark:bg-slate-950/80">
          <div className="mx-auto flex max-w-5xl items-center justify-end">
            <CurrencyToggle currency={currency} onChange={setCurrency} />
          </div>
        </header>
      ) : (
        <header className="sticky top-0 z-40 border-b border-slate-200/70 bg-white/90 px-4 py-3 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/90">
          <div className="mx-auto flex max-w-6xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
            <div className="flex shrink-0 items-center justify-between gap-3 sm:block">
              <p className="text-sm font-medium tracking-tight text-slate-700 dark:text-slate-200">
                Guitar Search
              </p>
              {!rateLoading && rateReady && exchangeRate != null && (
                <p className="hidden text-[11px] text-slate-400 sm:block dark:text-slate-500">
                  1 USD ≈ {exchangeRate.toFixed(4)} CNY
                </p>
              )}
              <div className="sm:hidden">
                <CurrencyToggle currency={currency} onChange={setCurrency} compact />
              </div>
            </div>
            <div className="min-w-0 flex-1">
              <SearchCluster
                compact
                query={query}
                setQuery={setQuery}
                loading={loading}
                onSubmitSearch={handleSubmitSearch}
              />
            </div>
            <div className="hidden shrink-0 sm:block">
              <CurrencyToggle currency={currency} onChange={setCurrency} compact />
            </div>
          </div>
        </header>
      )}

      {!showResults ? (
        <main className="mx-auto flex min-h-[calc(100svh-3.25rem)] max-w-5xl flex-col items-center justify-center px-6 pb-24 pt-8 sm:px-8">
          <div className="mb-12 text-center">
            <h1 className="text-5xl font-light tracking-tight text-slate-800 sm:text-6xl dark:text-white">
              Guitar Search
            </h1>
            {!rateLoading && rateReady && exchangeRate != null && (
              <p className="mt-4 text-[11px] text-slate-400 dark:text-slate-500">
                参考汇率 1 USD ≈ {exchangeRate.toFixed(4)} CNY
              </p>
            )}
            {rateLoading && (
              <p className="mx-auto mt-3 h-3 w-36 animate-pulse rounded bg-slate-200/90 dark:bg-slate-800" />
            )}
            {!rateLoading && rateError && (
              <p className="mt-3 text-[11px] text-amber-600 dark:text-amber-400">
                汇率暂不可用，仅显示原价
              </p>
            )}
          </div>
          <SearchCluster
            query={query}
            setQuery={setQuery}
            loading={loading}
            onSubmitSearch={handleSubmitSearch}
          />
        </main>
      ) : (
        <main className="mx-auto max-w-6xl px-4 pb-20 pt-10 sm:px-6">
          {submittedQuery && (
            <p className="mb-2 text-center text-sm text-slate-500 dark:text-slate-400">
              Reverb + Digimart + GuitarGuitar + Ishibashi + Swee Lee 搜索结果 · 「{submittedQuery}」
              {` · 第 ${currentPage} 页`}
              {loading && ' · 加载中…'}
            </p>
          )}
          {error && (
            <p className="mb-6 text-center text-sm text-red-600 dark:text-red-400" role="alert">
              {error}
            </p>
          )}
          {!error && (
            <p className="mb-6 text-center text-xs text-slate-400 dark:text-slate-500">
              标价由后端换算（Frankfurter，含 JPY / GBP / SGD→CNY）；切换 USD/CNY 仅改变展示币种。
            </p>
          )}

          {submittedQuery && !error && (
            <div
              className="mx-auto mb-6 flex flex-col gap-3 sm:mb-8 sm:flex-row sm:items-center sm:justify-between sm:gap-4"
              aria-live="polite"
            >
              <p className="min-h-[1.375rem] flex-1 text-center text-sm font-medium text-slate-500 dark:text-slate-400 sm:min-w-0 sm:text-left">
                {loading && '正在搜寻全球货源…'}
                {!loading && listings.length > 0 && (
                  <>🔍 为您找到 {listings.length} 个全球淘琴结果</>
                )}
                {!loading && listings.length === 0 && '未找到相关吉他，尝试换个关键词试试？'}
              </p>
              {listings.length > 0 && (
                <div className="flex shrink-0 flex-col items-center gap-1 sm:items-end">
                  <span className="text-[11px] font-medium uppercase tracking-wider text-slate-400 dark:text-slate-500">
                    排序
                  </span>
                  <SortOrderMenu value={sortOrder} onChange={setSortOrder} disabled={loading} />
                </div>
              )}
            </div>
          )}

          {loading && !error && <ResultsGridSkeleton />}

          {!loading && listings.length > 0 && (
            <section className="mt-4" aria-label="搜索结果">
              <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-6 lg:grid-cols-4">
                {displayedListings.map((item, index) => (
                  <li key={item.url ? `${item.url}-${index}` : `row-${index}`} className="min-w-0">
                    <article className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-slate-200/90 bg-white shadow-sm transition-shadow duration-200 hover:shadow-md md:rounded-2xl dark:border-slate-700/90 dark:bg-slate-900">
                      <div className="aspect-[4/3] w-full shrink-0 overflow-hidden bg-slate-100 dark:bg-slate-800">
                        <img
                          src={item.image || PLACEHOLDER_IMG}
                          alt=""
                          className="h-full w-full object-cover object-center"
                          loading="lazy"
                          width={640}
                          height={480}
                        />
                      </div>
                      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-1.5 p-3 text-left sm:gap-2 sm:p-4 md:gap-2.5 md:p-5">
                        <h2 className="line-clamp-2 min-h-0 text-sm font-medium leading-snug text-slate-900 sm:text-[15px] dark:text-slate-50">
                          {item.title}
                        </h2>
                        <p className="flex shrink-0 flex-wrap items-center gap-1.5">
                          <span className="inline-flex rounded-full border border-slate-200/80 bg-slate-50 px-2 py-0.5 text-[10px] font-medium text-slate-600 sm:px-2.5 sm:text-xs dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300">
                            {item.source}
                          </span>
                          <span
                            className={`inline-flex rounded-full border px-2 py-0.5 text-xs ${
                              item.condition === '全新'
                                ? 'border-emerald-200/90 bg-emerald-50 text-emerald-700 dark:border-emerald-800/60 dark:bg-emerald-950/45 dark:text-emerald-400'
                                : 'border-slate-200/90 bg-slate-100 text-slate-600 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-400'
                            }`}
                          >
                            {item.condition === '全新' ? '全新' : '二手'}
                          </span>
                        </p>
                        <p className="flex min-w-0 flex-col gap-0.5 text-xs tabular-nums text-slate-700 sm:flex-row sm:flex-wrap sm:items-baseline sm:gap-x-1 sm:text-sm dark:text-slate-200">
                          <span className="shrink-0 text-slate-500 dark:text-slate-400">标价</span>
                          <span className="min-w-0 max-w-full whitespace-nowrap text-sm font-bold sm:text-base">
                            <UnifiedPriceDisplay
                              priceUsd={item.price_usd}
                              priceCny={item.price_cny}
                              currency={currency}
                            />
                          </span>
                        </p>
                        {item.url ? (
                          <p className="mt-auto shrink-0 pt-1.5 sm:pt-2">
                            <a
                              href={item.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-xs font-medium text-blue-700 underline-offset-2 hover:underline sm:text-sm dark:text-blue-400"
                            >
                              前往原网页
                            </a>
                          </p>
                        ) : null}
                      </div>
                    </article>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {submittedQuery && !error && (
            <PaginationBar
              currentPage={currentPage}
              hasMore={hasMore}
              loading={loading}
              onPageChange={handlePageChange}
            />
          )}
        </main>
      )}
    </div>
  )
}

export default App
