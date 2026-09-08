"""编解码插件：提供 codec 服务（HEX/文本/多编码 纯函数集合，无依赖）。"""

from core import codec


class Plugin:
    name = "codec"
    inject = []

    def apply(self, ctx):
        ctx.provide("codec", codec)
