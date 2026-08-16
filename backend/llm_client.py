"""
LLM 客户端 - 支持多种 LLM 提供商

从应用配置 (DATA_DIR/criminal-llm-config.json) 获取 API Key、Base URL 和模型
"""
import asyncio
import httpx
import sys
import logging
import time
from typing import Optional, Dict, Any, List

# 导入上下文预算计算函数
from analysis_engine import (
    _get_content_budget_chars,
    _get_indictment_budget_chars,
    _get_evidence_budget_chars,
    _get_report_budget_chars
)

logger = logging.getLogger(__name__)

# 进程级缓存命中率累计器：跨 LLMClient 实例累计（LLM 调用均在同一事件循环内，简单全局变量即可）
_PROCESS_CACHE_STATS = {"hit": 0, "miss": 0, "calls": 0}


def _record_process_cache_stats(hit: int, miss: int) -> None:
    """累加进程级缓存命中统计（chat/completions 与豆包 Responses 两条路径共用）"""
    _PROCESS_CACHE_STATS["hit"] += hit
    _PROCESS_CACHE_STATS["miss"] += miss
    _PROCESS_CACHE_STATS["calls"] += 1


def get_cache_stats() -> dict:
    """获取进程级缓存命中率统计（供 /api/llm/cache-stats 展示）"""
    hit = _PROCESS_CACHE_STATS["hit"]
    miss = _PROCESS_CACHE_STATS["miss"]
    total = hit + miss
    return {
        "hit_tokens": hit,
        "miss_tokens": miss,
        "hit_rate": round(hit / total, 4) if total > 0 else 0,
        "calls": _PROCESS_CACHE_STATS["calls"],
    }


def reset_cache_stats() -> None:
    """重置进程级缓存命中率统计（主要供测试隔离使用）"""
    _PROCESS_CACHE_STATS["hit"] = 0
    _PROCESS_CACHE_STATS["miss"] = 0
    _PROCESS_CACHE_STATS["calls"] = 0

# 各模型家族的最大输出 token 上限与计算函数已移至 context_budget，
# 供预算公式（输入侧）与 chat()（输出侧）共用同一事实源，防止 input+output 超上下文
from context_budget import DEFAULT_OUTPUT_CAP, compute_max_output_tokens

# 打包后 certifi 证书路径可能失效，macOS 用系统证书
if sys.platform == "darwin" and getattr(sys, "frozen", False):
    _SSL_VERIFY = "/etc/ssl/cert.pem"
else:
    _SSL_VERIFY = True

# 导入法律知识库
try:
    from legal_knowledge import get_legal_knowledge, get_dynamic_legal_knowledge, THEORY_THREE_TIERS, CONSTITUTIVE_ELEMENT_ANALYSIS
except ImportError:
    def get_legal_knowledge():
        return ""
    def get_dynamic_legal_knowledge(crime_type=None):
        return ""
    THEORY_THREE_TIERS = ""
    CONSTITUTIVE_ELEMENT_ANALYSIS = ""


def _get_bailian_config() -> tuple[str, Optional[str], str]:
    """
    获取 LLM 配置（从 config_manager 读取，带缓存）

    Returns:
        (baseUrl, apiKey, defaultModel)
    """
    from config_manager import load_config

    config = load_config()
    api_key = config.get("llm_api_key")
    base_url = config.get("llm_base_url", "")
    default_model = config.get("llm_model", "")

    return base_url, api_key, default_model


class LLMRetryExhaustedError(Exception):
    """LLM 请求已耗尽所有内部重试，上层不应再次重试。"""
    pass


class LLMClient:
    """LLM 客户端 - 支持 OpenAI 兼容 API"""

    # 类级别配置缓存
    _config_cache: Optional[Dict[str, Any]] = None
    _config_cache_time: float = 0
    _config_cache_ttl: float = 30.0  # 缓存有效期 30 秒

    def __init__(self):
        base_url, api_key, default_model = self._get_cached_config()
        self.base_url = base_url
        self.api_key = api_key
        # 规范化模型名称：DeepSeek API 要求全小写
        self.model = default_model.lower() if default_model else ""
        # 分层超时：connect/write/pool 各 60s，read 180s 防 stream hang
        # 单一数值 timeout 在 streaming 响应下每次收到 chunk 重置计时器，
        # read timeout 是单次读取超时，180s 无数据则抛 ReadTimeout → 触发重试
        self.timeout = httpx.Timeout(
            connect=30.0,
            read=180.0,
            write=60.0,
            pool=30.0,
        )
        self.client = httpx.AsyncClient(timeout=self.timeout, verify=_SSL_VERIFY)

        # 缓存命中率统计（会话级别累计）
        self._cache_hit_tokens = 0
        self._cache_miss_tokens = 0
        self._total_requests = 0

        # 豆包 Responses API 降级标记：/responses 返回 4xx 后本进程不再尝试
        self._responses_api_unsupported = False

        logger.info("[LLM 客户端] baseUrl: %s", base_url)
        logger.info("[LLM 客户端] model: %s", self.model)
        logger.info("[LLM 客户端] apiKey: %s", '已配置' if api_key else '未配置')

    @classmethod
    def _get_cached_config(cls) -> tuple[str, Optional[str], str]:
        """获取配置（带缓存，30秒有效期）"""
        now = time.time()
        if cls._config_cache is not None and (now - cls._config_cache_time) < cls._config_cache_ttl:
            config = cls._config_cache
        else:
            from config_manager import load_config
            config = load_config()
            cls._config_cache = config
            cls._config_cache_time = now

        api_key = config.get("llm_api_key")
        base_url = config.get("llm_base_url", "")
        default_model = config.get("llm_model", "")
        return base_url, api_key, default_model

    def reload_config(self):
        """重新读取配置（强制刷新缓存）"""
        # 清除缓存
        LLMClient._config_cache = None
        base_url, api_key, default_model = self._get_cached_config()
        self.base_url = base_url
        self.api_key = api_key
        # 规范化模型名称：DeepSeek API 要求全小写（deepseek-v4-pro/deepseek-v4-flash）
        self.model = default_model.lower() if default_model else ""
        logger.info("[LLM 客户端] 配置已重载 baseUrl: %s, model: %s", base_url, self.model)

    def get_cache_stats(self) -> dict:
        """获取缓存命中率统计"""
        total = self._cache_hit_tokens + self._cache_miss_tokens
        return {
            "hit_tokens": self._cache_hit_tokens,
            "miss_tokens": self._cache_miss_tokens,
            "total_requests": self._total_requests,
            "hit_rate": round(self._cache_hit_tokens / total * 100, 1) if total > 0 else 0.0,
        }

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        model_override: Optional[str] = None
    ) -> str:
        """
        发送聊天请求

        model_override：分层路由用，仅覆盖本次请求的 model 字段
        （不改实例配置，不影响其他调用；base_url/key/超时/重试均不变）。
        优先级：model_override > model > 实例默认模型。
        """
        # 每次请求前重新读取配置，确保配置变更后立即生效
        self.reload_config()

        if not self.api_key:
            raise Exception("API Key 未配置，请先在「设置」中填写")
        if not self.base_url:
            raise Exception("Base URL 未配置，请先在「设置」中填写大模型地址")
        if not self.model:
            raise Exception("模型名称未配置，请先在「设置」中填写模型名称")

        # 本次请求生效的模型（分层路由只改这一处，其余配置共享）
        effective_model = model_override or model or self.model

        url = f"{self.base_url}/chat/completions"

        # 根据模型上下文窗口动态计算最大输出 tokens（防止 API 默认值截断长输出）
        from config_manager import get_config_value
        context_limit = int(get_config_value("model_context_limit", "250000"))
        # 预留 20% 给输入，其余作为输出上限；同时按模型家族上限截断，
        # 防止 context_limit 过大导致 max_tokens 超过模型实际上限而 API 400
        max_output_tokens = compute_max_output_tokens(context_limit, effective_model)

        payload = {
            "model": effective_model,
            "messages": messages,
            "max_tokens": max_output_tokens
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        # 豆包（火山方舟）：前缀缓存只在 Responses API 暴露，走 /responses 端点；
        # chat/completions 端点没有缓存参数，保持原路径给其他提供商
        if self._is_doubao() and not self._responses_api_unsupported:
            try:
                return await self._chat_via_responses_api(messages, effective_model, headers, max_output_tokens)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response is not None else 0
                if 400 <= status < 500:
                    # 模型/账户不支持 Responses API：本进程内降级 chat/completions，记一次性 warning
                    logger.warning(
                        "[LLM] Responses API 返回 %s（模型或账户不支持），本进程后续调用回退 chat/completions，前缀缓存不生效",
                        status,
                    )
                    self._responses_api_unsupported = True
                else:
                    error_body = e.response.text[:500] if e.response else "无响应内容"
                    raise Exception(f"API 请求失败：{status}\n{error_body}")

        logger.info("[LLM 请求] url=%s, model=%s, messages=%d", url, payload['model'], len(messages))

        # 重试 2 次，指数退避
        last_error = None
        for attempt in range(3):
            try:
                req_start = time.time()

                # 后台进度：每 30 秒打印一次等待状态
                async def progress_tick():
                    while True:
                        await asyncio.sleep(30)
                        elapsed = time.time() - req_start
                        logger.info("[LLM 请求] 等待响应... %.0fs", elapsed)

                tick = asyncio.create_task(progress_tick())
                try:
                    response = await self.client.post(url, json=payload, headers=headers)
                finally:
                    tick.cancel()

                response.raise_for_status()
                latency_ms = (time.time() - req_start) * 1000

                data = response.json()

                if "choices" in data and len(data["choices"]) > 0:
                    logger.info("[LLM 请求] 成功，耗时 %.1fs", latency_ms/1000)

                    # 缓存命中率统计
                    usage = data.get("usage", {})
                    hit = usage.get("prompt_cache_hit_tokens", 0)
                    miss = usage.get("prompt_cache_miss_tokens", 0)
                    if hit > 0 or miss > 0:
                        self._cache_hit_tokens += hit
                        self._cache_miss_tokens += miss
                        self._total_requests += 1
                        _record_process_cache_stats(hit, miss)
                        total = hit + miss
                        hit_rate = (hit / total * 100) if total > 0 else 0
                        overall_total = self._cache_hit_tokens + self._cache_miss_tokens
                        overall_rate = (self._cache_hit_tokens / overall_total * 100) if overall_total > 0 else 0
                        logger.info("[LLM 缓存] 本次命中: %d/%d tokens (%.0f%%), 累计: %d/%d (%.0f%%), 共 %d 次请求", hit, total, hit_rate, self._cache_hit_tokens, overall_total, overall_rate, self._total_requests)

                    # 检测输出是否被截断
                    choice = data["choices"][0]
                    finish_reason = choice.get("finish_reason", "")
                    if finish_reason == "length":
                        logger.warning("[LLM] 输出被截断！finish_reason=length，建议增大 max_output_tokens 配置（当前 %d）", max_output_tokens)
                    elif finish_reason == "stop":
                        pass  # 正常结束

                    return choice["message"]["content"]

                return str(data)
            except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                last_error = e
                wait = 5 * (attempt + 1)  # 5s, 10s, 15s
                print(f"[LLM 超时] 第 {attempt+1}/3 次重试，等待 {wait}s...")
                await asyncio.sleep(wait)
            except httpx.HTTPStatusError as e:
                error_body = e.response.text[:500] if e.response else "无响应内容"
                raise Exception(f"API 请求失败：{e.response.status_code}\n{error_body}")

        raise LLMRetryExhaustedError(f"LLM 请求超时（已重试 {3} 次）: {last_error}")

    def _is_doubao(self) -> bool:
        """是否为豆包（火山方舟）服务：前缀缓存只在火山方舟 Responses API 暴露"""
        return "volces.com" in (self.base_url or "")

    async def _chat_via_responses_api(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str],
        headers: Dict[str, str],
        max_output_tokens: int,
    ) -> str:
        """
        豆包（火山方舟）Responses API 调用路径

        请求体启用前缀缓存：caching={"type": "enabled", "prefix": True} + store=True。
        我们的调用是独立的共享前缀调用（非多轮会话），不使用 previous_response_id。
        messages 风格 [{role, content}] 与 input 字段直接兼容，原样传递。
        """
        url = f"{self.base_url}/responses"
        payload = {
            "model": model or self.model,
            "input": messages,
            "caching": {"type": "enabled", "prefix": True},
            "store": True,
            "max_output_tokens": max_output_tokens,
        }

        logger.info("[LLM 请求] url=%s, model=%s, messages=%d（Responses API 前缀缓存）", url, payload["model"], len(messages))

        # 重试策略与 chat() 主路径一致：3 次，指数退避
        last_error = None
        for attempt in range(3):
            try:
                req_start = time.time()

                async def progress_tick():
                    while True:
                        await asyncio.sleep(30)
                        logger.info("[LLM 请求] 等待响应... %.0fs", time.time() - req_start)

                tick = asyncio.create_task(progress_tick())
                try:
                    response = await self.client.post(url, json=payload, headers=headers)
                finally:
                    tick.cancel()

                response.raise_for_status()
                latency_ms = (time.time() - req_start) * 1000

                data = response.json()
                logger.info("[LLM 请求] 成功，耗时 %.1fs", latency_ms / 1000)

                # 缓存命中率统计（前缀缓存命中并入会话级统计）
                self._record_responses_cache_stats(data)

                return self._parse_responses_text(data)
            except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                last_error = e
                wait = 5 * (attempt + 1)  # 5s, 10s, 15s
                print(f"[LLM 超时] 第 {attempt+1}/3 次重试，等待 {wait}s...")
                await asyncio.sleep(wait)
            except httpx.HTTPStatusError:
                # 交回 chat() 决定降级（4xx）或报错（5xx）
                raise

        raise LLMRetryExhaustedError(f"LLM 请求超时（已重试 {3} 次）: {last_error}")

    @staticmethod
    def _parse_responses_text(data: Dict[str, Any]) -> str:
        """
        解析 Responses API 响应文本，防御性兼容两种形态：
        1. 扁平形态（ark-cli 扁平化后）：content 直接是文本
        2. 原始 output 列表形态：取 role=assistant 项的 content[0].text
        """
        content = data.get("content")
        if isinstance(content, str) and content:
            return content

        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("role") == "assistant" or item.get("type") == "message":
                parts = item.get("content")
                if isinstance(parts, str) and parts:
                    return parts
                for part in parts or []:
                    if isinstance(part, dict) and part.get("text"):
                        return part["text"]

        return str(data)

    def _record_responses_cache_stats(self, data: Dict[str, Any]) -> None:
        """Responses API 缓存命中统计：cached_tokens 并入会话级缓存命中率统计"""
        usage = data.get("usage") or {}
        hit = usage.get("cached_tokens") or 0
        # 兼容 prompt_tokens / input_tokens 两种字段名
        prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
        miss = max(prompt_tokens - hit, 0) if prompt_tokens else 0

        if hit > 0 or miss > 0:
            self._cache_hit_tokens += hit
            self._cache_miss_tokens += miss
            self._total_requests += 1
            _record_process_cache_stats(hit, miss)
            total = hit + miss
            hit_rate = (hit / total * 100) if total > 0 else 0
            overall_total = self._cache_hit_tokens + self._cache_miss_tokens
            overall_rate = (self._cache_hit_tokens / overall_total * 100) if overall_total > 0 else 0
            logger.info("[LLM 缓存] 本次命中: %d/%d tokens (%.0f%%), 累计: %d/%d (%.0f%%), 共 %d 次请求", hit, total, hit_rate, self._cache_hit_tokens, overall_total, overall_rate, self._total_requests)
        elif data.get("caching"):
            # 无 usage 缓存字段时至少打印 caching 对象，便于排查缓存是否启用
            logger.info("[LLM 缓存] caching=%s", data.get("caching"))

    async def analyze_case(
        self,
        defendant: str,
        evidence_texts: List[Dict[str, Any]],
        model: Optional[str] = None,
        crime_type: Optional[str] = None  # 新增：罪名类型，用于动态加载知识
    ) -> str:
        """
        分析案卷内容

        每次分析前重新读取配置，确保使用最新设置
        
        Args:
            defendant: 辩护对象（被告人）姓名
            evidence_texts: 证据文本列表，每项包含 id, filename, type, text
            model: 模型名称（可选）
            crime_type: 罪名类型（可选），如"职务侵占罪"，用于动态加载相关法条、司法解释、案例
        
        Returns:
            Markdown 格式的分析报告
        """
        # 每次分析前重新读取配置，确保使用最新设置
        self.reload_config()

        # 动态加载罪名特定知识
        crime_specific_knowledge = ""
        if crime_type:
            crime_specific_knowledge = get_dynamic_legal_knowledge(crime_type)
            if crime_specific_knowledge:
                print(f"[LLM 客户端] 已加载 {crime_type} 相关知识")
        
        # 使用三阶层理论 + 法条拆解分析法
        system_prompt = f"""你是一位资深的刑事辩护律师，精通刑法学界通行的三阶层犯罪论体系。

{THEORY_THREE_TIERS}

---

**【法条构成要件拆解分析法 - 辩护分析核心方法】**

{CONSTITUTIVE_ELEMENT_ANALYSIS}

---

**【罪名特定知识】**

{crime_specific_knowledge if crime_specific_knowledge else "（未指定罪名，无特定知识加载）"}

---

作为辩护律师，你的职责是为被告人进行有效辩护。

**核心分析方法**：
1. 先拆解法条构成要件（主体、主观、客观、对象、结果等）
2. 针对每个要件提出问题
3. 用证据回答每个问题
4. 判断是否符合该要件
5. 全部符合→本罪，部分不符→无罪或他罪

**重要提示**：
- 你是辩护律师，分析应从有利于被告人的角度出发
- 善于提出问题，用证据验证每个构成要件
- 注重发现证据中的疑点和矛盾
- 依据《刑法》《刑事诉讼法》及相关司法解释
- 输出 Markdown 格式的专业分析报告"""

        # 构建证据部分
        evidence_section = "\n\n".join([
            f"### {e['filename']} ({e['type']})\n{e['text'][:_get_evidence_budget_chars()]}"  # 每份证据按证据预算截断
            for e in evidence_texts
        ])
        
        user_message = f"""## 辩护对象
被告人：**{defendant}**

## 案卷材料

{evidence_section}

---

## 分析要求

请按照以下结构输出 Markdown 格式的分析报告：

### 一、指控要素分析
基于起诉意见书提取核心指控要素：
- 罪名及法律依据
- **犯罪构成要件**：列出该罪名法条规定的各项构成要件（主体、行为、结果等），结合本案证据逐一说明
- 涉案金额/数量
- 涉案时间、地点
- 涉案人员（主从犯认定）
- 指控行为（作为/不作为）

注意：构成要件只需列出法条规定的内容并结合证据说明，不要使用"主体/主观/客观/客体"四要件框架进行理论分析。后续分析阶段会采用三阶层理论进行辩护。

### 二、证据内容概括
对每份证据的核心内容进行概括，简明扼要地说明每份证据证明的主要内容。

### 人物关系图
从笔录中提取涉案人员及其关系，输出为表格：

| 人物 | 角色 | 关联人物 | 关系类型 | 关系说明 |
|------|------|----------|----------|----------|

- 角色可选：犯罪嫌疑人、同案犯、被害人、证人、介绍人、中间人、其他
- 关系类型可选：同伙、上下级、介绍人、亲属、朋友、同事、交易方、其他
- 每个人都应与有直接关系的人建立一条记录，不要遗漏

### 三、证据内部差异分析（纵向对比）⭐ 重点
分析同一人多份讯问笔录/询问笔录之间的差异：
- 差异点具体内容（时间、金额、参与人、行为方式等）
- 差异形成原因（时间间隔、讯问环境、讯问策略、记忆变化、诱导因素等）
- 对证明力的影响（是否影响证据的可采信性）

### 四、证据间矛盾分析（横向对比）⭐ 重点
**不同证据对同一事实的记载是否矛盾：**

**（一）关键事实横向比对**
| 比对维度 | 被告人供述 | 证人证言 | 书证/物证 | 是否矛盾 |
|----------|-----------|---------|----------|----------|
| 作案时间 | ？ | ？ | ？ | ？ |
| 作案地点 | ？ | ？ | ？ | ？ |
| 参与人员 | ？ | ？ | ？ | ？ |
| 涉案金额/数量 | ？ | ？ | ？ | ？ |
| 行为方式 | ？ | ？ | ？ | ？ |

**（二）矛盾类型识别**
- **直接矛盾**：两份证据对同一事实的描述完全相反
- **间接矛盾**：一份证据的推论与另一份证据的事实冲突
- **隐性矛盾**：表面不矛盾，但逻辑上无法同时成立

**（三）重点比对组合**
- 被告人供述 vs 证人证言（是否相互印证）
- 证人证言 vs 书证/物证（客观证据是否支持言词证据）
- 同案犯供述之间（是否存在推诿或矛盾）
- 被害人陈述 vs 其他证据（是否存在夸大或虚假）

**（四）矛盾形成原因分析**
- 记忆偏差 vs 故意虚假陈述
- 观察角度不同 vs 事实本身矛盾
- 取证程序问题（诱导、胁迫、指供）

**（五）矛盾对证明力的影响**
- 核心事实矛盾 → 动摇指控基础，可能构成合理怀疑
- 细节矛盾 → 影响证据可信度
- 可合理解释的矛盾 → 不影响采信



**（六）各类证据审查重点**
- **物证**：来源与保管链条、关联性、同一性
- **书证**：真实性（原件/伪造）、提取程序、内容关联
- **证人证言**：证人资格、利害关系、取证合法性、内容客观性、出庭必要性
- **被害人陈述**：同证人证言，额外关注情绪化夸大、诉求影响
- **被告人供述和辩解**：合法性（刑讯逼供等）、稳定性与合理性、辩解是否被重视
- **鉴定意见**：资质与程序、方法与论证、结论明确性
- **勘验、检查、辨认、侦查实验笔录**：程序合法性、记载客观全面性、辨认规则遵守
- **视听资料、电子数据**：原始性与完整性、真实性（剪辑修改）、提取合法性

### 五、证据三性分析（参照刑事辩护提示词的质证意见格式）
对每份证据进行合法性、真实性、关联性综合评价：
- 合法性：取证程序是否合法，是否存在非法证据排除情形
- 真实性：证据内容是否真实可靠，是否存在矛盾
- 关联性：证据与待证事实的关联程度

### 六、辩护要点（基于三阶层理论 + 法条拆解分析法）⭐ 核心

**【核心方法：提出问题 → 套入法条 → 是否符合构成要件 → 本罪/无罪/他罪】**

**（一）人物关系对辩护的影响（优先分析）⭐**

基于上述人物关系图，分析以下方面：

1. **被告人在关系网络中的位置**
   - 被告人与各涉案人员的角色关系（主导/从属/被动/中间人）
   - 关系网络中是否存在真正的主谋或关键人物
   - 被告人是否被错误认定为核心角色

2. **共同犯罪中的地位认定**
   - 各涉案人员的实际作用和分工
   - 主犯与从犯的区分依据
   - 是否存在胁从犯、被教唆犯等减轻情节
   - 从犯辩护：地位越边缘、作用越次要，辩护空间越大

3. **关键关系的合理怀疑**
   - 证人/被害人与被告人是否存在利益冲突或利害关系
   - 证人证言是否存在因个人关系产生的偏颇
   - 同案犯供述是否存在推卸责任的动机
   - 介绍人/中间人是否承担了指控未认定的关键角色

4. **关系证据的薄弱环节**
   - 哪些关系仅凭言词证据认定，缺乏客观证据印证
   - 关系链条中是否存在断裂或无法印证的部分
   - 是否存在"中间人"角色模糊、责任不清的情况

**（二）构成要件拆解分析**

请针对指控罪名，逐项分析：

| 构成要件 | 法条要求 | 需要查明的问题 | 证据支撑 | 是否符合 |
|----------|----------|----------------|----------|----------|
| **行为主体** | ？ | ①是否要求特殊身份（国家工作人员、纳税人等）？②行为人是否具备该身份？③共同犯罪中各人身份如何认定？ | ？ | ？ |
| **行为** | ？ | ①实施了什么具体行为？②是作为还是不作为？③是否属于刑法禁止的行为类型？ | ？ | ？ |
| **对象** | ？ | ①行为针对什么？②是否属于刑法保护的特定对象（他人财物、公共安全等）？ | ？ | ？ |
| **结果** | ？ | ①是否造成法定危害结果（死亡、财产损失等）？②结果是否达到入罪标准？ | ？ | ？ |
| **因果关系** | ？ | ①行为与结果之间是否存在引起与被引起关系？②是否有介入因素中断因果关系？ | ？ | ？ |
| **故意/过失** | ？ | ①是故意还是过失？②是否明知？③是直接故意、间接故意、疏忽大意过失还是过于自信过失？ | ？ | ？ |
| **目的与动机** | ？ | ①是否具有特定犯罪目的（非法占有、勒索财物等）？②目的是否有证据支撑？ | ？ | ？ |

**（三）关键问题分析**

针对本案，重点回答以下问题：

1. **主体资格问题**
   - 刑法对本罪主体有何规定？
   - 各嫌疑人是否具有特定身份？
   - 共同犯罪中各人的地位和作用？

2. **行为定性问题**
   - 嫌疑人具体做了什么？
   - 行为是否符合法条描述的犯罪行为？
   - 是否存在正当化事由？

3. **对象与结果问题**
   - 侵害对象的性质是什么？
   - 是否符合法条对犯罪对象的规定？
   - 损害结果如何计算？

**（四）违法性层面辩护**
- 是否存在正当防卫、紧急避险等违法阻却事由？
- 是否存在被害人承诺？
- 是否存在执行命令、正当业务行为？

**（五）有责性层面辩护**
- 责任能力（刑事责任年龄、精神状态）
- 故意/过失的具体认定
- 期待可能性
- 量刑情节（自首、立功、从犯、未遂、中止、认罪认罚等）

**（六）结论**
- 哪些构成要件存在疑问？
- 是否可能构成其他罪名？
- 辩护方向建议（无罪/罪轻/改变定性）

### 七、结论与建议
综合以上分析，给出：
1. 辩护策略建议（无罪辩护/罪轻辩护/程序辩护方向）
2. 预期结果评估
3. 下一步工作建议（需要补充的证据、申请事项等）

请基于《中华人民共和国刑法》《刑事诉讼法》及相关司法解释，提供专业、准确的分析。
"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        return await self.chat(messages, model)
    
    async def update_report_section(
        self,
        update_instruction: str,
        original_report: str,
        evidence_context: str,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        智能更新报告 - 分析修改的影响范围，输出需要修改的所有部分
        
        Args:
            update_instruction: 用户修改指令
            original_report: 原报告（Markdown 格式）
            evidence_context: 相关证据材料
            model: 模型名称
        
        Returns:
            {
                "impact_analysis": str,           # 影响分析说明
                "affected_sections": str[],       # 受影响的章节列表
                "updates": [                      # 需要执行的更新列表
                    {
                        "action": "replace" | "insert" | "delete",
                        "target_section": str,
                        "new_content": str,
                        "position": str,
                        "reason": str
                    }
                ],
                "full_markdown": str              # 完整的新报告（备选）
            }
        """
        system_prompt = """你是一个专业的刑事辩护律师助手。正在智能更新辩护分析报告。

**核心原则**：
刑事案卷分析是系统工程，修改一个结论可能影响多个部分。

**任务流程**：
1. 分析用户修改指令涉及的核心问题
2. 识别原报告中受影响的章节（影响范围分析）
3. 基于原始证据，重新分析受影响的部分
4. 输出所有需要修改的内容

**输出格式**（JSON）：
{
    "impact_analysis": "说明修改涉及的核心问题及影响范围",
    "affected_sections": ["六、辩护要点", "七、结论与建议"],
    "updates": [
        {
            "action": "replace",
            "target_section": "六、辩护要点",
            "new_content": "...",
            "reason": "自首情节需要新增辩护要点"
        },
        {
            "action": "replace", 
            "target_section": "七、结论与建议",
            "new_content": "...",
            "reason": "辩护策略因自首情节需要调整"
        }
    ]
}

**重要**：
- 不要只修改表面章节，要分析深层影响
- 每个更新都必须基于原始证据重新分析
- 如果影响范围过大（超过 4 个章节），建议在 impact_analysis 中说明"""

        user_message = f"""## 用户修改指令

{update_instruction}

---

## 原报告内容

{original_report[:_get_report_budget_chars()]}

---

## 相关证据材料（供重新分析使用）

{evidence_context[:_get_evidence_budget_chars()]}

---

请分析修改的影响范围，输出所有需要更新的内容。基于原始证据重新分析，不要直接沿用原结论。"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

        response = await self.chat(messages, model)
        
        # 解析 JSON 响应
        try:
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return {
                "impact_analysis": "无法解析响应",
                "affected_sections": [],
                "updates": [{"action": "replace", "target_section": "未知", "new_content": response, "reason": "JSON 解析失败"}]
            }
        except Exception as e:
            return {
                "impact_analysis": f"解析失败：{e}",
                "affected_sections": [],
                "updates": [{"action": "replace", "target_section": "未知", "new_content": response, "reason": f"解析失败：{e}"}]
            }

    async def chat_about_case(
        self,
        question: str,
        evidence_context: str,
        report_context: Optional[str] = None,
        model: Optional[str] = None
    ) -> str:
        """
        基于案件证据进行对话问答
        
        重要：对话分析必须基于原始证据材料，不能引用分析报告的结论作为既定事实。
        每次对话都是独立的分析过程，需要重新审视证据。
        
        Args:
            question: 用户问题
            evidence_context: 原始证据材料（必需）
            report_context: 原报告内容（可选，仅在更新报告时使用）
            model: 模型名称
        """
        # 判断是否为更新报告场景
        is_update_mode = report_context is not None
        
        if is_update_mode:
            # 更新报告模式：基于原报告修改，但修改部分必须基于证据重新分析
            system_prompt = """你是一个专业的刑事辩护律师助手。正在更新辩护分析报告。

**重要原则**：
1. 这是报告更新任务，需要在原报告基础上进行修改
2. 对于用户要求修改/补充的部分，必须基于原始证据重新分析，不能直接沿用原结论
3. 保持原报告的整体结构和未修改部分的内容
4. 只修改用户要求的部分，其他部分保持不变
5. 输出完整的更新后报告（Markdown 格式）"""
            
            user_message = f"""## 原始案卷证据材料

{evidence_context[:_get_evidence_budget_chars()]}

---

## 原辩护分析报告（仅供参考，修改部分需重新分析）

{report_context[:_get_report_budget_chars()]}

---

## 用户要求

{question}

请根据用户要求更新报告。对于需要修改/补充的部分，请基于原始证据重新分析，不要直接引用原报告结论。
输出完整的更新后报告（Markdown 格式）。"""
        else:
            # 普通对话模式：独立分析，不引用报告
            system_prompt = """你是一个专业的刑事辩护律师助手。基于案件证据材料，回答用户的问题。

**重要原则**：
1. 分析必须基于原始证据材料，不能引用分析报告的结论作为既定事实
2. 每次对话都是独立的分析过程，需要重新审视证据
3. 如果用户询问分析结论，你应该基于证据重新推导，而不是引用之前的结论
4. 善于提出问题，用证据验证每个构成要件
5. 依据《刑法》《刑事诉讼法》及相关司法解释"""
            
            user_message = f"""## 案卷证据材料

{evidence_context[:_get_evidence_budget_chars()]}

---

## 用户问题

{question}

请基于上述证据材料，独立分析问题。不要引用任何分析报告的结论作为既定事实。"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        return await self.chat(messages, model)
    
    async def close(self):
        """关闭客户端"""
        await self.client.aclose()


# 全局客户端实例
_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取全局客户端实例"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


async def close_llm_client():
    """关闭全局客户端"""
    global _client
    if _client:
        await _client.close()
        _client = None