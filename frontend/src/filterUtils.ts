export const has = (q: string, ...fields: unknown[]) => !q || fields.some((f) => String(f ?? '').toLowerCase().includes(q.toLowerCase()))
export const uniq = (xs: (string | null | undefined)[]) => [...new Set(xs.filter(Boolean) as string[])].sort()
