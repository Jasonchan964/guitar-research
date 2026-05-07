import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { normalizeOriginalUrl } from './urlNormalize'

const TOKEN_KEY = 'token'

export type FavoriteRow = {
  id: number
  title: string
  price_cny: number
  image_url: string
  original_url: string
  platform: string
  created_at: string
}

type JwtPayload = {
  sub?: string
  email?: string
  exp?: number
}

function parseJwtPayload(token: string): JwtPayload | null {
  try {
    const parts = token.split('.')
    if (parts.length !== 3) return null
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const pad = base64.length % 4 === 0 ? '' : '='.repeat(4 - (base64.length % 4))
    const json = atob(base64 + pad)
    return JSON.parse(json) as JwtPayload
  } catch {
    return null
  }
}

function isTokenExpired(token: string): boolean {
  const p = parseJwtPayload(token)
  if (!p?.exp) return false
  return p.exp * 1000 <= Date.now()
}

async function parseHttpError(res: Response): Promise<string> {
  let msg = await res.text()
  try {
    const j = JSON.parse(msg) as { detail?: unknown }
    if (typeof j.detail === 'string') msg = j.detail
    else if (
      Array.isArray(j.detail) &&
      typeof (j.detail as { msg?: string }[])[0]?.msg === 'string'
    )
      msg = (j.detail as { msg: string }[]).map((x) => x.msg).join('；')
  } catch {
    /* keep raw */
  }
  return msg || `请求失败 (${res.status})`
}

type AuthUser = { email: string }

type AuthContextValue = {
  user: AuthUser | null
  token: string | null
  favorites: FavoriteRow[]
  favoritesLoading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => void
  refreshFavorites: () => Promise<void>
  isFavorite: (originalUrl: string) => boolean
  addFavorite: (body: {
    title: string
    price_cny: number
    image_url: string
    original_url: string
    platform: string
  }) => Promise<FavoriteRow>
  removeFavorite: (opts: { originalUrl: string } | { favoriteId: number }) => Promise<void>
  authOpen: boolean
  authTab: 'login' | 'register'
  openAuth: (tab?: 'login' | 'register') => void
  closeAuth: () => void
  toast: string | null
  showToast: (message: string) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

function readTokenFromStorage(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

function userFromToken(token: string | null): AuthUser | null {
  if (!token) return null
  if (isTokenExpired(token)) return null
  const p = parseJwtPayload(token)
  const email = typeof p?.email === 'string' ? p.email : null
  if (!email) return null
  return { email }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => readTokenFromStorage())
  const [user, setUser] = useState<AuthUser | null>(() => userFromToken(readTokenFromStorage()))
  const [favorites, setFavorites] = useState<FavoriteRow[]>([])
  const [favoritesLoading, setFavoritesLoading] = useState(false)
  const [authOpen, setAuthOpen] = useState(false)
  const [authTab, setAuthTab] = useState<'login' | 'register'>('login')
  const [toast, setToast] = useState<string | null>(null)
  const toastTimer = useRef<number | null>(null)

  const showToast = useCallback((message: string) => {
    setToast(message)
    if (toastTimer.current) window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => {
      setToast(null)
      toastTimer.current = null
    }, 3200)
  }, [])

  const persistToken = useCallback((t: string | null) => {
    setToken(t)
    try {
      if (t) localStorage.setItem(TOKEN_KEY, t)
      else localStorage.removeItem(TOKEN_KEY)
    } catch {
      /* ignore */
    }
    setUser(userFromToken(t))
  }, [])

  const authHeaders = useCallback((): HeadersInit => {
    const h: Record<string, string> = {}
    const t = readTokenFromStorage()
    if (t) h.Authorization = `Bearer ${t}`
    return h
  }, [])

  const refreshFavorites = useCallback(async () => {
    const t = readTokenFromStorage()
    if (!t || isTokenExpired(t)) {
      setFavorites([])
      return
    }
    setFavoritesLoading(true)
    try {
      const res = await fetch('/api/favorites', {
        headers: { Authorization: `Bearer ${t}` },
      })
      if (res.status === 401) {
        persistToken(null)
        setFavorites([])
        showToast('登录已过期，请重新登录')
        return
      }
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `加载收藏失败 (${res.status})`)
      }
      const data = (await res.json()) as FavoriteRow[]
      setFavorites(Array.isArray(data) ? data : [])
    } catch (e) {
      showToast(e instanceof Error ? e.message : '加载收藏失败')
      setFavorites([])
    } finally {
      setFavoritesLoading(false)
    }
  }, [persistToken, showToast])

  useEffect(() => {
    if (!token || isTokenExpired(token)) {
      if (token && isTokenExpired(token)) {
        persistToken(null)
        showToast('登录已过期，请重新登录')
      }
      setFavorites([])
      return
    }
    setUser(userFromToken(token))
    void refreshFavorites()
  }, [token, persistToken, refreshFavorites, showToast])

  const finalizeSession = useCallback(
    async (accessToken: string, message: string) => {
      persistToken(accessToken)
      setAuthOpen(false)
      showToast(message)
      await refreshFavorites()
    },
    [persistToken, refreshFavorites, showToast],
  )

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), password }),
      })
      if (!res.ok) throw new Error(await parseHttpError(res))
      const body = (await res.json()) as { access_token?: string }
      const t = body.access_token
      if (!t) throw new Error('服务器未返回令牌')
      await finalizeSession(t, '登录成功')
    },
    [finalizeSession],
  )

  const register = useCallback(
    async (email: string, password: string) => {
      const resReg = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), password }),
      })
      if (!resReg.ok) throw new Error(await parseHttpError(resReg))

      const resLogin = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), password }),
      })
      if (!resLogin.ok) throw new Error(await parseHttpError(resLogin))
      const body = (await resLogin.json()) as { access_token?: string }
      const t = body.access_token
      if (!t) throw new Error('服务器未返回令牌')
      await finalizeSession(t, '注册成功')
    },
    [finalizeSession],
  )

  const logout = useCallback(() => {
    persistToken(null)
    setFavorites([])
    showToast('已退出登录')
  }, [persistToken, showToast])

  const normSet = useMemo(() => {
    const s = new Set<string>()
    for (const f of favorites) {
      const k = normalizeOriginalUrl(f.original_url)
      if (k) s.add(k)
    }
    return s
  }, [favorites])

  const isFavorite = useCallback(
    (originalUrl: string) => {
      const k = normalizeOriginalUrl(originalUrl)
      return k ? normSet.has(k) : false
    },
    [normSet],
  )

  const addFavorite = useCallback(
    async (body: {
      title: string
      price_cny: number
      image_url: string
      original_url: string
      platform: string
    }) => {
      const res = await fetch('/api/favorites/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(body),
      })
      if (res.status === 401) {
        persistToken(null)
        throw new Error('请先登录')
      }
      if (res.status === 409) {
        await refreshFavorites()
        const existing = favorites.find(
          (f) => normalizeOriginalUrl(f.original_url) === normalizeOriginalUrl(body.original_url),
        )
        if (existing) return existing
        throw new Error('已在收藏夹中')
      }
      if (!res.ok) {
        let msg = await res.text()
        try {
          const j = JSON.parse(msg) as { detail?: unknown }
          if (typeof j.detail === 'string') msg = j.detail
        } catch {
          /* keep */
        }
        throw new Error(msg || '添加收藏失败')
      }
      const row = (await res.json()) as FavoriteRow
      setFavorites((prev) => {
        const next = prev.filter((p) => p.id !== row.id)
        return [row, ...next]
      })
      return row
    },
    [authHeaders, favorites, persistToken, refreshFavorites],
  )

  const removeFavorite = useCallback(
    async (opts: { originalUrl: string } | { favoriteId: number }) => {
      const params = new URLSearchParams()
      if ('favoriteId' in opts) params.set('favorite_id', String(opts.favoriteId))
      else params.set('original_url', opts.originalUrl)
      const res = await fetch(`/api/favorites/remove?${params.toString()}`, {
        method: 'DELETE',
        headers: authHeaders(),
      })
      if (res.status === 401) {
        persistToken(null)
        throw new Error('请先登录')
      }
      if (res.status === 404) {
        await refreshFavorites()
        return
      }
      if (!res.ok) {
        let msg = await res.text()
        try {
          const j = JSON.parse(msg) as { detail?: unknown }
          if (typeof j.detail === 'string') msg = j.detail
        } catch {
          /* keep */
        }
        throw new Error(msg || '取消收藏失败')
      }
      if ('favoriteId' in opts) {
        setFavorites((prev) => prev.filter((p) => p.id !== opts.favoriteId))
      } else {
        const k = normalizeOriginalUrl(opts.originalUrl)
        setFavorites((prev) =>
          prev.filter((p) => normalizeOriginalUrl(p.original_url) !== k),
        )
      }
    },
    [authHeaders, persistToken, refreshFavorites],
  )

  const openAuth = useCallback((tab: 'login' | 'register' = 'login') => {
    setAuthTab(tab)
    setAuthOpen(true)
  }, [])

  const closeAuth = useCallback(() => setAuthOpen(false), [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      favorites,
      favoritesLoading,
      login,
      register,
      logout,
      refreshFavorites,
      isFavorite,
      addFavorite,
      removeFavorite,
      authOpen,
      authTab,
      openAuth,
      closeAuth,
      toast,
      showToast,
    }),
    [
      user,
      token,
      favorites,
      favoritesLoading,
      login,
      register,
      logout,
      refreshFavorites,
      isFavorite,
      addFavorite,
      removeFavorite,
      authOpen,
      authTab,
      openAuth,
      closeAuth,
      toast,
      showToast,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
