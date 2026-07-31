// 案例库检索面板 — 报告页法律法规 tab 内的独立组件
// 检索刑事审判参考案例库，勾选案例后可注入阶段 4 重新生成

import { useCallback, useEffect, useRef, useState } from 'react'
import { Search, BookMarked, FileText, X } from 'lucide-react'
import { searchCases, getCharges, getCaseFull, CaseSummary } from '../../api/caseSearch'
import { colors } from './reportColors'

// 每页条数 — doSearch 与 totalPages 共用
const PAGE_SIZE = 20

interface Props {
  regenerating: boolean
  onRegenerate: (caseNos: string[]) => void
}

export default function CaseSearchPanel({ regenerating, onRegenerate }: Props) {
  const [q, setQ] = useState('')
  const [charge, setCharge] = useState('')
  const [chargeOptions, setChargeOptions] = useState<string[]>([])
  const [results, setResults] = useState<CaseSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [fullText, setFullText] = useState<{ title: string; text: string } | null>(null)

  // 检索竞态守卫：响应回来时若不是最新一次请求则丢弃
  const seqRef = useRef(0)

  useEffect(() => {
    getCharges().then(d => setChargeOptions(d.charges)).catch(() => {})
  }, [])

  // Escape 关闭全文弹窗
  useEffect(() => {
    if (!fullText) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFullText(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fullText])

  const doSearch = useCallback(async (pageNum: number) => {
    const seq = ++seqRef.current
    setLoading(true)
    setError('')
    try {
      const data = await searchCases(q, charge, pageNum, PAGE_SIZE)
      if (seq !== seqRef.current) return
      setResults(data.results)
      setTotal(data.total)
      setPage(pageNum)
    } catch (e: unknown) {
      if (seq !== seqRef.current) return
      setError(e instanceof Error ? e.message : '检索失败')
      setResults([])
      setTotal(0)
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [q, charge])

  const toggle = (caseNo: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(caseNo)) next.delete(caseNo)
      else next.add(caseNo)
      return next
    })
  }

  const viewFull = async (caseNo: string) => {
    try {
      const data = await getCaseFull(caseNo)
      setFullText({ title: `【${data.case_no}】${data.title}`, text: data.full_text })
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载全文失败')
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div style={{ borderTop: '1px solid', borderColor: colors.goldBorder, marginTop: '28px', paddingTop: '20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
        <BookMarked className="w-4 h-4" style={{ color: colors.gold }} />
        <span style={{ fontSize: '13px', fontWeight: 600, color: colors.textPrimary }}>案例库检索</span>
        <span style={{ fontSize: '11px', color: colors.textTertiary }}>刑事审判参考 · 1750 篇</span>
      </div>

      <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && doSearch(1)}
          placeholder="关键词 / 争议焦点，如：未成年人 强拿硬要"
          style={{
            flex: 1, padding: '7px 10px', fontSize: '12px', borderRadius: '6px',
            border: `1px solid ${colors.goldBorder}`, color: colors.textPrimary, background: 'transparent',
          }}
        />
        <select
          value={charge}
          onChange={e => setCharge(e.target.value)}
          style={{
            width: '160px', padding: '7px 8px', fontSize: '12px', borderRadius: '6px',
            border: `1px solid ${colors.goldBorder}`, color: colors.textPrimary, background: 'transparent',
          }}
        >
          <option value="">全部罪名</option>
          {chargeOptions.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <button
          onClick={() => doSearch(1)}
          disabled={loading}
          style={{
            display: 'flex', alignItems: 'center', gap: '4px', padding: '6px 14px', fontSize: '12px',
            borderRadius: '6px', background: colors.gold, color: '#fff', border: 'none',
            cursor: loading ? 'not-allowed' : 'pointer', fontWeight: 500,
          }}
        >
          <Search className="w-3 h-3" /> 检索
        </button>
      </div>

      {error && <div style={{ fontSize: '12px', color: '#c62828', marginBottom: '10px' }}>{error}</div>}

      {results.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {results.map(r => (
            <div key={r.case_no} style={{
              padding: '10px 12px', borderRadius: '8px', border: `1px solid ${colors.goldBorder}`,
              background: selected.has(r.case_no) ? colors.goldBg : 'transparent',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <input type="checkbox" checked={selected.has(r.case_no)} onChange={() => toggle(r.case_no)} aria-label={`选择案例 ${r.case_no} ${r.title}`} />
                <span style={{ fontSize: '12px', fontWeight: 600, color: colors.textPrimary, flex: 1 }}>
                  【{r.case_no}】{r.title}
                </span>
                <button
                  onClick={() => viewFull(r.case_no)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '3px', fontSize: '11px', padding: '3px 8px',
                    borderRadius: '5px', border: `1px solid ${colors.goldBorder}`, background: 'transparent',
                    color: colors.gold, cursor: 'pointer',
                  }}
                >
                  <FileText className="w-3 h-3" /> 全文
                </button>
              </div>
              <div style={{ fontSize: '11px', color: colors.textSecondary, marginTop: '6px', paddingLeft: '22px' }}>
                {r.issue}
              </div>
              <div style={{ fontSize: '11px', color: colors.textTertiary, marginTop: '4px', paddingLeft: '22px' }}>
                要旨：{r.holding_summary.length > 80 ? r.holding_summary.slice(0, 80) + '…' : r.holding_summary}
              </div>
            </div>
          ))}

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '4px' }}>
            <span style={{ fontSize: '11px', color: colors.textTertiary }}>
              共 {total} 篇 · 第 {page}/{totalPages} 页
            </span>
            <div style={{ display: 'flex', gap: '6px' }}>
              <button disabled={page <= 1 || loading} onClick={() => doSearch(page - 1)}
                style={{ fontSize: '11px', padding: '3px 10px', borderRadius: '5px', border: `1px solid ${colors.goldBorder}`, background: 'transparent', color: colors.gold, cursor: 'pointer' }}>上一页</button>
              <button disabled={page >= totalPages || loading} onClick={() => doSearch(page + 1)}
                style={{ fontSize: '11px', padding: '3px 10px', borderRadius: '5px', border: `1px solid ${colors.goldBorder}`, background: 'transparent', color: colors.gold, cursor: 'pointer' }}>下一页</button>
            </div>
          </div>
        </div>
      )}

      {selected.size > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginTop: '12px', padding: '10px 12px', borderRadius: '8px', background: colors.goldBg,
        }}>
          <span style={{ fontSize: '12px', color: colors.textPrimary }}>已选 {selected.size} 篇案例</span>
          <button
            onClick={() => onRegenerate(Array.from(selected))}
            disabled={regenerating}
            style={{
              padding: '6px 14px', fontSize: '12px', borderRadius: '6px', border: 'none',
              background: regenerating ? colors.goldBorder : colors.gold, color: '#fff',
              cursor: regenerating ? 'not-allowed' : 'pointer', fontWeight: 500,
            }}
          >
            {regenerating ? '正在重新生成…' : '引用选中案例并重新生成'}
          </button>
        </div>
      )}

      {fullText && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 1000,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }} onClick={() => setFullText(null)}>
          <div
            role="dialog" aria-modal="true" aria-label={fullText.title}
            style={{
              background: '#fff', borderRadius: '12px', padding: '24px', maxWidth: '720px',
              width: '90%', maxHeight: '80vh', overflowY: 'auto',
            }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <span style={{ fontSize: '14px', fontWeight: 600, color: colors.textPrimary }}>{fullText.title}</span>
              <button onClick={() => setFullText(null)} aria-label="关闭" style={{ background: 'none', border: 'none', cursor: 'pointer', color: colors.textTertiary }}>
                <X className="w-4 h-4" />
              </button>
            </div>
            <div style={{ fontSize: '13px', color: colors.textSecondary, whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>
              {fullText.text}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
