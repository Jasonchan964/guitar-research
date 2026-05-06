/** Reverb `/search` 返回的单条结果 */
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
