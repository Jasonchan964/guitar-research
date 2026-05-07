import { useEffect, useId, useState } from 'react'
import { X } from 'lucide-react'
import { useAuth } from '../authContext'

export default function AuthModal() {
  const { authOpen, authTab, openAuth, closeAuth, login, register } = useAuth()
  const titleId = useId()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!authOpen) {
      setError(null)
      setSubmitting(false)
    }
  }, [authOpen])

  useEffect(() => {
    if (authOpen) {
      setError(null)
    }
  }, [authTab, authOpen])

  if (!authOpen) return null

  const onSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      if (authTab === 'login') await login(email, password)
      else await register(email, password)
      setPassword('')
    } catch (err) {
      setError(err instanceof Error ? err.message : '请求失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[90] flex items-end justify-center p-4 sm:items-center sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <button
        type="button"
        aria-label="关闭"
        className="absolute inset-0 bg-slate-900/25 backdrop-blur-[2px] dark:bg-slate-950/50"
        onClick={closeAuth}
      />
      <div
        className="relative w-full max-w-md overflow-hidden rounded-3xl border border-rose-100/80 bg-gradient-to-b from-white/95 to-rose-50/40 shadow-2xl shadow-rose-200/30 ring-1 ring-rose-100/60 dark:from-slate-900/95 dark:to-rose-950/30 dark:border-rose-900/40 dark:ring-rose-900/30"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-rose-100/60 bg-white/50 px-5 py-3.5 dark:border-rose-900/40 dark:bg-slate-900/50">
          <h2 id={titleId} className="text-base font-semibold text-slate-800 dark:text-slate-100">
            {authTab === 'login' ? '欢迎回来' : '创建账户'}
          </h2>
          <button
            type="button"
            onClick={closeAuth}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full text-slate-500 transition-colors hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/50"
            aria-label="关闭"
          >
            <X className="h-4 w-4" strokeWidth={2.5} />
          </button>
        </div>

        <div className="px-5 pt-4">
          <div
            className="mb-4 flex rounded-2xl border border-slate-200/80 bg-slate-100/60 p-1 dark:border-slate-700/80 dark:bg-slate-800/60"
            role="tablist"
          >
            <button
              type="button"
              role="tab"
              aria-selected={authTab === 'login'}
              onClick={() => openAuth('login')}
              className={`flex-1 rounded-xl py-2 text-sm font-semibold transition-colors ${
                authTab === 'login'
                  ? 'bg-white text-rose-600 shadow-sm ring-1 ring-rose-100/80 dark:bg-slate-900 dark:text-rose-300 dark:ring-rose-900/50'
                  : 'text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200'
              }`}
            >
              登录
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={authTab === 'register'}
              onClick={() => openAuth('register')}
              className={`flex-1 rounded-xl py-2 text-sm font-semibold transition-colors ${
                authTab === 'register'
                  ? 'bg-white text-rose-600 shadow-sm ring-1 ring-rose-100/80 dark:bg-slate-900 dark:text-rose-300 dark:ring-rose-900/50'
                  : 'text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200'
              }`}
            >
              注册
            </button>
          </div>

          <form className="space-y-3 pb-5" onSubmit={onSubmit}>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">
                邮箱
              </label>
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-2xl border border-slate-200/90 bg-white/90 px-4 py-2.5 text-sm text-slate-900 shadow-inner outline-none ring-0 transition-colors placeholder:text-slate-400 focus:border-rose-300 focus:ring-2 focus:ring-rose-200/80 dark:border-slate-600 dark:bg-slate-900/80 dark:text-slate-100 dark:focus:border-rose-700 dark:focus:ring-rose-900/40"
                placeholder="you@example.com"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">
                密码
              </label>
              <input
                type="password"
                autoComplete={authTab === 'login' ? 'current-password' : 'new-password'}
                required
                minLength={authTab === 'register' ? 8 : 1}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-2xl border border-slate-200/90 bg-white/90 px-4 py-2.5 text-sm text-slate-900 shadow-inner outline-none ring-0 transition-colors placeholder:text-slate-400 focus:border-rose-300 focus:ring-2 focus:ring-rose-200/80 dark:border-slate-600 dark:bg-slate-900/80 dark:text-slate-100 dark:focus:border-rose-700 dark:focus:ring-rose-900/40"
                placeholder={authTab === 'register' ? '至少 8 位' : '••••••••'}
              />
            </div>

            {error ? (
              <p className="rounded-xl bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-950/50 dark:text-rose-200">
                {error}
              </p>
            ) : null}

            <button
              type="submit"
              disabled={submitting}
              className="mt-1 w-full rounded-2xl bg-gradient-to-r from-rose-400 to-pink-400 px-4 py-3 text-sm font-semibold text-white shadow-md shadow-rose-300/40 transition-[filter,transform] hover:brightness-105 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-60 dark:from-rose-500 dark:to-pink-500 dark:shadow-rose-900/30"
            >
              {submitting ? '请稍候…' : authTab === 'login' ? '登录' : '注册并登录'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
