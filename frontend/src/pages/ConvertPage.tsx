import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileText, Wand2, ArrowRight, FolderOpen, Settings, CheckCircle, AlertCircle, ExternalLink, Key } from 'lucide-react'
import { MacOSTitlebar, MacOSSidebar, MacOSToolbar, MacOSButton, MacOSCard, MacOSEmptyState } from '../components/MacOSLayout'

export function ConvertPage() {
  const navigate = useNavigate()
  const [files, setFiles] = useState<File[]>([])
  const [converting, setConverting] = useState(false)
  const [progress, setProgress] = useState('')
  const [completed, setCompleted] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  
  const [options, setOptions] = useState({
    method: 'auto' as 'auto' | 'api' | 'cli',
    mineruToken: ''
  })

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files
    if (selected) {
      setFiles(Array.from(selected))
    }
  }

  const handleConvert = async () => {
    if (files.length === 0) return
    setConverting(true)
    setCompleted(false)
    
    try {
      for (let i = 0; i < files.length; i++) {
        const file = files[i]
        setProgress(`[${i + 1}/${files.length}] 正在转换：${file.name}`)
        
        // TODO: 调用 MinerU API 或本地 CLI
        await new Promise(resolve => setTimeout(resolve, 1000))
      }
      
      setProgress('✅ 全部转换完成！')
      setCompleted(true)
    } catch (err) {
      setProgress('❌ 转换失败')
    } finally {
      setConverting(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#ffffff', overflow: 'hidden' }}>
      <MacOSTitlebar showBack onBack={() => navigate('/')} />
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <MacOSSidebar />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <MacOSToolbar title="② PDF转MD">
            <MacOSButton variant="primary" icon={FolderOpen} onClick={() => document.getElementById('pdf-upload')?.click()}>
              选择 PDF
            </MacOSButton>
          </MacOSToolbar>

          <div style={{ flex: 1, overflow: 'auto', padding: '30px' }}>
            {files.length === 0 && !completed ? (
              <MacOSEmptyState
                icon={FileText}
                title="选择拆分后的 PDF 文件"
                description="使用 MinerU 将 PDF 高质量转换为 Markdown 格式"
                action={
                  <MacOSButton variant="primary" icon={FolderOpen} onClick={() => document.getElementById('pdf-upload')?.click()}>
                    选择文件
                  </MacOSButton>
                }
              />
            ) : completed ? (
              <MacOSCard>
                <div style={{ textAlign: 'center', padding: '40px' }}>
                  <CheckCircle className="w-16 h-16" style={{ color: '#34c759', margin: '0 auto 20px' }} />
                  <h2 style={{ fontSize: '24px', marginBottom: '10px' }}>转换完成</h2>
                  <p style={{ color: '#6e6e73', marginBottom: '30px' }}>
                    共转换 {files.length} 个文件，已保存为 Markdown 格式
                  </p>
                  <MacOSButton variant="primary" icon={ArrowRight} onClick={() => navigate('/analyze')}>
                    继续案卷分析 →
                  </MacOSButton>
                </div>
              </MacOSCard>
            ) : (
              <div>
                {/* 文件列表 */}
                <MacOSCard>
                  <h3 style={{ fontSize: '16px', marginBottom: '16px' }}>待转换 {files.length} 个文件</h3>
                  {files.map((file, index) => (
                    <div key={index} style={{ 
                      display: 'flex', 
                      alignItems: 'center', 
                      justifyContent: 'space-between',
                      padding: '10px 0',
                      borderBottom: index < files.length - 1 ? '1px solid var(--macos-border)' : 'none'
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <FileText className="w-5 h-5" style={{ color: '#6e6e73' }} />
                        <div>
                          <div style={{ fontSize: '14px' }}>{file.name}</div>
                          <div style={{ fontSize: '12px', color: '#86868b' }}>{(file.size / 1024).toFixed(1)} KB</div>
                        </div>
                      </div>
                      <ArrowRight className="w-4 h-4" style={{ color: '#86868b' }} />
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <span style={{ fontSize: '12px', color: '#007aff', background: 'rgba(0,122,255,0.1)', padding: '2px 8px', borderRadius: '10px' }}>
                          .md
                        </span>
                      </div>
                    </div>
                  ))}
                </MacOSCard>

                {/* 转换选项 */}
                <MacOSCard style={{ marginTop: '20px' }}>
                  <h3 style={{ fontSize: '16px', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Settings className="w-4 h-4" />
                    转换选项
                  </h3>
                  
                  {/* MinerU 模式选择 */}
                  <div style={{ marginBottom: '16px' }}>
                    <div style={{ fontSize: '13px', fontWeight: '500', marginBottom: '8px' }}>转换方式</div>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      {[
                        { value: 'auto', label: '自动', desc: '优先 CLI，降级 API' },
                        { value: 'api', label: '云端 API', desc: '高质量，需 Token' },
                        { value: 'cli', label: '本地 CLI', desc: '离线，需安装' },
                      ].map(mode => (
                        <button
                          key={mode.value}
                          onClick={() => setOptions({...options, method: mode.value as any})}
                          style={{
                            flex: 1,
                            padding: '10px',
                            background: options.method === mode.value ? 'rgba(0,122,255,0.1)' : 'var(--macos-bg-secondary)',
                            border: options.method === mode.value ? '2px solid #007aff' : '1px solid var(--macos-border)',
                            borderRadius: '8px',
                            cursor: 'pointer',
                            textAlign: 'left'
                          }}
                        >
                          <div style={{ fontSize: '13px', fontWeight: options.method === mode.value ? '600' : '400', color: options.method === mode.value ? '#007aff' : 'var(--macos-text-primary)' }}>
                            {mode.label}
                          </div>
                          <div style={{ fontSize: '11px', color: 'var(--macos-text-secondary)', marginTop: '2px' }}>
                            {mode.desc}
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* API Token 配置 */}
                  {(options.method === 'api' || options.method === 'auto') && (
                    <div style={{ marginBottom: '16px', padding: '16px', background: 'var(--macos-bg-secondary)', borderRadius: '10px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', fontWeight: '500' }}>
                          <Key className="w-4 h-4" style={{ color: '#007aff' }} />
                          MinerU API Token
                        </div>
                        <a
                          href="https://mineru.net"
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{ fontSize: '12px', color: '#007aff', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '4px', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                        >
                          申请 Token <ExternalLink className="w-3 h-3" />
                        </a>
                      </div>
                      
                      {/* 提示信息 */}
                      <div style={{ display: 'flex', gap: '8px', padding: '10px', background: 'rgba(0,122,255,0.05)', borderRadius: '8px', marginBottom: '12px' }}>
                        <AlertCircle className="w-4 h-4" style={{ color: '#007aff', flexShrink: 0, marginTop: '2px' }} />
                        <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)', lineHeight: '1.5' }}>
                          <div>• MinerU API 需要自行申请 Token</div>
                          <div>• 访问 <a href="https://mineru.net" target="_blank" rel="noopener noreferrer" style={{ color: '#007aff' }}>mineru.net</a> 注册账号</div>
                          <div>• 免费额度：每日有一定量的免费转换次数</div>
                        </div>
                      </div>

                      <div style={{ position: 'relative' }}>
                        <input
                          type={showApiKey ? 'text' : 'password'}
                          placeholder="输入 MinerU API Token"
                          value={options.mineruToken}
                          onChange={(e) => setOptions({...options, mineruToken: e.target.value})}
                          style={{
                            width: '100%',
                            padding: '10px 40px 10px 12px',
                            border: '1px solid var(--macos-border)',
                            borderRadius: '8px',
                            fontSize: '13px',
                            fontFamily: 'monospace',
                            background: 'white'
                          }}
                        />
                        <button
                          onClick={() => setShowApiKey(!showApiKey)}
                          style={{
                            position: 'absolute',
                            right: '8px',
                            top: '50%',
                            transform: 'translateY(-50%)',
                            background: 'none',
                            border: 'none',
                            cursor: 'pointer',
                            color: 'var(--macos-text-secondary)',
                            padding: '4px'
                          }}
                        >
                          {showApiKey ? '🙈' : '👁️'}
                        </button>
                      </div>
                    </div>
                  )}

                  {/* 转换说明 */}
                  <div style={{ marginBottom: '16px', padding: '16px', background: 'var(--macos-bg-secondary)', borderRadius: '10px' }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '8px' }}>
                      <AlertCircle className="w-4 h-4" style={{ color: '#007aff', flexShrink: 0, marginTop: '2px' }} />
                      <div style={{ fontSize: '12px', color: 'var(--macos-text-secondary)', lineHeight: '1.5' }}>
                        <div style={{ fontWeight: '500', marginBottom: '4px', color: 'var(--macos-text-primary)' }}>MinerU 自动处理</div>
                        <div>• 有文字层的 PDF → 直接提取文字</div>
                        <div>• 扫描件/无文字层 → 自动启用 OCR</div>
                        <div>• 自动保留表格、公式、版面结构</div>
                      </div>
                    </div>
                  </div>
                </MacOSCard>

                {/* 转换按钮 */}
                <div style={{ marginTop: '20px', display: 'flex', gap: '12px' }}>
                  <MacOSButton 
                    variant="primary" 
                    icon={Wand2}
                    onClick={handleConvert}
                    disabled={converting}
                  >
                    {converting ? '转换中...' : '开始转换'}
                  </MacOSButton>
                </div>

                {/* 进度 */}
                {progress && (
                  <MacOSCard style={{ marginTop: '20px' }}>
                    <div style={{ fontSize: '14px', color: progress.startsWith('✅') ? '#34c759' : progress.startsWith('❌') ? '#ff3b30' : '#1d1d1f' }}>
                      {progress}
                    </div>
                    {converting && (
                      <div style={{ marginTop: '10px', height: '4px', background: '#f5f5f7', borderRadius: '2px', overflow: 'hidden' }}>
                        <div style={{ height: '100%', background: '#007aff', width: '50%', borderRadius: '2px' }} />
                      </div>
                    )}
                  </MacOSCard>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      <input 
        id="pdf-upload" 
        type="file" 
        accept=".pdf" 
        multiple 
        style={{ display: 'none' }} 
        onChange={handleFileSelect} 
      />
    </div>
  )
}
