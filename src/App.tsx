import { useEffect, useState } from 'react'
import type { ReverbListing, ReverbSearchApiResponse } from './mockGuitars'

const PLACEHOLDER_IMG =
  'data:image/svg+xml,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480"><rect fill="#e4e4e7" width="640" height="480"/><text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#71717a" font-family="system-ui" font-size="18">No image</text></svg>',
  )

/** 解析 Reverb `1234.56 USD` */
function parseUsdAmount(raw: string): number | null {
  const m = raw.trim().match(/^([\d,.]+)\s+USD$/i)
  if (!m) return null
  const n = parseFloat(m[1].replace(/,/g, ''))
  return Number.isNaN(n) ? null : n
}

function formatUsdPretty(amount: number): string {
  return `$${amount.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

function formatCnyPretty(amountUsd: number, rate: number): string {
  const cny = amountUsd * rate
  return `¥${cny.toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })}`
}

type CurrencyToggleProps = {
  currency: 'USD' | 'CNY'
  onChange: (c: 'USD' | 'CNY') => void
}

/** iOS 风格滑动开关：USD $ / CNY ¥ */
function CurrencyToggle({ currency, onChange }: CurrencyToggleProps) {
  const isCny = currency === 'CNY'

  return (
    <div
      className="flex flex-col items-end gap-1"
      role="group"
      aria-label="标价货币"
    >
      <span className="text-[10px] font-medium uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
        标价
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={isCny}
        aria-label={isCny ? '当前为人民币，点击切换到美元' : '当前为美元，点击切换到人民币'}
        onClick={() => onChange(isCny ? 'USD' : 'CNY')}
        className="relative h-11 w-[148px] shrink-0 rounded-[13px] bg-gradient-to-b from-zinc-200 to-zinc-300 p-[3px] shadow-inner ring-1 ring-zinc-400/40 transition-shadow duration-300 hover:ring-zinc-400/60 active:scale-[0.98] dark:from-zinc-700 dark:to-zinc-800 dark:ring-zinc-600/50 dark:hover:ring-zinc-500/60"
      >
        <span
          className={`pointer-events-none absolute top-[3px] h-[calc(100%-6px)] w-[calc(50%-6px)] rounded-[10px] bg-white shadow-md ring-1 ring-black/5 transition-[left,box-shadow] duration-300 ease-[cubic-bezier(0.34,1.2,0.64,1)] dark:bg-zinc-950 dark:shadow-black/50 dark:ring-white/10 ${
            isCny ? 'left-[calc(50%+3px)]' : 'left-[3px]'
          }`}
        />
        <span className="relative z-10 flex h-full items-stretch">
          <span
            className={`flex flex-1 items-center justify-center gap-0.5 text-[11px] font-semibold tracking-tight transition-colors duration-300 ${
              !isCny ? 'text-zinc-900 dark:text-white' : 'text-zinc-500 dark:text-zinc-500'
            }`}
          >
            USD <span className="text-xs opacity-80">$</span>
          </span>
          <span
            className={`flex flex-1 items-center justify-center gap-0.5 text-[11px] font-semibold tracking-tight transition-colors duration-300 ${
              isCny ? 'text-zinc-900 dark:text-white' : 'text-zinc-500 dark:text-zinc-500'
            }`}
          >
            CNY <span className="text-xs opacity-80">¥</span>
          </span>
        </span>
      </button>
    </div>
  )
}

type PriceCrossfadeProps = {
  rawPrice: string
  currency: 'USD' | 'CNY'
  rate: number | null
  rateReady: boolean
}

/** 双图层交叉淡入淡出，避免数字跳变闪烁 */
function PriceCrossfade({ rawPrice, currency, rate, rateReady }: PriceCrossfadeProps) {
  const amount = parseUsdAmount(rawPrice)
  const isUsdLike = amount != null

  const usdLine = isUsdLike ? formatUsdPretty(amount) : rawPrice
  const cnyLine =
    isUsdLike && rate != null ? formatCnyPretty(amount, rate) : null

  const showCny = currency === 'CNY' && isUsdLike && rateReady && cnyLine != null
  const showUsd = currency === 'USD' || !isUsdLike || !rateReady || cnyLine == null

  return (
    <span className="relative inline-grid min-h-[1.35em] place-items-start">
      <span
        className={`col-start-1 row-start-1 font-semibold tabular-nums tracking-tight text-emerald-600 transition-[opacity,transform] duration-300 ease-out dark:text-emerald-400 ${
          showUsd ? 'opacity-100' : 'pointer-events-none opacity-0 [transform:translateY(2px)]'
        }`}
        aria-hidden={!showUsd}
      >
        {usdLine}
      </span>
      <span
        className={`col-start-1 row-start-1 font-semibold tabular-nums tracking-tight text-emerald-600 transition-[opacity,transform] duration-300 ease-out dark:text-emerald-400 ${
          showCny ? 'opacity-100' : 'pointer-events-none opacity-0 [transform:translateY(-2px)]'
        }`}
        aria-hidden={!showCny}
      >
        {cnyLine ?? usdLine}
      </span>
    </span>
  )
}

function App() {
  const [query, setQuery] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState<string | null>(null)
  const [listings, setListings] = useState<ReverbListing[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  const handleSearch = async () => {
    const q = query.trim()
    if (!q) return

    setSubmittedQuery(q)
    setLoading(true)
    setError(null)

    try {
      const res = await fetch(`/search?q=${encodeURIComponent(q)}`)
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `请求失败 (${res.status})`)
      }
      const data: ReverbSearchApiResponse = await res.json()
      setListings(data.results)
    } catch (e) {
      setListings([])
      setError(e instanceof Error ? e.message : '网络错误')
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      void handleSearch()
    }
  }

  const showResults = submittedQuery !== null

  return (
    <div className="min-h-svh bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
      <header className="sticky top-0 z-40 border-b border-zinc-200/80 bg-white/75 px-4 py-3 backdrop-blur-xl dark:border-zinc-800/80 dark:bg-zinc-950/75">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-zinc-600 dark:text-zinc-300">
              Guitar Search
            </p>
            {!rateLoading && rateReady && exchangeRate != null && (
              <p className="mt-0.5 text-[11px] text-zinc-400 transition-opacity duration-300 dark:text-zinc-500">
                1 USD ≈ {exchangeRate.toFixed(4)} CNY
              </p>
            )}
            {rateLoading && (
              <p className="mt-0.5 h-4 w-32 animate-pulse rounded bg-zinc-200/80 dark:bg-zinc-800/80" />
            )}
            {!rateLoading && rateError && (
              <p className="mt-0.5 text-[11px] text-amber-600 dark:text-amber-400">
                汇率暂不可用，仅显示原价
              </p>
            )}
          </div>
          <CurrencyToggle currency={currency} onChange={setCurrency} />
        </div>
      </header>

      <div
        className={
          showResults
            ? 'mx-auto flex max-w-6xl flex-col px-4 pb-16 pt-8'
            : 'mx-auto flex min-h-[calc(100svh-4.5rem)] max-w-6xl flex-col justify-center px-4 pb-16 pt-10'
        }
      >
        <div className={showResults ? 'w-full max-w-2xl self-center' : 'w-full max-w-2xl self-center'}>
          {!showResults && (
            <h1 className="mb-10 text-center text-4xl font-light tracking-tight text-zinc-800 dark:text-zinc-100 sm:text-5xl">
              Guitar Search
            </h1>
          )}
          <label className="sr-only" htmlFor="search">
            搜索吉他
          </label>
          <input
            id="search"
            type="search"
            autoComplete="off"
            placeholder="例如：Fender Mustang"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
            className="w-full rounded-full border border-zinc-200 bg-white px-6 py-4 text-lg shadow-sm outline-none ring-zinc-300 transition placeholder:text-zinc-400 focus:border-zinc-300 focus:ring-4 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:placeholder:text-zinc-500 dark:focus:border-zinc-600 dark:focus:ring-zinc-700"
          />
          {showResults && submittedQuery && (
            <p className="mt-3 text-center text-sm text-zinc-500 transition-colors duration-300 dark:text-zinc-400">
              Reverb 搜索结果 · 「{submittedQuery}」
              {loading && ' · 加载中…'}
            </p>
          )}
          {error && (
            <p className="mt-3 text-center text-sm text-red-600 dark:text-red-400" role="alert">
              {error}
            </p>
          )}
          {showResults && !error && (
            <p className="mt-2 text-center text-xs text-zinc-400 transition-opacity duration-300 dark:text-zinc-500">
              USD / CNY 仅对「金额 + USD」标价换算；其它币种显示原价。
            </p>
          )}
        </div>

        {showResults && !loading && !error && listings.length === 0 && (
          <p className="mt-10 text-center text-zinc-500 dark:text-zinc-400">
            没有结果，可换个关键词（例如 <strong>Fender</strong>、<strong>Mustang</strong>）。
          </p>
        )}

        {showResults && listings.length > 0 && (
          <section className="mt-12 w-full" aria-label="搜索结果">
            <ul className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {listings.map((item, index) => (
                <li key={item.url ? `${item.url}-${index}` : `row-${index}`}>
                  <article className="flex h-full flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm transition-[box-shadow,transform] duration-300 ease-out hover:-translate-y-0.5 hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900">
                    <div className="aspect-[4/3] overflow-hidden bg-zinc-100 dark:bg-zinc-800">
                      <img
                        src={item.imageUrl || PLACEHOLDER_IMG}
                        alt=""
                        className="h-full w-full object-cover transition-transform duration-500 ease-out hover:scale-[1.02]"
                        loading="lazy"
                        width={640}
                        height={480}
                      />
                    </div>
                    <div className="flex flex-1 flex-col space-y-3 p-4 text-left">
                      <h2 className="line-clamp-2 text-base font-medium leading-snug text-zinc-900 transition-colors duration-300 dark:text-zinc-100">
                        {item.title}
                      </h2>
                      <p>
                        <span className="inline-flex rounded-md bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                          Reverb
                        </span>
                      </p>
                      <p className="text-sm text-zinc-700 dark:text-zinc-200">
                        <span className="text-zinc-500 transition-colors duration-300 dark:text-zinc-400">
                          标价{' '}
                        </span>
                        <PriceCrossfade
                          rawPrice={item.price}
                          currency={currency}
                          rate={exchangeRate}
                          rateReady={rateReady}
                        />
                      </p>
                      {item.url ? (
                        <p className="mt-auto pt-1">
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex text-sm font-medium text-violet-600 underline-offset-2 transition-colors duration-200 hover:underline dark:text-violet-400"
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
      </div>
    </div>
  )
}

export default App
