type CurrencyToggleProps = {
  currency: 'USD' | 'CNY'
  onChange: (c: 'USD' | 'CNY') => void
  compact?: boolean
}

export default function CurrencyToggle({ currency, onChange, compact }: CurrencyToggleProps) {
  const isCny = currency === 'CNY'

  return (
    <div
      className={`flex flex-col items-end gap-1 ${compact ? 'scale-95' : ''}`}
      role="group"
      aria-label="标价货币"
    >
      <span className="text-[10px] font-medium uppercase tracking-wider text-slate-400">
        标价
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={isCny}
        aria-label={isCny ? '当前为人民币，点击切换到美元' : '当前为美元，点击切换到人民币'}
        onClick={() => onChange(isCny ? 'USD' : 'CNY')}
        className="relative h-10 w-[136px] shrink-0 rounded-full border border-slate-200/90 bg-slate-100/90 p-[3px] shadow-sm transition-shadow duration-200 hover:shadow-md dark:border-slate-600 dark:bg-slate-800"
      >
        <span
          className={`pointer-events-none absolute top-[3px] h-[calc(100%-6px)] w-[calc(50%-6px)] rounded-full bg-white shadow-sm ring-1 ring-slate-200/80 transition-[left] duration-200 ease-out dark:bg-slate-950 dark:ring-slate-700 ${
            isCny ? 'left-[calc(50%+3px)]' : 'left-[3px]'
          }`}
        />
        <span className="relative z-10 flex h-full items-stretch">
          <span
            className={`flex flex-1 items-center justify-center gap-0.5 text-[11px] font-semibold tracking-tight ${
              !isCny ? 'text-slate-900 dark:text-slate-100' : 'text-slate-500'
            }`}
          >
            USD <span className="text-xs opacity-80">$</span>
          </span>
          <span
            className={`flex flex-1 items-center justify-center gap-0.5 text-[11px] font-semibold tracking-tight ${
              isCny ? 'text-slate-900 dark:text-slate-100' : 'text-slate-500'
            }`}
          >
            CNY <span className="text-xs opacity-80">¥</span>
          </span>
        </span>
      </button>
    </div>
  )
}
