// 步骤 0：上传 + 处理选项组件

import React from 'react'
import { MacOSCard } from '../../../components/MacOSLayout'

interface Step0UploadProps {
  optDecrypt: boolean
  setOptDecrypt: (v: boolean) => void
  optWatermark: boolean
  setOptWatermark: (v: boolean) => void
  optDeleteOriginal: boolean
  setOptDeleteOriginal: (v: boolean) => void
  password: string
  setPassword: (v: string) => void
}

export function Step0Upload({
  optDecrypt, setOptDecrypt,
  optWatermark, setOptWatermark,
  optDeleteOriginal, setOptDeleteOriginal,
  password, setPassword,
}: Step0UploadProps) {
  return (
    <MacOSCard style={{ marginBottom: 12, padding: 14 }}>
      <div className="text-sm font-medium mb-sm">处理选项</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <label className="flex-row gap-sm cursor-pointer">
          <input type="checkbox" checked={optDecrypt} onChange={(e) => setOptDecrypt(e.target.checked)} style={{ accentColor: 'var(--macos-accent)' }} />
          <span className="text-sm">PDF 有密码，需要解密</span>
        </label>
        {optDecrypt && (
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="请输入 PDF 密码"
            style={{ padding: '6px 10px', border: '1px solid var(--macos-border)', borderRadius: 6, fontSize: 13, width: 200, marginLeft: 20 }}
          />
        )}
        <label className="flex-row gap-sm cursor-pointer">
          <input type="checkbox" checked={optWatermark} onChange={(e) => setOptWatermark(e.target.checked)} style={{ accentColor: 'var(--macos-accent)' }} />
          <span className="text-sm">PDF 有水印，需要去除</span>
        </label>
        {(optDecrypt || optWatermark) && (
          <label className="flex-row gap-sm cursor-pointer" style={{ marginTop: 4, paddingTop: 8, borderTop: '1px solid var(--macos-border)' }}>
            <input type="checkbox" checked={optDeleteOriginal} onChange={(e) => setOptDeleteOriginal(e.target.checked)} style={{ accentColor: 'var(--macos-accent)' }} />
            <span className="text-sm">处理成功后删除原始文件（节省空间）</span>
          </label>
        )}
      </div>
    </MacOSCard>
  )
}