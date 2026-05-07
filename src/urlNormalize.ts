/** 与后端 ``normalize_original_url`` 对齐，用于收藏去重与前端比对 */

export function normalizeOriginalUrl(url: string): string {
  let raw = (url || '').trim()
  if (!raw) return ''
  if (raw.startsWith('//')) raw = `https:${raw}`
  try {
    const u = new URL(raw)
    const scheme = (u.protocol.replace(':', '') || 'https').toLowerCase()
    const host = u.host.toLowerCase()
    let path = u.pathname.replace(/\/+$/, '')
    if (!path) path = '/'
    return `${scheme}://${host}${path}${u.search}`
  } catch {
    return ''
  }
}
