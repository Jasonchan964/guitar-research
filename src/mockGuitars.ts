/** ``GET /api/search`` 返回的单条结果（Reverb + Digimart + GuitarGuitar + Ishibashi + Swee Lee 合并） */
export type UnifiedListing = {
  /** 后端由规范化 URL 派生的短 id，便于列表 key */
  id?: string
  title: string
  image: string | null
  price_usd: number | null
  price_cny: number | null
  source: string
  url: string
  /** 后端统一：`全新` 或 `二手` */
  condition: string
  /** 多图 URL 列表；无多图平台通常仅含主图 */
  all_images?: string[]
  /** 富文本 HTML（Shopify ``body_html``）；可能为空 */
  description?: string
  /** 后端在已登录请求下根据收藏表填充 */
  is_favorited?: boolean
}

export type UnifiedSearchApiResponse = {
  query: string
  page: number
  has_more: boolean
  /** 后端实际采用的排序：``relevance`` | ``price_desc`` | ``price_asc`` */
  sort?: string
  results: UnifiedListing[]
  /** 与 ``results`` 相同（后端兼容字段） */
  items?: UnifiedListing[]
  /** 当前页条数 */
  total?: number
  total_count?: number
  /** 后端搜索异常时的简要说明 */
  error?: string
}

/** 旧版 ``GET /search``（仅 Reverb），保留便于对照 */
export type ReverbListing = {
  title: string
  imageUrl: string | null
  price: string
  url: string
}

export type ReverbSearchApiResponse = {
  query: string
  sort?: string
  results: ReverbListing[]
}

/** ``GET /api/guitar/detail`` 统一详情结构 */
export type GuitarDetailApi = {
  title: string
  price_cny: number | null
  price_original: string
  platform: string
  condition: string
  images: string[]
  specs: Record<string, string>
  description_html: string
  buy_url: string
}

/** 列表图缺省 / onError 回退：与 ``public/placeholder.svg`` 一致，便于静态路径引用 */
export const LISTING_PLACEHOLDER_IMAGE_PATH = '/placeholder.svg'

export function coerceListingPrice(value: unknown): number | null {
  if (value == null) return null
  if (typeof value === 'number') {
    return Number.isFinite(value) && value >= 0 ? value : null
  }
  if (typeof value === 'string') {
    const t = value.replace(/,/g, '').trim()
    if (!t) return null
    const n = Number.parseFloat(t)
    return Number.isFinite(n) && n >= 0 ? n : null
  }
  return null
}

/**
 * 将单条 API / 缓存中的未知结构规整为 ``UnifiedListing``，避免 map / 价格格式化崩溃。
 */
export function sanitizeUnifiedListing(row: unknown): UnifiedListing {
  const fallback: UnifiedListing = {
    title: 'Untitled',
    image: null,
    price_usd: null,
    price_cny: null,
    source: 'Unknown',
    url: '',
    condition: '二手',
  }
  if (!row || typeof row !== 'object' || Array.isArray(row)) {
    return fallback
  }
  const r = row as Record<string, unknown>
  const title =
    typeof r.title === 'string' && r.title.trim() ? r.title.trim() : fallback.title
  let image: string | null = fallback.image
  if (typeof r.image === 'string' && r.image.trim()) image = r.image.trim()
  else if (r.image === null) image = null
  const source =
    typeof r.source === 'string' && r.source.trim() ? r.source.trim() : fallback.source
  const url = typeof r.url === 'string' ? r.url : fallback.url
  const condition = r.condition === '全新' ? '全新' : '二手'
  const id = typeof r.id === 'string' && r.id.trim() ? r.id.trim() : undefined
  const price_usd = coerceListingPrice(r.price_usd)
  const price_cny = coerceListingPrice(r.price_cny)
  const all_images = Array.isArray(r.all_images)
    ? r.all_images.filter((x): x is string => typeof x === 'string' && x.trim() !== '')
    : undefined
  const description = typeof r.description === 'string' ? r.description : undefined
  const is_favorited = typeof r.is_favorited === 'boolean' ? r.is_favorited : undefined
  return {
    id,
    title,
    image,
    price_usd,
    price_cny,
    source,
    url,
    condition,
    ...(all_images?.length ? { all_images } : {}),
    ...(description !== undefined ? { description } : {}),
    ...(is_favorited !== undefined ? { is_favorited } : {}),
  }
}

export function sanitizeUnifiedSearchResponse(
  data: unknown,
  fallbackQuery: string,
): UnifiedSearchApiResponse {
  const d =
    data && typeof data === 'object' && !Array.isArray(data)
      ? (data as Record<string, unknown>)
      : {}
  const resultsRaw = d.results
  const results = Array.isArray(resultsRaw)
    ? resultsRaw.map((row) => sanitizeUnifiedListing(row))
    : []
  const q =
    typeof d.query === 'string' && d.query.trim() ? d.query.trim() : fallbackQuery.trim()
  const page = typeof d.page === 'number' && Number.isFinite(d.page) && d.page >= 1 ? d.page : 1
  return {
    query: q,
    page,
    has_more: Boolean(d.has_more),
    sort: typeof d.sort === 'string' ? d.sort : undefined,
    results,
  }
}
