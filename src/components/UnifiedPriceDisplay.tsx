export function formatUsdPretty(amount: number): string {
  return `$${amount.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

export function formatCnyFromServer(amount: number): string {
  return `¥${amount.toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })}`
}

type UnifiedPriceProps = {
  priceUsd: number | null
  priceCny: number | null
  currency: 'USD' | 'CNY'
}

/** 使用后端已换算的 ``price_usd`` / ``price_cny`` 切换展示 */
export default function UnifiedPriceDisplay({ priceUsd, priceCny, currency }: UnifiedPriceProps) {
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
