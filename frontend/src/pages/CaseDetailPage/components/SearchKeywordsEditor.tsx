// 类案检索关键词编辑区（spec 9.1）
// 预填：search_keywords ?? suggested_keywords ?? charges；保存写 case.json 的 search_keywords

import { useCallback, useEffect, useRef, useState } from 'react'
import { Search, Loader2 } from 'lucide-react'
import { getCaseInfo, updateCaseSearchKeywords } from '../../../api'
import { colors } from '../../../components/report/reportColors'

interface SearchKeywordsEditorProps {
  caseId: string
  charges: string[]
}

export function SearchKeywordsEditor({ caseId, charges }: SearchKeywordsEditorProps) {
  const [keywords, setKeywords] = useState<string[]>([])
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // 挂载时读案件详情：search_keywords ?? suggested_keywords ?? charges
  useEffect(() => {
    let cancelled = false
    getCaseInfo(caseId)
      .then(d => {
        if (cancelled) return
        const existing: string[] = d?.search_keywords?.length
          ? d.search_keywords
          : d?.suggested_keywords?.length
            ? d.suggested_keywords
            : charges
        setKeywords(existing || [])
      })
      .catch(() => { if (!cancelled) setKeywords(charges) })
      .finally(() => { if (!cancelled) setLoaded(true) })
    return () => { cancelled = true }
    // charges 作为兜底预填，仅在挂载时取一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId])

  const addKeyword = useCallback(() => {
    const v = inputRef.current?.value.trim() || ''
    if (!v) return
    setKeywords(prev => (prev.includes(v) ? prev : [...prev, v]))
    setSaved(false)
    if (inputRef.current) inputRef.current.value = ''
  }, [])

  const removeKeyword = useCallback((idx: number) => {
    setKeywords(prev => prev.filter((_, j) => j !== idx))
    setSaved(false)
  }, [])

  const handleSave = useCallback(async () => {
    if (saving) return
    setSaving(true)
    setError('')
    try {
      await updateCaseSearchKeywords(caseId, keywords)
      setSaved(true)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }, [caseId, keywords, saving])

  if (!loaded) return null

  return (
    <div style={{
      marginBottom: 12, padding: '12px 14px', borderRadius: 10,
      border: `1px solid ${colors.border}`, background: colors.surface,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <Search className="w-4 h-4" style={{ color: colors.accent }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: colors.textPrimary }}>类案检索关键词</span>
        <span style={{ fontSize: 11, color: colors.textTertiary }}>用于法律法规阶段的相似案例检索</span>
      </div>
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 6, padding: '6px 10px',
        border: `1px solid ${colors.border}`, borderRadius: 8, minHeight: 36,
        alignItems: 'center', background: colors.surfaceAlt,
      }}>
        {keywords.map((k, i) => (
          <span key={`${k}-${i}`} style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            padding: '2px 8px', background: colors.accent, color: '#fff',
            borderRadius: 10, fontSize: 12, fontWeight: 500,
          }}>
            {k}
            <button
              onClick={() => removeKeyword(i)}
              aria-label={`删除关键词 ${k}`}
              style={{ background: 'none', border: 'none', color: '#fff', cursor: 'pointer', padding: '0 2px', fontSize: 14, lineHeight: 1 }}
            >×</button>
          </span>
        ))}
        <input
          ref={inputRef}
          type="text"
          placeholder={keywords.length === 0 ? '如：自首 退赃' : '继续添加...'}
          aria-label="新增检索关键词"
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addKeyword() } }}
          style={{ border: 'none', outline: 'none', fontSize: 13, flex: 1, minWidth: 80, background: 'transparent' }}
        />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
        <button
          onClick={handleSave}
          disabled={saving}
          style={{
            padding: '5px 14px', fontSize: 12, fontWeight: 500, borderRadius: 6, border: 'none',
            background: saving ? colors.accentBorder : colors.accent, color: '#fff',
            cursor: saving ? 'not-allowed' : 'pointer',
            display: 'flex', alignItems: 'center', gap: 4,
          }}
        >
          {saving && <Loader2 className="w-3 h-3 animate-spin" />}
          保存关键词
        </button>
        {saved && !saving && <span style={{ fontSize: 11, color: '#3b5998' }}>已保存</span>}
        {error && <span style={{ fontSize: 11, color: '#c62828' }}>{error}</span>}
      </div>
    </div>
  )
}
