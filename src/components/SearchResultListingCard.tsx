import { Link } from 'react-router-dom'
import type { UnifiedListing } from '../mockGuitars'
import FavoriteHeart from './FavoriteHeart'
import UnifiedPriceDisplay from './UnifiedPriceDisplay'

const PLACEHOLDER_IMG =
  'data:image/svg+xml,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480"><rect fill="#f1f5f9" width="640" height="480"/><text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#94a3b8" font-family="system-ui" font-size="18">No image</text></svg>',
  )

/** 列表卡片平台标签：与筛选栏选中态一致的马卡龙浅色 + 淡色描边 */
const SOURCE_PILL_CLASS: Record<string, string> = {
  Reverb: 'border-[#E07A5F]/25 bg-[#FFF0EB] text-[#E07A5F] dark:border-[#E07A5F]/30 dark:bg-[#FFF0EB]/95',
  Digimart: 'border-[#D15C7D]/25 bg-[#FFF0F5] text-[#D15C7D] dark:border-[#D15C7D]/30 dark:bg-[#FFF0F5]/95',
  GuitarGuitar: 'border-[#2D9B75]/25 bg-[#E6F7F0] text-[#2D9B75] dark:border-[#2D9B75]/30 dark:bg-[#E6F7F0]/95',
  Ishibashi: 'border-[#2B7BC6]/25 bg-[#E6F2FF] text-[#2B7BC6] dark:border-[#2B7BC6]/30 dark:bg-[#E6F2FF]/95',
  'Swee Lee': 'border-[#B8860B]/25 bg-[#FFF9E6] text-[#B8860B] dark:border-[#B8860B]/30 dark:bg-[#FFF9E6]/95',
}
const SOURCE_PILL_FALLBACK =
  'border-[#CCCCCC]/40 bg-[#FAFAFA] text-[#CCCCCC] dark:border-slate-600 dark:bg-slate-800 dark:text-slate-400'

export type SearchResultListingCardProps = {
  item: UnifiedListing
  currency: 'USD' | 'CNY'
}

function detailPath(item: UnifiedListing): string {
  return `/guitar?${new URLSearchParams({ url: item.url, platform: item.source }).toString()}`
}

export default function SearchResultListingCard({ item, currency }: SearchResultListingCardProps) {
  return (
    <li className="min-w-0">
      <article className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-slate-200/90 bg-white shadow-sm transition-shadow duration-200 hover:shadow-md md:rounded-2xl dark:border-slate-700/90 dark:bg-slate-900">
        {item.url ? (
          <>
            <div className="relative aspect-[4/3] w-full shrink-0 overflow-hidden bg-slate-100 dark:bg-slate-800">
              <Link
                to={detailPath(item)}
                className="block h-full w-full outline-none transition-[box-shadow] focus-visible:ring-2 focus-visible:ring-blue-500/80 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-slate-900"
              >
                <img
                  src={item.image || PLACEHOLDER_IMG}
                  alt=""
                  className="h-full w-full object-cover object-center"
                  loading="lazy"
                  width={640}
                  height={480}
                />
              </Link>
              <FavoriteHeart
                className="right-2 top-2"
                item={{
                  title: item.title,
                  price_cny: item.price_cny,
                  image: item.image,
                  original_url: item.url,
                  platform: item.source,
                }}
              />
            </div>
            <Link
              to={detailPath(item)}
              className="flex min-h-0 min-w-0 flex-1 flex-col gap-1.5 p-3 text-left outline-none transition-[box-shadow] focus-visible:ring-2 focus-visible:ring-blue-500/80 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-slate-900 sm:gap-2 sm:p-4 md:gap-2.5 md:p-5"
            >
              <h2 className="line-clamp-2 min-h-0 text-sm font-medium leading-snug text-slate-900 sm:text-[15px] dark:text-slate-50">
                {item.title}
              </h2>
              <p className="flex shrink-0 flex-wrap items-center gap-1.5">
                <span
                  className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-medium sm:px-2.5 sm:text-xs ${SOURCE_PILL_CLASS[item.source] ?? SOURCE_PILL_FALLBACK}`}
                >
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
              <p className="mt-auto shrink-0 pt-1.5 text-[11px] font-medium text-slate-400 sm:pt-2 sm:text-xs dark:text-slate-500">
                站内详情页
              </p>
            </Link>
          </>
        ) : (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col text-left opacity-60">
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
            <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-1.5 p-3 sm:gap-2 sm:p-4 md:gap-2.5 md:p-5">
              <h2 className="line-clamp-2 min-h-0 text-sm font-medium leading-snug text-slate-900 sm:text-[15px] dark:text-slate-50">
                {item.title}
              </h2>
              <p className="text-[11px] text-slate-400">无原站链接</p>
            </div>
          </div>
        )}
        {item.url ? (
          <div className="border-t border-slate-100 px-3 py-2 dark:border-slate-800/80 sm:px-4">
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-medium text-blue-700 underline-offset-2 hover:underline sm:text-sm dark:text-blue-400"
            >
              新窗口打开官网
            </a>
          </div>
        ) : null}
      </article>
    </li>
  )
}
