import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

/**
 * 已废弃 — 重定向到首页
 * PDF 转 MD 功能已整合到案件详情页
 */
export default function ConvertPage() {
  const navigate = useNavigate()

  useEffect(() => {
    navigate('/', { replace: true })
  }, [navigate])

  return null
}
