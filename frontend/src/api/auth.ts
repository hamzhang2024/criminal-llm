// 认证 API（通过 Tauri 命令调用）

import { isTauri, tauriInvoke } from './client'

interface AuthResult {
  success: boolean
  token?: string
  email?: string
  error?: string
}

export interface LoginResponse {
  success: boolean
  token: string
  email: string
}

export interface VerifyResponse {
  success: boolean
  email: string
  sub: string
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  const result: AuthResult = isTauri()
    ? await tauriInvoke('auth_login', { email, password })
    : { success: false, error: '浏览器模式下不支持 Tauri 认证' }
  if (!result.success) {
    throw new Error(result.error || '登录失败')
  }
  return {
    success: true,
    token: result.token || '',
    email: result.email || '',
  }
}

export async function verifyToken(token: string): Promise<VerifyResponse> {
  const result: AuthResult = isTauri()
    ? await tauriInvoke('auth_verify', { token })
    : { success: false, error: '浏览器模式下不支持 Tauri 认证' }
  if (!result.success) {
    throw new Error(result.error || 'Token 无效')
  }
  return {
    success: true,
    email: result.email || '',
    sub: '',
  }
}

export async function register(email: string, password: string): Promise<{ success: boolean; message?: string }> {
  const result: AuthResult = isTauri()
    ? await tauriInvoke('auth_register', { email, password })
    : { success: false, error: '浏览器模式下不支持 Tauri 认证' }
  if (!result.success) {
    throw new Error(result.error || '注册失败')
  }
  return { success: true, message: '注册成功' }
}

export async function resetPassword(email: string, oldPassword: string, newPassword: string): Promise<{ success: boolean; message?: string }> {
  const result: AuthResult = isTauri()
    ? await tauriInvoke('auth_reset_password', { email, oldPassword, newPassword })
    : { success: false, error: '浏览器模式下不支持 Tauri 认证' }
  if (!result.success) {
    throw new Error(result.error || '重置失败')
  }
  return { success: true, message: '密码已重置' }
}

export async function sendResetCode(email: string): Promise<{ success: boolean; message?: string }> {
  const result: AuthResult = isTauri()
    ? await tauriInvoke('auth_send_reset_code', { email })
    : { success: false, error: '浏览器模式下不支持 Tauri 认证' }
  if (!result.success) {
    throw new Error(result.error || '发送验证码失败')
  }
  return { success: true, message: result.error || '验证码已发送' }
}

export async function resetWithCode(email: string, code: string, newPassword: string): Promise<{ success: boolean; message?: string }> {
  const result: AuthResult = isTauri()
    ? await tauriInvoke('auth_reset_with_code', { email, code, newPassword })
    : { success: false, error: '浏览器模式下不支持 Tauri 认证' }
  if (!result.success) {
    throw new Error(result.error || '重置失败')
  }
  return { success: true, message: '密码已重置' }
}

// Token 本地存储管理
export function getToken(): string | null {
  return localStorage.getItem('auth_token')
}

export function setToken(token: string): void {
  localStorage.setItem('auth_token', token)
}

export function clearToken(): void {
  localStorage.removeItem('auth_token')
}

export function getAuthEmail(): string | null {
  return localStorage.getItem('auth_email')
}

export function setAuthEmail(email: string): void {
  localStorage.setItem('auth_email', email)
}

export function clearAuthEmail(): void {
  localStorage.removeItem('auth_email')
}