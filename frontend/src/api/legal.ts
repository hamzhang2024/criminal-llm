// 法律知识库 API

import { API_BASE } from './client'

export async function listLegalKB(): Promise<any> {
  const res = await fetch(`${API_BASE}/legal-knowledge`)
  return res.json()
}

export async function getLegalKBItem(itemId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/legal-knowledge/${encodeURIComponent(itemId)}`)
  return res.json()
}

export async function createLegalKBItem(title: string, content: string, crimeType: string = '', itemId?: string): Promise<any> {
  const res = await fetch(`${API_BASE}/legal-knowledge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, content, crime_type: crimeType, id: itemId })
  })
  return res.json()
}

export async function updateLegalKBItem(itemId: string, updates: { title?: string; content?: string; crime_type?: string }): Promise<any> {
  const res = await fetch(`${API_BASE}/legal-knowledge/${encodeURIComponent(itemId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates)
  })
  return res.json()
}

export async function deleteLegalKBItem(itemId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/legal-knowledge/${encodeURIComponent(itemId)}`, {
    method: 'DELETE'
  })
  return res.json()
}

export async function searchLaws(crimeType: string): Promise<any> {
  const res = await fetch(`${API_BASE}/legal-knowledge/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ crime_type: crimeType })
  })
  return res.json()
}