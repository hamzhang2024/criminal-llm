# Criminal LLM - P2P 多律师协作改造计划

> 从单机桌面应用转变为 Web 服务 + P2P 协作网络
> 
> 创建时间：2026-05-04

---

## 项目概述

**Criminal LLM** 当前是 Tauri 桌面应用（React + FastAPI），单机使用。改造目标：
1. 微信扫码即可使用（Web 化 + 云部署）
2. 多律师通过 P2P 网络共享案卷、共享分析内容、实时聊天
3. 所有 P2P 传输端到端加密
4. 用户贡献带宽作为中继节点，加速网络传输

---

## 系统架构

```
云服务器 (阿里云 ECS, 8C16G, 5Mbps)
┌─────────────────────────────────────────────┐
│  Nginx (HTTPS + 反向代理)                    │
│    │                                        │
│  FastAPI (API + WebSocket 信令)              │
│    │                                        │
│  Redis (Session、在线状态、房间)             │
│    │                                        │
│  PostgreSQL (用户、案件元数据、权限)         │
│    │                                        │
│  阿里云 OSS (案卷文件、分析报告 - 加密存储)   │
└─────────────────────────────────────────────┘
       ▲  HTTPS/WebSocket
       │
  ┌────┼────────┐
  │    │        │
手机A  手机B    手机C
(WebRTC P2P 直连)
  │    │        │
  └── DataChannel ──┘
  聊天/文件分片/协同/中继转发
```

**云服务器职责**：登录认证、API、信令握手、文件初始存储锚点。
**P2P 网络职责**：聊天、文件加速传输（分片）、协同编辑、用户中继转发。

---

## 核心设计决策

### 微信扫码登录
- 前端生成 UUID 二维码 → 微信扫码打开链接 → 自动注册/登录
- 零微信 API 依赖，就是普通 URL 访问
- 如微信内置浏览器限制 WebRTC，引导用户「在浏览器中打开」

### 文件传输（双路径）
| 场景 | 路径 | 说明 |
|------|------|------|
| 初始上传 | OSS 直传 | 案卷先加密上传到 OSS，支持断点续传 |
| 共享下载 | P2P 优先 | 多 peer 并发拉取加密分片（类 BitTorrent），peer 不在线时回退 OSS |
| 文件大小 | 正常 100-500MB，大案卷 1-2GB | 分片大小 5MB，MD5 校验 |

### P2P 网络加速
- A-B 直连慢时，C 自动成为中继：A→C→B
- 中继节点仅转发加密数据，无法解密内容
- STUN（Google 公共服务）+ 用户贡献 TURN + 云服务器 TURN 兜底

### 安全
- 传输层：WebRTC 内置 DTLS-SRTP
- 应用层：AES-256-GCM 端到端加密
- 密钥交换：ECDH，仅通信双方持有
- 存储层：OSS 加密存储，云端不持有解密密钥

---

## 阶段规划

### Phase 0：证据浏览增强（PDF 浏览 + 批注基础）（1-2 周）

> 独立于 P2P 改造，现有桌面/Web 架构即可实现。

#### 0.1 PDF 浏览

**现状**：ReportPage 左侧面板仅显示 MD 文本（`marked` 渲染）。`react-pdf` 已在 `package.json` 中但未使用。后端已有 `serve-file` API 可提供 PDF 文件。

**设计**：
- 左侧证据面板顶部增加 **MD / PDF 切换按钮**
- PDF 模式：通过 `serve-file` API 从 `processed/` 目录加载 PDF，`react-pdf` 逐页渲染
- 分页浏览 + 缩放控制
- MD 文件与 PDF 文件的映射关系：`xxx_去水印.pdf` ↔ `xxx_去水印.md`（同名不同扩展名）
- PDF 查看器保持与现有证据下拉选择器的联动

**文件变化**：
- `frontend/src/pages/ReportPage.tsx` — 左侧面板增加 PDF 渲染组件
- `frontend/src/components/PdfViewer.tsx` — NEW: PDF 查看器组件（分页、缩放、懒加载）

#### 0.2 报告批注

**设计**：「选中文字触发批注 + 侧边批注栏」混合模式

**交互流程**：
1. 用户在中间报告区选中一段文字
2. 弹出小浮标「添加批注」
3. 点击后右侧面板切换到「批注」tab
4. 输入批注内容，保存
5. 被批注的段落左侧出现竖线标记 + 批注计数
6. 点击标记，右侧显示该段落的所有批注（线程式回复）

**定位策略**（解决报告修改后批注错位）：
- `textHash`: 选中文字的短哈希（MD5 前 8 位），报告未变时精确命中
- `contextBefore` / `contextAfter`: 选中文字前后各 20 字快照，用于模糊重定位
- 都找不到时，批注灰化标记为「原文已变更」，内容仍可见

**数据结构**：
```typescript
interface Annotation {
  id: string
  caseId: string
  textHash: string           // 选中文字的短哈希
  selectedText: string       // 原始选中文字（前 50 字）
  contextBefore: string      // 选中前 20 字
  contextAfter: string       // 选中后 20 字
  content: string            // 批注内容
  authorId: string
  authorName: string
  createdAt: string
  replies: Array<{           // 线程式回复
    id: string
    authorId: string
    authorName: string
    content: string
    createdAt: string
  }>
}
```

**视觉示意**：
```
┌─ 中间：报告内容 ──────────────────────────┐  ┌─ 右侧：批注面板 ──────────────────┐
│                                           │  │ 批注 (3)                         │
│ │ 一、构成要件符合性                      │  │ ─────────────────────────────── │
│ │   行为主体：一般主体 ✓ 2              │← │ 🔵 张三 14:32                     │
│ │   主观方面：故意                        │  │ "行为主体为一般主体"              │
│ │                                         │  │ 质疑：本案被告人是否有特殊身份？  │
│ │ 二、违法性                              │  │   └─ 🔴 李四 14:35               │
│ │   不存在正当防卫事由 ✓ 1              │← │      同意，案卷中被告人为外包人员 │
│                                           │  │                                  │
│                                           │  │ 🟢 王五 15:10                     │
└───────────────────────────────────────────┘  │ "不存在正当防卫事由"              │
                                               │ 需补充：被害人是否存在过错？     │
                                               └──────────────────────────────────┘
```

**与 P2P 协作的衔接**：
- 批注通过 P2P DataChannel 实时同步（Phase 4 实现）
- 每个批注唯一 ID，不冲突
- 新律师加入后拉取现有批注
- 批注持久化：localStorage + 可选上传 OSS

**文件变化**：
- `frontend/src/components/AnnotationPanel.tsx` — NEW: 批注面板
- `frontend/src/components/AnnotationOverlay.tsx` — NEW: 选中文字浮标
- `frontend/src/lib/annotations.ts` — NEW: 批注数据模型 + 定位逻辑
- `backend/case_manager.py` — 新增批注 CRUD API（或独立 `annotation_api.py`）

### Phase 1：Web 化 + 微信扫码登录 + 云部署（2-3 周）

| # | 任务 | 说明 |
|---|------|------|
| 1.1 | 脱离 Tauri | 移除 `frontend/src-tauri/`，前端改为纯 Web，构建产物由 FastAPI serve |
| 1.2 | 微信扫码注册 | 前端 UUID 生成 + 二维码展示 → 扫码打开链接 → 后端自动注册 → 签发 JWT |
| 1.3 | Session 管理 | JWT + Redis，扫码即签发，支持吊销 |
| 1.4 | 用户系统 | PostgreSQL `users` 表：id, openid, created_at, status |
| 1.5 | 多租户隔离 | `data/cases/{user_id}/` → 云端改为 OSS `cases/{user_id}/` |
| 1.6 | 认证中间件 | FastAPI 所有 API 加 `Authorization: Bearer <jwt>` 校验 |
| 1.7 | Docker Compose | Nginx + FastAPI + Redis + PostgreSQL 一键部署 |
| 1.8 | HTTPS | Let's Encrypt 证书自动续期 |

**交付物**：用户扫码 → 登录 → 案件列表 → 上传/分析/查看报告，流程与桌面版一致。

---

### Phase 2：P2P 基础设施（2-3 周）

| # | 任务 | 说明 |
|---|------|------|
| 2.1 | 信令服务器 | FastAPI WebSocket，仅交换 SDP/ICE，不转发业务数据 |
| 2.2 | 房间管理 | Redis 管理在线用户、房间成员、加入/离开事件 |
| 2.3 | WebRTC 连接管理 | 前端 `P2PManager`：创建/加入房间、SDP 协商、ICE 候选交换、重连 |
| 2.4 | STUN/TURN 配置 | Google STUN + 云服务器 TURN (coturn) + 用户贡献 TURN |
| 2.5 | 端到端加密 | ECDH 密钥交换 → 派生 AES-GCM 对称密钥 → DataChannel 全加密 |
| 2.6 | 连接拓扑 | Mesh 全连接（≤5 人房间）或 Star 拓扑（>5 人选主节点） |
| 2.7 | 心跳与存活 | P2P 心跳包、断线重连、自动降级到 WebSocket |

**关键设计**：
- 信令服务器不存储任何聊天/文件内容
- 每个 DataChannel 消息 AES-256-GCM 加密
- 加密密钥通过 ECDH 协商，仅通信双方持有

---

### Phase 3：P2P 聊天 + 案卷共享（2-3 周）

| # | 任务 | 说明 |
|---|------|------|
| 3.1 | 协作聊天室 | DataChannel 广播消息，支持文本/图片/文件引用 |
| 3.2 | 案卷共享 | 选择案件 → 生成邀请码 → 对方输入/扫码 → P2P 开始传输 |
| 3.3 | 文件分片传输 | DataChannel 可靠模式 + 5MB 分片 + MD5 校验 + 断点续传 |
| 3.4 | 传输进度 UI | 前端进度条、速度、ETA、分片状态 |
| 3.5 | 用户中继 (TURN) | A-B 直连慢 → C 自动中继 → 加密数据通过 C 转发 |
| 3.6 | OSS 回退 | P2P peer 不在线 → 从 OSS 下载加密文件 → 本地解密 |
| 3.7 | 在线状态 | 显示谁在查看同一案件 |

---

### Phase 4：多律师协同分析（2-3 周）

| # | 任务 | 说明 |
|---|------|------|
| 4.1 | 协同批注 | 案卷高亮/标注/笔记通过 DataChannel 同步 |
| 4.2 | 协同编辑 | Yjs (CRDT) 通过 DataChannel 绑定，多人同时编辑不冲突 |
| 4.3 | 共享分析结果 | 三阶层分析报告 P2P 共享 |
| 4.4 | 权限系统 | 案卷所有者 → 可读 / 可批注 / 可编辑 三级 |
| 4.5 | 操作日志 | 协同操作审计日志（本地存储，可选同步） |
| 4.6 | 冲突解决 | CRDT 自动合并，无中心冲突检测 |

---

## 文件结构变化

```
criminal-llm/
├── backend/
│   ├── main.py                    # 增加 WebSocket 信令
│   ├── auth.py                    # NEW: 微信扫码登录 + JWT
│   ├── user_manager.py            # NEW: 用户 CRUD
│   ├── annotation_api.py          # NEW: 批注 CRUD API
│   ├── p2p_signaling.py           # NEW: WebSocket 信令处理
│   ├── room_manager.py            # NEW: 房间/在线状态
│   ├── case_manager.py            # 改造：多租户 + OSS + 批注
│   ├── file_transfer.py           # NEW: OSS 上传/下载 + 分片管理
│   ├── ... (其他现有文件)
│   └── config.py                  # 云端配置
│
├── frontend/src/
│   ├── api/
│   │   ├── auth.ts                # NEW: 登录/注册 API
│   │   └── p2p.ts                 # NEW: P2P 连接 API
│   ├── lib/
│   │   ├── annotations.ts         # NEW: 批注数据模型 + 定位
│   │   ├── p2p-manager.ts         # NEW: WebRTC 封装
│   │   ├── crypto.ts              # NEW: 端到端加密
│   │   ├── file-transfer.ts       # NEW: P2P 分片传输
│   │   ├── turn-relay.ts          # NEW: 用户中继逻辑
│   │   └── collab-engine.ts       # NEW: 协同编辑引擎 (Yjs)
│   ├── components/
│   │   ├── PdfViewer.tsx          # NEW: PDF 查看器
│   │   ├── AnnotationPanel.tsx    # NEW: 批注面板
│   │   └── AnnotationOverlay.tsx  # NEW: 选中文字浮标
│   ├── pages/
│   │   ├── LoginPage.tsx          # NEW: 扫码登录
│   │   ├── ReportPage.tsx         # 改造：PDF/MD 切换 + 批注
│   │   ├── ChatRoom.tsx           # NEW: P2P 聊天
│   │   ├── CollaboratePage.tsx    # NEW: 协同分析
│   │   └── ... (现有页面)
│   └── ...
│
├── infra/
│   ├── docker-compose.yml         # NEW
│   ├── nginx.conf                 # NEW
│   ├── coturn.conf                # NEW: TURN 服务器配置
│   └── deploy.sh                  # NEW
│
└── data/                          # 本地开发保留
```

---

## 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| WebRTC | 浏览器原生 | 零依赖，手机浏览器直接支持 |
| 信令 | FastAPI WebSocket | 复用现有后端 |
| P2P 加密 | WebRTC DTLS + 应用层 AES-GCM | 双层加密 |
| 协同编辑 | Yjs (CRDT) | 成熟，支持 DataChannel 绑定 |
| 二维码 | `qrcode.react` | 前端生成 |
| Session | JWT + Redis | 无状态 + 快速吊销 |
| 数据库 | PostgreSQL | 用户、权限、案件元数据 |
| 对象存储 | 阿里云 OSS | 大文件持久化 + 分片下载 |
| TURN | coturn | 开源，支持用户贡献节点 |
| 部署 | Docker Compose | 单机够用 |

---

## 数据库设计（Phase 1）

```sql
-- 用户表
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    openid VARCHAR(64) UNIQUE NOT NULL,  -- 微信扫码唯一标识
    nickname VARCHAR(128),
    avatar_url VARCHAR(512),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login TIMESTAMPTZ,
    status VARCHAR(16) DEFAULT 'active'  -- active / banned
);

-- 案件表
CREATE TABLE cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    name VARCHAR(256) NOT NULL,
    oss_prefix VARCHAR(512) NOT NULL,  -- OSS 存储前缀
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    status VARCHAR(16) DEFAULT 'active'
);

-- 案件共享权限表
CREATE TABLE case_shares (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID NOT NULL REFERENCES cases(id),
    owner_id UUID NOT NULL REFERENCES users(id),
    shared_to_id UUID NOT NULL REFERENCES users(id),
    permission VARCHAR(16) NOT NULL,  -- read / annotate / edit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(case_id, shared_to_id)
);

-- P2P 房间表（信令用 Redis，此表仅审计）
CREATE TABLE rooms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID REFERENCES cases(id),
    created_by UUID NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    closed_at TIMESTAMPTZ
);
```

---

## 风险和注意事项

1. **微信内置浏览器 WebRTC 兼容性**：iOS 微信内置浏览器可能不支持 WebRTC DataChannel。需要测试，如不支持则引导「在浏览器中打开」。
2. **NAT 穿透率**：移动网络下 WebRTC 直连成功率约 70-80%，剩余依赖 TURN 中继。云服务器需部署 coturn 兜底。
3. **iOS Safari WebRTC**：15.2+ 支持 DataChannel，需确认目标用户 iOS 版本。
4. **大文件传输可靠性**：DataChannel 需可靠模式 + 手动分片 + MD5 校验 + 断点续传。
5. **成本控制**：初期云服务器约 300-500 元/月（ECS + OSS + 域名 + SSL），OSS 流量按实际使用。
6. **P2P peer 稳定性**：用户贡献的 TURN 节点在线时长不可控，关键传输必须有 OSS 回退路径。

---

## 后续待确认

- [ ] 云服务器选型（阿里云/腾讯云，地域）
- [ ] 域名注册与备案
- [ ] 微信服务号/公众号是否已有（影响扫码方案细节）
- [ ] OSS 存储容量预估（案卷总量级）
- [ ] Phase 1 启动时间
