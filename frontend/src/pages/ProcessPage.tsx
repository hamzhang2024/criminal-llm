import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileText, Upload, Wand2, ArrowRight, FolderOpen, FileDown, Settings } from 'lucide-react'
import { MacOSTitlebar, MacOSSidebar, MacOSToolbar, MacOSButton, MacOSCard, MacOSEmptyState } from '../components/MacOSLayout'

export function ProcessPage() {
  const navigate = useNavigate()
  const [files, setFiles] = useState<File[]>([])
  const [processing, setProcessing] = useState(false)
  const [progress, setProgress] = useState('')
  
  // 处理选项
  const [options, setOptions] = useState({
    removePassword: true,   // 去除密码
    removeWatermark: true,  // 去除水印
    password: ''            // 如果有密码
  })

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files
    if (selected) {
      setFiles(Array.from(selected))
    }
  }

  const handleProcess = async () => {
    if (files.length === 0) return
    setProcessing(true)
    setProgress('开始处理...')

    try {
      // Step 1: PDF 去水印
      setProgress('步骤 1/2: 去除水印...')
      await new Promise(resolve => setTimeout(resolve, 1000))

      // Step 2: 跳转到转换页面
      setProgress('✅ 处理完成！正在跳转到 PDF转MD...')
      setTimeout(() => {
        navigate('/convert')
      }, 1500)
    } catch (err) {
      setProgress('❌ 处理失败')
    } finally {
      setProcessing(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#ffffff', overflow: 'hidden' }}>
      <MacOSTitlebar showBack onBack={() => navigate('/')} />
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <MacOSSidebar />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <MacOSToolbar title="① PDF处理">
            <MacOSButton variant="primary" icon={FolderOpen} onClick={() => document.getElementById('pdf-upload')?.click()}>
              选择 PDF
            </MacOSButton>
          </MacOSToolbar>

          <div style={{ flex: 1, overflow: 'auto', padding: '30px' }}>
            {files.length === 0 ? (
              <MacOSEmptyState
                icon={FileText}
                title="上传 PDF 开始处理"
                description="去除密码、去除水印，得到干净的案卷 PDF"
                action={
                  <MacOSButton variant="primary" icon={FolderOpen} onClick={() => document.getElementById('pdf-upload')?.click()}>
                    选择文件
                  </MacOSButton>
                }
              />
            ) : (
              <div>
                {/* 文件列表 */}
                <MacOSCard>
                  <h3 style={{ fontSize: '16px', marginBottom: '16px' }}>已选择 {files.length} 个文件</h3>
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
                        <FileDown className="w-5 h-5" style={{ color: '#1e3a5f' }} />
                        <div style={{ fontSize: '14px', color: '#1e3a5f' }}>{file.name.replace('.pdf', '.md')}</div>
                      </div>
                    </div>
                  ))}
                </MacOSCard>

                {/* 处理选项 */}
                <MacOSCard style={{ marginTop: '20px' }}>
                  <h3 style={{ fontSize: '16px', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Settings className="w-4 h-4" />
                    处理选项
                  </h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer' }}>
                      <input 
                        type="checkbox" 
                        checked={options.removePassword}
                        onChange={(e) => setOptions({...options, removePassword: e.target.checked})}
                        style={{ width: '18px', height: '18px' }}
                      />
                      <span>去除密码保护</span>
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer' }}>
                      <input 
                        type="checkbox" 
                        checked={options.removeWatermark}
                        onChange={(e) => setOptions({...options, removeWatermark: e.target.checked})}
                        style={{ width: '18px', height: '18px' }}
                      />
                      <span>去除水印</span>
                    </label>
                  </div>
                </MacOSCard>

                {/* 处理按钮 */}
                <div style={{ marginTop: '20px', display: 'flex', gap: '12px' }}>
                  <MacOSButton 
                    variant="primary" 
                    icon={Wand2}
                    onClick={handleProcess}
                    disabled={processing}
                  >
                    {processing ? '处理中...' : '开始处理'}
                  </MacOSButton>
                </div>

                {/* 进度 */}
                {progress && (
                  <MacOSCard style={{ marginTop: '20px' }}>
                    <div style={{ fontSize: '14px', color: progress.startsWith('✅') ? '#2d8f3d' : progress.startsWith('❌') ? '#ff3b30' : '#1d1d1f' }}>
                      {progress}
                    </div>
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
