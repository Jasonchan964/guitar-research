/** ``GET /api/search`` 返回的单条结果（Reverb + Digimart + GuitarGuitar + Ishibashi + Swee Lee 合并） */
export type UnifiedListing = {
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
