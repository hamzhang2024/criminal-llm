// 报告页面聊天功能 Hook

import { useState, useCallback, useRef } from 'react'
import { api } from '../../../api'
import { showAlert } from '../../../components/MacOSDialog'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: string
}

const generateId = () => Math.random().toString(36).substr(2, 9)

export function useReportChat(caseId: string | undefined, defendant: string) {
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [chatSelectMode, setChatSelectMode] = useState(false)
  const [selectedChatIds, setSelectedChatIds] = useState<Set<string>>(new Set())
  const chatInputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // 发送消息
  const sendMessage = useCallback(async () => {
    if (!caseId || !chatInput.trim() || chatLoading) return

    const userMessage: ChatMessage = {
      id: generateId(),
      role: 'user',
      content: chatInput.trim(),
      timestamp: new Date().toISOString(),
    }

    setChatMessages(prev => [...prev, userMessage])
    setChatInput('')
    setChatLoading(true)

    try {
      const response = await api.chatAboutCase(caseId, userMessage.content, [])
      const assistantMessage: ChatMessage = {
        id: generateId(),
        role: 'assistant',
        content: response.response || response.message || '无法获取回复',
        timestamp: new Date().toISOString(),
      }
      setChatMessages(prev => [...prev, assistantMessage])
    } catch (e) {
      showAlert({ title: '错误', message: '发送消息失败', variant: 'danger' })
    } finally {
      setChatLoading(false)
      chatInputRef.current?.focus()
    }
  }, [caseId, chatInput, chatLoading, defendant])

  // 清空对话
  const clearChat = useCallback(() => {
    setChatMessages([])
    setChatSelectMode(false)
    setSelectedChatIds(new Set())
  }, [])

  // 删除选中消息
  const deleteSelectedMessages = useCallback(() => {
    setChatMessages(prev => prev.filter(m => !selectedChatIds.has(m.id)))
    setSelectedChatIds(new Set())
    setChatSelectMode(false)
  }, [selectedChatIds])

  // 切换消息选择
  const toggleMessageSelection = useCallback((id: string) => {
    setSelectedChatIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  // 滚动到底部
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  return {
    // 消息
    chatMessages, setChatMessages,
    chatInput, setChatInput,
    chatLoading, setChatLoading,
    // 选择模式
    chatSelectMode, setChatSelectMode,
    selectedChatIds, setSelectedChatIds,
    // refs
    chatInputRef,
    messagesEndRef,
    // 方法
    sendMessage,
    clearChat,
    deleteSelectedMessages,
    toggleMessageSelection,
    scrollToBottom,
  }
}
