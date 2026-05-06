/** ``GET /api/search`` 返回的单条结果（Reverb + Digimart 合并） */
export type UnifiedListing = {
  title: string
  image: string | null
  price_usd: number | null
  price_cny: number | null
  source: string
  url: string
}

export type UnifiedSearchApiResponse = {
  query: string
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
  results: ReverbListing[]
}
