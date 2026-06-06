import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

/**
 * 已废弃 — 重定向到首页
 * 案卷分析功能已整合到案件详情页步骤 3
 */
export default function AnalyzePage() {
  const navigate = useNavigate()

  useEffect(() => {
    navigate('/', { replace: true })
  }, [navigate])

  return null
}
