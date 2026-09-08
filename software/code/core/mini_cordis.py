"""
mini_cordis.py - 一个极简的 Cordis 风格插件框架

相对旧版的主要改动（对齐原版 Cordis 的可逆副作用模型）：
1. provide/on/on_depend/effect 均返回 disposer（可逆资源释放函数），
   卸载时框架自动按逆序调用，不用手动在每个插件里记账。
2. 归属插件由框架自动追踪（加载时记录 current_plugin），
   插件代码不再需要手动写 ctx.effect("timer", ...) 这种 owner。
   旧签名 ctx.effect(owner, setup, teardown) 仍兼容。
3. teardown 支持异步（async def），卸载时会正确 await 清理。
4. 事件总线支持四种分发模式：emit（顺序观察、无返回值、错误隔离）、
   waterfall（环绕中间件，可短路）、serial（有序+返回值）、parallel（并行扇出）。
5. emit 做了错误隔离：单个监听器抛错不会阻断其他监听器。
6. Context 支持属性式服务访问（ctx.log / ctx.psu），并暴露上下文管理方法。
7. 新增 ctx.setTimeout/ctx.setInterval 计时助手，自动注册为可逆副作用，
   插件无需手写 running/task 样板。
"""

from __future__ import annotations

from typing import Dict, List, Any, Callable, Optional, Awaitable, Protocol
import asyncio
import importlib.util
import threading
import time
import warnings
from pathlib import Path

# 类型别名：一个可逆副作用。setup 先执行产生资源，teardown 负责撤销。
EffectFn = Callable[[], Any]
# 事件分发时"继续执行下游"的句柄。
NextFn = Callable[[Any], Awaitable[Any]]


# ---------- 1. 副作用（Effect） ----------
class Effect:
    """可逆副作用：记录一个操作及其撤销方法，支持异步 teardown。"""

    def __init__(self, owner: str, setup: EffectFn, teardown: EffectFn):
        self.owner = owner
        self._setup = setup
        self._teardown = teardown or (lambda: None)
        self._applied = False

    def apply(self):
        """执行 setup，仅执行一次。"""
        if not self._applied:
            self._applied = True
            self._setup()

    async def revert(self):
        """执行 teardown（若为协程则 await），仅执行一次。"""
        if not self._applied:
            return
        self._applied = False
        out = self._teardown()
        if asyncio.iscoroutine(out):
            await out


# ---------- 2. 上下文（Context） ----------
class Context:
    """插件运行环境：服务注册 + 副作用记录 + 事件总线。"""

    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._effects: List[Effect] = []
        self._event_listeners: Dict[str, List[Callable]] = {}
        self._current_plugin: Optional[str] = None
        self._current_config: Any = None
        # ---- 依赖关系追踪 ----
        self._dependencies: Dict[str, List[str]] = {}  # 插件名 -> 它 inject 的服务名
        self._dependents: Dict[str, List[str]] = {}  # 服务名 -> 依赖它的插件名列表
        self._service_owners: Dict[str, str] = {}  # 服务名 -> 提供者插件名
        self._provided: Dict[str, List[str]] = {}  # 插件名 -> 它提供的服务名列表
        self._depend_hooks: Dict[
            str, List[Callable[[str], None]]
        ] = {}  # 服务名 -> 回调

    # ---------- 实例身份 ----------
    @property
    def name(self) -> Optional[str]:
        """当前正在 apply 的插件的实例 key（None = 不在插件加载流程中）。"""
        return self._current_plugin

    @property
    def config(self) -> Any:
        """当前正在 apply 的插件的实例配置（由 load_plugin(..., config=...) 传入）。"""
        return self._current_config

    # ---------- 归属插件解析 ----------
    @property
    def _owner(self) -> str:
        return self._current_plugin or "unknown"

    # ---- 服务管理 ----
    def provide(self, name: str, service: Any) -> Callable[[], None]:
        """注册一个服务，返回卸载该服务的 disposer。"""
        owner = self._owner

        def setup():
            self._services[name] = service

        def teardown():
            self._services.pop(name, None)

        self._service_owners[name] = owner
        self._provided.setdefault(owner, []).append(name)
        return self.effect(setup, teardown)

    def get(self, name: str, strict: bool = True) -> Any:
        """读取服务。strict=True 时仅返回"提供方当前仍存活"的服务。"""
        if not strict:
            return self._services.get(name)
        owner = self._service_owners.get(name)
        if owner is not None and not self._is_owner_active(owner):
            return None
        return self._services.get(name)

    def _is_owner_active(self, owner: str) -> bool:
        """owner 当前是否存活（还有副作用，即尚未被卸载）。"""
        return any(e.owner == owner and e._applied for e in self._effects)

    def set(self, name: str, value: Any):
        """覆盖已提供服务的值；只允许提供方设置。"""
        owner = self._service_owners.get(name)
        if owner is None or owner != self._owner:
            raise RuntimeError(f"服务 {name} 不存在或不属于当前插件")
        self._services[name] = value

    def __getattr__(self, name: str):
        """属性式服务访问：ctx.log、ctx.psu。"""
        services = self.__dict__.get("_services")
        if services and name in services:
            return services[name]
        raise AttributeError(f"context has no service or attribute {name!r}")

    def mixin(self, service_name: str, keys: Optional[List[str]] = None):
        """把某个服务的可调用成员在 context 上暴露为转发方法，卸载时移除。"""
        svc = self.get(service_name)
        if svc is None:
            raise RuntimeError(f"服务 {service_name} 尚未就绪，无法 mixin")
        source = [k for k in dir(svc) if not k.startswith("_")]
        targets = (
            keys
            if keys is not None
            else [k for k in source if callable(getattr(svc, k))]
        )

        forwarders: List[tuple] = []
        for k in targets:
            attr = getattr(svc, k)
            if not callable(attr):
                continue

            def make(key):
                def forward(*args, **kwargs):
                    return getattr(self.get(service_name), key)(*args, **kwargs)

                return forward

            forwarders.append((k, make(k)))

        def apply_mixins():
            for k, bound in forwarders:
                setattr(self, k, bound)

        def remove_mixins():
            for k, _ in forwarders:
                if k in self.__dict__:
                    del self.__dict__[k]

        return self.effect(apply_mixins, remove_mixins)

    # ---- 依赖关系查询 ----（与旧版保持一致）
    def get_dependents(self, service_name: str) -> List[str]:
        return list(self._dependents.get(service_name, []))

    def get_dependencies(self, plugin_name: str) -> List[str]:
        return list(self._dependencies.get(plugin_name, []))

    def get_owner(self, service_name: str) -> Optional[str]:
        return self._service_owners.get(service_name)

    def who_uses_me(self, plugin_name: str) -> set[str]:
        users: set[str] = set()
        for svc in self._provided.get(plugin_name, []):
            users.update(self._dependents.get(svc, []))
        return users

    # ---- 副作用注册（核心） ----（签名 4 种）
    def effect(self, *args) -> Callable[[], None]:
        """注册一个可逆副作用，返回 disposer。

        支持三种调用方式：
          ctx.effect(setup, teardown)              # 归属当前插件
          ctx.effect(owner, setup, teardown)       # 显式归属（旧签名）
          ctx.effect(setup)                        # teardown 缺省为 no-op
        """
        if len(args) == 3:
            owner, setup, teardown = args
        elif len(args) == 2:
            setup, teardown = args
            owner = self._owner
        elif len(args) == 1:
            (setup,) = args
            teardown = None
            owner = self._owner
        else:
            raise TypeError("effect() 接受 1~3 个参数")

        eff = Effect(owner, setup, teardown)
        eff.apply()
        self._effects.append(eff)

        def disposer():
            self._detach_effect(eff)

        return disposer

    def _detach_effect(self, eff: Effect):
        """移除单个副作用并执行 teardown（同步优先，异步则排入事件循环）。"""
        if eff in self._effects:
            self._effects.remove(eff)
        out = eff._teardown()
        eff._applied = False
        if asyncio.iscoroutine(out):
            self._run_coroutine(out)

    def _run_coroutine(self, coro):
        """在运行中的事件循环上调度一个协程；无循环则告警。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            warnings.warn("detached an async teardown but no event loop is running")
            return
        loop.create_task(coro)

    # ---- 事件总线 ----
    def on(self, event: str, handler: Callable) -> Callable[[], None]:
        """注册事件监听（作为副作用），返回解除监听的 disposer。"""
        owner = self._owner

        def setup():
            self._event_listeners.setdefault(event, []).append(handler)

        def teardown():
            listeners = self._event_listeners.get(event, [])
            if handler in listeners:
                listeners.remove(handler)

        return self.effect(setup, teardown)

    def on_depend(
        self, service_name: str, hook: Callable[[str], None]
    ) -> Callable[[], None]:
        """每当有插件开始依赖 service_name 时，调用 hook(依赖者插件名)。"""
        owner = self._owner

        def setup():
            self._depend_hooks.setdefault(service_name, []).append(hook)

        def teardown():
            hooks = self._depend_hooks.get(service_name, [])
            if hook in hooks:
                hooks.remove(hook)

        return self.effect(setup, teardown)

    async def emit(self, event: str, data: Any = None) -> None:
        """顺序观察，监听器按注册顺序执行；单个监听器出错不影响其他（错误隔离）。"""
        for handler in list(self._event_listeners.get(event, [])):
            try:
                await self._safe_call(handler, data)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._report_error(event, handler, e)

    def _safe_call(self, handler: Callable, data: Any) -> Awaitable:
        out = handler(data)
        if asyncio.iscoroutine(out):
            return out

        async def _noop():
            return out

        return _noop()

    async def waterfall(self, event: str, data: Any = None) -> Any:
        """环绕中间件：监听器接收 (data, next)；调用 next 继续下游，否则短路。

        每个监听器的返回值会向上游传递；若监听器返回 (await next(x)) 的包装值，
        上游看到的就是包装后的结果。不调用 next 则短路。
        """
        handlers = list(self._event_listeners.get(event, []))

        async def run(i: int, value: Any) -> Any:
            if i >= len(handlers):
                return value
            handler = handlers[i]

            async def next_(v):
                return await run(i + 1, v)

            out = handler(value, next_)
            if asyncio.iscoroutine(out):
                out = await out
            return out

        return await run(0, data)

    async def serial(self, event: str, data: Any = None) -> Any:
        """按注册顺序执行，每个监听器的返回值传给下一个。"""
        value = data
        for handler in list(self._event_listeners.get(event, [])):
            try:
                value = await self._safe_call(handler, value)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._report_error(event, handler, e)
        return value

    async def parallel(self, event: str, data: Any = None) -> None:
        """所有监听器并行观察事件，等待全部完成（错误隔离）。"""
        handlers = list(self._event_listeners.get(event, []))
        results = await asyncio.gather(
            *(self._safe_call(h, data) for h in handlers),
            return_exceptions=True,
        )
        for handler, result in zip(handlers, results):
            if isinstance(result, Exception) and not isinstance(
                result, asyncio.CancelledError
            ):
                self._report_error(event, handler, result)

    @staticmethod
    def _report_error(event: str, handler, error: Exception):
        print(f"[error] 事件监听器 {event} 抛错: {error!r}")

    # ---- 计时助手 ----
    def setTimeout(
        self, delay: float, callback: Callable[[], Any]
    ) -> Callable[[], None]:
        """延时调用 callback；作为副作用注册，卸载时自动取消。"""
        loop = asyncio.get_running_loop()
        handle = {"cancelled": False}

        async def run():
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            if not handle["cancelled"]:
                try:
                    out = callback()
                    if asyncio.iscoroutine(out):
                        await out
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._report_error("setTimeout", callback, e)

        def setup():
            loop.create_task(run())

        def teardown():
            handle["cancelled"] = True

        return self.effect(setup, teardown)

    def setInterval(
        self, delay: float, callback: Callable[[], Any]
    ) -> Callable[[], None]:
        """每隔 delay 秒调用 callback（无参数）；作为副作用注册，卸载时自动取消。"""
        loop = asyncio.get_running_loop()
        handle = {"cancelled": False}

        async def run():
            while not handle["cancelled"]:
                await asyncio.sleep(delay)
                if handle["cancelled"]:
                    break
                try:
                    out = callback()
                    if asyncio.iscoroutine(out):
                        await out
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self._report_error("setInterval", callback, e)

        def setup():
            loop.create_task(run())

        def teardown():
            handle["cancelled"] = True

        return self.effect(setup, teardown)

    # ---- 卸载插件 ----
    def _effects_for(self, owner: str) -> List[Effect]:
        return [e for e in self._effects if e.owner == owner]

    def revert_effects_for(self, owner: str) -> None:
        """撤销指定插件的所有副作用（同步路径；异步 teardown 会排入事件循环）。"""
        pending = []
        remaining = []
        for eff in self._effects:
            if eff.owner == owner:
                out = eff._teardown()
                eff._applied = False
                if asyncio.iscoroutine(out):
                    pending.append(out)
            else:
                remaining.append(eff)
        self._effects = remaining
        for coro in pending:
            self._run_coroutine(coro)

    # 提供异步卸载的接口（若插件使用了异步 teardown，建议调用它）
    async def dispose(self, owner: str) -> None:
        """异步撤销指定插件的全部副作用（会 await 异步 teardown）。"""
        remaining = []
        for eff in self._effects:
            if eff.owner == owner:
                await eff.revert()
            else:
                remaining.append(eff)
        self._effects = remaining


# ---------- 3. 插件协议（Protocol） ----------
class Plugin(Protocol):
    name: str
    inject: List[str]

    def apply(self, ctx: Context): ...


def validate_plugin(plugin: Plugin) -> None:
    """校验插件是否符合规范，不符合抛 ValueError。"""
    name = getattr(plugin, "name", None)
    if not isinstance(name, str) or not name:
        raise ValueError("插件缺少有效的 name（非空字符串）")

    inject = getattr(plugin, "inject", None) or []
    if not isinstance(inject, list) or not all(isinstance(d, str) for d in inject):
        raise ValueError(f"插件 {name} 的 inject 必须是字符串列表")

    if not callable(getattr(plugin, "apply", None)):
        raise ValueError(f"插件 {name} 缺少可调用的 apply 方法")


def scan_plugins(directory: Path, skip_private: bool = True) -> List[Any]:
    """扫描目录，动态导入所有插件模块，返回插件实例列表。

    约定：每个模块暴露一个名为 Plugin 的类，且通过 validate_plugin 校验。
    单个插件加载失败会被跳过并打印警告，不影响其他插件。
    """
    plugins = []
    for path in sorted(directory.glob("*.py")):
        if skip_private and path.name.startswith("_"):
            continue
        try:
            module_name = path.stem
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"无法为 {path} 创建模块规格")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            plugin = module.Plugin()
            validate_plugin(plugin)
            plugins.append(plugin)
        except Exception as e:
            print(f"[warn] 加载插件失败 {path.name}: {e}")
    return plugins


# ---------- 4. 核心加载器 ----------
class Cordis:
    def __init__(self):
        self.ctx = Context()
        self._loaded: List[str] = []  # 记录加载顺序，用于逆序卸载

    def load_plugin(self, plugin: Plugin, key: Optional[str] = None, config: Any = None):
        """加载插件：校验规范 -> 检查依赖 -> 记录依赖关系 -> 执行 apply。

        key：实例标识，缺省为 plugin.name。同一插件定义要加载多个实例时，
        必须传入不同的 key（如 "dji.0"、"dji.1"）。插件内可用 ctx.name 读取。
        config：该实例的私有配置，插件内用 ctx.config 读取。
        """
        validate_plugin(plugin)
        key = key or plugin.name

        for dep in plugin.inject:
            if self.ctx.get(dep, strict=False) is None:
                raise RuntimeError(f"缺少依赖服务: {dep}")

        if key in self.ctx._dependencies:
            raise RuntimeError(
                f"插件已加载: {key}（同一插件定义多实例请传 key=...）"
            )

        # 正向：记录该插件依赖了哪些服务
        self.ctx._dependencies[key] = list(plugin.inject)

        # 反向：记录"谁依赖了这个服务"，并通知已注册的监听者
        for dep in plugin.inject:
            self.ctx._dependents.setdefault(dep, []).append(key)
            for hook in self.ctx._depend_hooks.get(dep, []):
                hook(key)

        # 将副作用归属到当前插件实例，执行 apply
        self.ctx._current_plugin = key
        self.ctx._current_config = config
        try:
            out = plugin.apply(self.ctx)
            if asyncio.iscoroutine(out):
                # 允许插件 apply 是 async 的，但加载过程为同步调用时标记警告
                warnings.warn(
                    f"插件 {key} 的 apply 是异步的，加载过程不会等待它完成"
                )
        finally:
            self.ctx._current_plugin = None
            self.ctx._current_config = None

        self._loaded.append(key)

    async def load_plugin_async(self, plugin: Plugin, key=None, config=None):
        """异步加载插件，支持 async 的 apply（key/config 语义同 load_plugin）。"""
        validate_plugin(plugin)
        key = key or plugin.name
        ctx = self.ctx
        for dep in plugin.inject:
            if ctx.get(dep, strict=False) is None:
                raise RuntimeError(f"缺少依赖服务: {dep}")
        if key in ctx._dependencies:
            raise RuntimeError(
                f"插件已加载: {key}（同一插件定义多实例请传 key=...）"
            )

        ctx._dependencies[key] = list(plugin.inject)
        for dep in plugin.inject:
            ctx._dependents.setdefault(dep, []).append(key)
            for hook in ctx._depend_hooks.get(dep, []):
                hook(key)

        ctx._current_plugin = key
        ctx._current_config = config
        try:
            out = plugin.apply(ctx)
            if asyncio.iscoroutine(out):
                await out
        finally:
            ctx._current_plugin = None
            ctx._current_config = None
        self._loaded.append(key)

    def unload_plugin(self, plugin_name: str):
        """卸载插件及其所有（传递）依赖者，按逆序逐个撤销。"""
        affected = self._transitive_dependents(plugin_name) | {plugin_name}
        for name in reversed(list(self._loaded)):
            if name in affected:
                self._unload_one(name)

    def _transitive_dependents(self, plugin_name: str) -> set[str]:
        result: set[str] = set()
        stack = list(self.ctx.who_uses_me(plugin_name))
        while stack:
            dep = stack.pop()
            if dep in result:
                continue
            result.add(dep)
            stack.extend(self.ctx.who_uses_me(dep))
        return result

    def _unload_one(self, plugin_name: str):
        """卸载单个插件（不级联），并清理依赖图。"""
        self.ctx.revert_effects_for(plugin_name)

        self.ctx._dependencies.pop(plugin_name, None)

        for svc in self.ctx._provided.pop(plugin_name, []):
            self.ctx._service_owners.pop(svc, None)
            self.ctx._dependents.pop(svc, None)

        for deps in self.ctx._dependents.values():
            if plugin_name in deps:
                deps.remove(plugin_name)

        if plugin_name in self._loaded:
            self._loaded.remove(plugin_name)

    async def unload_plugin_async(self, plugin_name: str):
        """异步卸载插件及其依赖者（会 await 异步 teardown）。"""
        affected = self._transitive_dependents(plugin_name) | {plugin_name}
        for name in reversed(list(self._loaded)):
            if name in affected:
                await self._unload_one_async(name)

    async def _unload_one_async(self, plugin_name: str):
        await self.ctx.dispose(plugin_name)
        self.ctx._dependencies.pop(plugin_name, None)
        for svc in self.ctx._provided.pop(plugin_name, []):
            self.ctx._service_owners.pop(svc, None)
            self.ctx._dependents.pop(svc, None)
        for deps in self.ctx._dependents.values():
            if plugin_name in deps:
                deps.remove(plugin_name)
        if plugin_name in self._loaded:
            self._loaded.remove(plugin_name)

    def load_all(self, plugins: List[Plugin], keys: Optional[List[str]] = None):
        """批量加载：自动按依赖顺序（被依赖者先加载）。

        keys：与 plugins 等长的实例 key 列表，缺省时使用各插件 name。
        """
        if keys is None:
            keys = [p.name for p in plugins]
        else:
            keys = list(keys)
        if len(keys) != len(plugins):
            raise ValueError("keys 的长度必须与 plugins 一致")

        remaining = list(zip(plugins, keys))
        while remaining:
            progress = False
            for idx, (plugin, key) in enumerate(remaining):
                if any(
                    self.ctx.get(dep, strict=False) is None for dep in plugin.inject
                ):
                    continue
                self.load_plugin(plugin, key=key)
                remaining.pop(idx)
                progress = True
                break
            if not progress:
                unresolved = ", ".join(
                    f"{p.name}(缺 {[d for d in p.inject if self.ctx.get(d, strict=False) is None]})"
                    for p, _ in remaining
                )
                raise RuntimeError(
                    f"无法确定加载顺序（循环依赖或缺少依赖）: {unresolved}"
                )

    def load_dir(self, directory: Path, *, exclude=()) -> List[str]:
        """扫描并加载目录下的所有插件（依赖自动排序），返回已加载的插件名列表。

        exclude：要跳过的插件名集合（例如已单独加载的 UI 叶子插件）。
        """
        plugins = scan_plugins(directory)
        plugins = [p for p in plugins if p.name not in exclude]
        names = {p.name for p in plugins}
        self.load_all(plugins)
        # 按真实加载顺序返回
        return [n for n in self._loaded if n in names]

    def unload_all(self):
        """批量卸载：按加载顺序的逆序（后加载的先卸载）。"""
        while self._loaded:
            self._unload_one(self._loaded[-1])
