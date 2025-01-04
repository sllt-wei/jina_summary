import json
import os
import html
from urllib.parse import urlparse
import requests
from zhipuai import ZhipuAI
import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins import *

@plugins.register(
    name="junSummary",
    desire_priority=10,
    hidden=False,
    desc="Sum url link content with jina reader and llm",
    version="0.0.1",
    author="sllt",
)
class JinaSum(Plugin):

    jina_reader_base = "https://r.jina.ai"
    open_ai_api_base = "https://api.openai.com/v1"
    open_ai_model = "gpt-3.5-turbo"
    zhipu_api_base = "https://open.bigmodel.cn/api/paas/v4"  # 智谱默认API地址
    zhipu_model = "glm-4-flash"  # 默认智谱模型
    max_words = 8000
    prompt = "我需要对下面引号内文档进行总结，总结输出包括以下三个部分：\n📖 一句话总结\n🔑 关键要点,用数字序号列出3-5个文章的核心内容\n🏷 标签: #xx #xx\n请使用emoji让你的表达更生动\n\n"
    white_url_list = []
    black_url_list = [
        "https://support.weixin.qq.com",  # 视频号视频
        "https://channels-aladin.wxqcloud.qq.com",  # 视频号音乐
    ]

    def __init__(self):
        super().__init__()
        try:
            # 加载配置文件
            self.config = super().load_config()
            if not self.config:
                self.config = self._load_config_template()

            # 读取 OpenAI 和智谱 API 配置
            self.open_ai_api_key = self.config.get("open_ai_api_key", "")
            self.open_ai_model = self.config.get("open_ai_model", self.open_ai_model)
            self.zhipu_api_key = self.config.get("zhipu_api_key", "")
            self.zhipu_model = self.config.get("zhipu_model", self.zhipu_model)
            self.zhipu_api_base = self.config.get("zhipu_api_base", self.zhipu_api_base)
            self.max_words = self.config.get("max_words", self.max_words)
            self.prompt = self.config.get("prompt", self.prompt)
            self.white_url_list = self.config.get("white_url_list", self.white_url_list)
            self.black_url_list = self.config.get("black_url_list", self.black_url_list)

            # 加载智谱配置（直接从同一配置文件中加载）
            logger.info(f"[JinaSum] Initialized with config: {self.config}")
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        except Exception as e:
            logger.error(f"[JinaSum] Initialization error: {e}")
            raise "[JinaSum] init failed, ignoring"

    def on_handle_context(self, e_context: EventContext, retry_count: int = 0):
        try:
            context = e_context["context"]
            content = context.content
            if context.type != ContextType.SHARING and context.type != ContextType.TEXT:
                return
            if not self._check_url(content):
                logger.debug(f"[JinaSum] {content} is not a valid URL, skipping")
                return
            if retry_count == 0:
                logger.debug("[JinaSum] on_handle_context. Content: %s" % content)
                reply = Reply(ReplyType.TEXT, "🎉正在为您生成总结，请稍候...")
                channel = e_context["channel"]
                channel.send(reply, context)

            target_url = html.unescape(content)  # 处理HTML转义字符
            jina_url = self._get_jina_url(target_url)
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(jina_url, headers=headers, timeout=60)
            response.raise_for_status()
            target_url_content = response.text

            # 调用智谱生成总结
            result = self._get_zhipu_summary(target_url_content)
            reply = Reply(ReplyType.TEXT, result)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

        except Exception as e:
            if retry_count < 3:
                logger.warning(f"[JinaSum] {str(e)}, retrying {retry_count + 1}")
                self.on_handle_context(e_context, retry_count + 1)
                return

            logger.exception(f"[JinaSum] {str(e)}")
            reply = Reply(ReplyType.ERROR, "我暂时无法总结链接，请稍后再试")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

    def _get_zhipu_summary(self, target_url_content):
        """使用智谱模型生成内容总结"""
        if not self.zhipu_api_key:
            raise ValueError("[JinaSum] 未配置智谱API密钥")
        client = ZhipuAI(api_key=self.zhipu_api_key)
        target_url_content = target_url_content[:self.max_words]
        sum_prompt = f"{self.prompt}\n\n'''{target_url_content}'''"
        response = client.chat.completions.create(
            model=self.zhipu_model,
            messages=[
                {"role": "system", "content": "你是一个专业的内容总结助手，请按照用户的要求完成总结任务。"},
                {"role": "user", "content": sum_prompt},
            ],
        )
        return response.choices[0].message.content

    def get_help_text(self, verbose, **kwargs):
        return f"使用Jina Reader、OpenAI或智谱总结网页链接内容"

    def _get_jina_url(self, target_url):
        return self.jina_reader_base + "/" + target_url

    def _check_url(self, target_url: str):
        stripped_url = target_url.strip()
        if not stripped_url.startswith("http://") and not stripped_url.startswith("https://"):
            return False
        if len(self.white_url_list):
            if not any(stripped_url.startswith(white_url) for white_url in self.white_url_list):
                return False
        for black_url in self.black_url_list:
            if stripped_url.startswith(black_url):
                return False
        return True
