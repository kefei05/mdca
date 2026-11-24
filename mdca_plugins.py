#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MDCA 插件层：把「自己造数」的能力做成可选插件
==============================================

背景：

    A 路（主线）审计器不产生数据，只判断数据能不能用。
    B 路（插件）自己算温度，需要独立热阻与实测验证，默认关闭。

本模块只管三件事，**不含任何物理逻辑，也不 import 任何业务模块**（避免循环依赖）：

    ① 注册   哪些模块算插件、属于哪条路、默认开还是关
    ② 解析   一次审计里到底开哪几个
    ③ 加载   延迟 import；插件缺失或抛异常时不拖垮 A 路

开关优先级（高 → 低）：

    1. CLI        `--plugins thermal,scaling`
    2. case JSON   `"plugins": {"thermal": true}`  或  `"plugins": ["thermal"]`
    3. 环境变量    `MDCA_PLUGINS=thermal,scaling`
    4. 默认        B 路插件一律关闭

插件的返回约定（`run(case, opts) -> dict`）：

    {
      "ok": bool,                 # 插件自身是否跑成功（与审计结论无关）
      "reason": str,              # ok=False 时的原因
      "answer": dict | None,      # 业务结果，结构由插件定义
      "issues": [ {code, severity, message, params, detail}, ... ],
    }

`run_plugin()` 永不抛异常：加载失败、`run()` 抛错都折算成 `ok=False`。
「插件被显式要求却加载失败」要不要拦，由调用方决定（mdca.py 记 BLOCK，
因为用户要的结论拿不到，静默降级到 A 路是最危险的失败方式）。
"""

from __future__ import annotations

import importlib
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

ENV_VAR = "MDCA_PLUGINS"


class PluginError(Exception):
    """插件层可预期的错误（未注册、名字拼错、字段类型不对）。"""


@dataclass(frozen=True)
class PluginSpec:
    name: str
    road: str            # "A"（审计，核心） / "B"（造数，可选）
    module: str
    summary: str
    default_enabled: bool = False


REGISTRY: Dict[str, PluginSpec] = {
    "thermal": PluginSpec(
        name="thermal", road="B", module="mdca_plugin_thermal",
        summary="两热阻/三节点温度计算（含区间与压力测试）"),
    "scaling": PluginSpec(
        name="scaling", road="B", module="mdca_plugin_scaling",
        summary="几何缩放律 → Rws/Rsa/Rrs（含外推检查）"),
}

DEFAULT_PLUGINS: Tuple[str, ...] = tuple(
    name for name, spec in REGISTRY.items() if spec.default_enabled
)

_LOADED: Dict[str, Any] = {}


# --------------------------------------------------------------------------
# 注册表查询
# --------------------------------------------------------------------------


def all_names() -> List[str]:
    return sorted(REGISTRY)


def names_for_road(road: str) -> List[str]:
    return sorted(n for n, s in REGISTRY.items() if s.road == road.upper())


def describe() -> List[Dict[str, Any]]:
    return [
        {"name": s.name, "road": s.road, "module": s.module,
         "summary": s.summary,
         "default_enabled": s.default_enabled or s.name in DEFAULT_PLUGINS}
        for s in sorted(REGISTRY.values(), key=lambda x: x.name)
    ]


def spec(name: str) -> PluginSpec:
    s = REGISTRY.get(str(name))
    if s is None:
        raise PluginError(
            f"未注册的插件 {name!r}；可用：{', '.join(all_names()) or '(无)'}")
    return s


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------


def parse_request(text: Any) -> List[str]:
    """把 `"thermal,scaling"` / `"all"` / `""` / 列表 解析成插件名列表。

    显式列出未知名字抛 PluginError——拼错的插件名静默失效，是本项目最忌讳的失败方式。

    `""` 与 `"none"` 都表示「显式关闭全部」，与「不传」不同：
    不传 ⇒ 落到下一级优先级；传空串 ⇒ 就地定为「一个都不开」。
    """
    if text is None:
        raise PluginError("parse_request 收到 None；要表示「不指定」请不要传参")
    if isinstance(text, (list, tuple, set)):
        items = [str(x).strip() for x in text]
    else:
        raw = str(text).strip().lower()
        if raw in ("", "none", "off"):
            return []
        if raw in ("all", "*"):
            return all_names()
        items = [p.strip() for p in raw.replace(";", ",").split(",")]

    out: List[str] = []
    for p in items:
        if not p:
            continue
        if p in ("all", "*"):
            return all_names()
        if p in ("none", "off"):
            continue
        spec(p)  # 校验名字
        if p not in out:
            out.append(p)
    return out


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(v)


def from_case(case: Any) -> Optional[List[str]]:
    """从 case 对象或原始 dict 里读 `plugins` 声明；没声明返回 None。"""
    raw = getattr(case, "plugins", None)
    if raw is None and isinstance(case, Mapping):
        raw = case.get("plugins")
    if raw is None:
        return None
    if isinstance(raw, str):
        return parse_request(raw)
    if isinstance(raw, Mapping):
        # {"thermal": true, "scaling": false} —— 只取为真的那几个
        return [str(k).strip() for k, v in raw.items() if _truthy(v)]
    if isinstance(raw, (list, tuple, set)):
        return parse_request(list(raw))
    raise PluginError(f"plugins 字段类型不支持：{type(raw).__name__}")


def from_env() -> Optional[List[str]]:
    raw = os.environ.get(ENV_VAR)
    if raw is None:
        return None
    return parse_request(raw)


def split_plugin_args(argv: Sequence[str]) -> Tuple[List[str], Optional[str]]:
    """从命令行参数里剥出 --plugins / --plugins=x，其余顺序不变。

    mdca.py 与 mdca_review.py 原先各写一份，逻辑一旦不同步，同一个
    `--plugins` 在两个入口的行为就会不一致。统一放这里。
    """
    out: List[str] = []
    plugins: Optional[str] = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--plugins":
            if i + 1 >= len(argv):
                raise PluginError("--plugins 需要一个参数（如 thermal,scaling 或 none）")
            plugins = argv[i + 1]
            i += 2
            continue
        if a.startswith("--plugins="):
            plugins = a.split("=", 1)[1]
            i += 1
            continue
        out.append(a)
        i += 1
    return out, plugins


def resolve(explicit: Any = None, case: Any = None) -> Tuple[List[str], str]:
    """按 4 级优先级定出本次审计启用的插件。

    返回 (启用列表, 来源)，来源取值 `cli` / `case` / `env` / `default`。
    """
    if explicit is not None:
        return parse_request(explicit), "cli"
    got = from_case(case)
    if got is not None:
        return got, "case"
    got = from_env()
    if got is not None:
        return got, "env"
    return list(DEFAULT_PLUGINS), "default"


# --------------------------------------------------------------------------
# 加载与运行
# --------------------------------------------------------------------------


def load(name: str) -> Any:
    s = spec(name)
    if name in _LOADED:
        return _LOADED[name]
    try:
        mod = importlib.import_module(s.module)
    except Exception as e:  # noqa: BLE001 —— 任何导入失败都要能降级
        raise PluginError(
            f"插件 {name} 的可选模块 {s.module} 导入失败：{type(e).__name__}: {e}"
        ) from e
    if not callable(getattr(mod, "run", None)):
        raise PluginError(f"插件 {name} 的模块 {s.module} 未提供 run(case, opts)")
    _LOADED[name] = mod
    return mod


def preload(names: Sequence[str]) -> Dict[str, Optional[str]]:
    """预检：返回 {插件名: 错误信息或 None}，不执行计算。"""
    out: Dict[str, Optional[str]] = {}
    for n in names:
        try:
            load(n)
            out[n] = None
        except PluginError as e:
            out[n] = str(e)
    return out


def run(name: str, case: Any, opts: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """统一入口。永不抛异常；失败以 ok=False + reason 返回。"""
    try:
        mod = load(name)
    except PluginError as e:
        return {"name": name, "ok": False, "reason": str(e), "issues": []}

    try:
        out = mod.run(case, dict(opts or {}))
    except Exception as e:  # noqa: BLE001
        return {
            "name": name, "ok": False,
            "reason": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc()[-1500:],
            "issues": [],
        }
    if not isinstance(out, dict):
        return {"name": name, "ok": False,
                "reason": f"run() 返回了 {type(out).__name__}，应为 dict", "issues": []}
    out.setdefault("ok", True)
    out.setdefault("issues", [])
    out["name"] = name
    return out


def reset_cache() -> None:
    """测试用：清掉模块缓存，模拟「插件缺失」。"""
    _LOADED.clear()


# --------------------------------------------------------------------------
# 自测（纯开关逻辑，不碰物理）
# --------------------------------------------------------------------------


def selftest() -> bool:
    ok = True

    def check(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {label:<44}{'PASS' if good else 'FAIL'}"
              + (f"  {detail}" if detail and not good else ""))
        ok = ok and good

    print("=== 插件层自测 ===")

    check("默认关闭：DEFAULT_PLUGINS 为空", DEFAULT_PLUGINS == (),
          f"实际 {DEFAULT_PLUGINS}")

    check("注册表两支都是 B 路",
          sorted(s.road for s in REGISTRY.values()) == ["B", "B"])

    check("parse 'thermal,scaling' 保序", parse_request("thermal,scaling") == ["thermal", "scaling"])
    check("parse 'all' 展开全部", parse_request("all") == all_names())
    check("parse '' 显式全关", parse_request("") == [])
    check("parse 去重", parse_request("thermal,thermal") == ["thermal"])

    try:
        parse_request("thermal,bogus")
        check("未知插件名必须报错", False, "居然没抛")
    except PluginError:
        check("未知插件名必须报错", True)

    check("case 列表形式", from_case({"plugins": ["thermal"]}) == ["thermal"])
    check("case 对象形式只取为真", from_case({"plugins": {"thermal": True, "scaling": False}}) == ["thermal"])
    check("case 未声明 → None", from_case({"case_id": "x"}) is None)

    # 优先级
    check("CLI 优先于 case",
          resolve("scaling", {"plugins": ["thermal"]}) == (["scaling"], "cli"))
    check("无 CLI 时用 case",
          resolve(None, {"plugins": ["thermal"]}) == (["thermal"], "case"))
    old = os.environ.pop(ENV_VAR, None)
    os.environ[ENV_VAR] = "thermal"
    try:
        check("无 CLI/case 时用环境变量", resolve(None, {}) == (["thermal"], "env"))
        os.environ.pop(ENV_VAR, None)
        check("都没有时用默认（全关）", resolve(None, {}) == ([], "default"))
    finally:
        os.environ.pop(ENV_VAR, None)
        if old is not None:
            os.environ[ENV_VAR] = old

    # 加载失败不抛
    bad = run("thermal-not-registered", {})
    check("未注册插件 run() 不抛异常", bad["ok"] is False and "未注册" in bad["reason"])

    print(f"自测结果：{'全部通过' if ok else '有失败项'}")
    return ok


def main(argv: List[str]) -> int:
    if not argv or argv[0] == "selftest":
        return 0 if selftest() else 1
    if argv[0] == "list":
        for d in describe():
            print(f"  {d['name']:<10}{d['road']} 路  默认{'开' if d['default_enabled'] else '关'}"
                  f"  {d['module']:<26}{d['summary']}")
        return 0
    if argv[0] == "resolve":
        try:
            names, src = resolve(argv[1] if len(argv) > 1 else None)
        except PluginError as e:
            print(f"错误：{e}")
            return 2
        print(f"启用：{names or '(无)'}   来源：{src}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
