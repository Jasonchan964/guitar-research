import { useState } from 'react'
import type { ReverbListing, ReverbSearchApiResponse } from './mockGuitars'

const PLACEHOLDER_IMG =
  'data:image/svg+xml,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480"><rect fill="#e4e4e7" width="640" height="480"/><text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#71717a" font-family="system-ui" font-size="18">No image</text></svg>',
  )

function App() {
  const [query, setQuery] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState<string | null>(null)
  const [listings, setListings] = useState<ReverbListing[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
      <div
        className={
          showResults
            ? 'mx-auto flex max-w-6xl flex-col px-4 pt-10 pb-16'
            : 'mx-auto flex min-h-svh max-w-6xl flex-col items-center justify-center px-4 py-16'
        }
      >
        <header className={showResults ? 'w-full max-w-2xl self-center' : 'w-full max-w-2xl'}>
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
            <p className="mt-3 text-center text-sm text-zinc-500 dark:text-zinc-400">
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
            <p className="mt-2 text-center text-xs text-zinc-400 dark:text-zinc-500">
              数据来自 Reverb API；请保持后端 <code className="rounded bg-zinc-200 px-1 dark:bg-zinc-800">uvicorn</code>{' '}
              在 8000 端口，并重启一次以加载 .env / .env.txt。
            </p>
          )}
        </header>

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
                  <article className="flex h-full flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm transition hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900">
                    <div className="aspect-[4/3] overflow-hidden bg-zinc-100 dark:bg-zinc-800">
                      <img
                        src={item.imageUrl || PLACEHOLDER_IMG}
                        alt=""
                        className="h-full w-full object-cover"
                        loading="lazy"
                        width={640}
                        height={480}
                      />
                    </div>
                    <div className="flex flex-1 flex-col space-y-3 p-4 text-left">
                      <h2 className="line-clamp-2 text-base font-medium leading-snug text-zinc-900 dark:text-zinc-100">
                        {item.title}
                      </h2>
                      <p>
                        <span className="inline-flex rounded-md bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                          Reverb
                        </span>
                      </p>
                      <p className="text-sm text-zinc-700 dark:text-zinc-200">
                        标价{' '}
                        <span className="font-semibold text-emerald-600 dark:text-emerald-400">{item.price}</span>
                      </p>
                      {item.url ? (
                        <p className="mt-auto pt-1">
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex text-sm font-medium text-violet-600 underline-offset-2 hover:underline dark:text-violet-400"
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
