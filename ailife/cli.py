"""命令行工具：第一次配置向导、人设管理。

    python run.py setup                         第一次用：一步步填好 Telegram、大模型 key、代理、选人设
    python run.py persona list|check|use|new|import ...   见 ailife/personas.py
"""
from __future__ import annotations

import asyncio
import re
import socket
import sys
from pathlib import Path

from .config import ROOT


def ask(q: str, default: str = "", secret: bool = False) -> str:
    hint = f"（直接回车＝{default}）" if default and not secret else ""
    try:
        if secret:
            import getpass
            v = getpass.getpass(f"{q}{hint}：")
        else:
            v = input(f"{q}{hint}：")
    except EOFError:
        v = ""
    return v.strip() or default


def yes(q: str, default: bool = True) -> bool:
    v = ask(q + ("（Y/n）" if default else "（y/N）")).lower()
    return default if not v else v.startswith("y") or v in ("是", "好", "要")


def run_coro(coro):
    """在命令行里跑一个协程；已经在事件循环里（比如启动时进的配置向导）就放到新线程里跑。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def detect_proxy() -> str:
    for port, kind in ((7890, "http"), (7897, "http"), (10809, "http"), (7891, "socks5"), (10808, "socks5"),
                       (1080, "socks5")):
        if _port_open(port):
            return f"{kind}://127.0.0.1:{port}"
    return ""


def _set_line(text: str, pattern: str, new: str) -> str:
    out, n = re.subn(pattern, new, text, count=1, flags=re.M)
    return out if n else text


# ================= 配置向导 =================
def setup(root: Path = ROOT):
    from . import secrets
    print("\n=== ai-life 配置向导 ===")
    print("会生成 config.yaml（设置）和 .env（密钥，别发给别人）。之后都可以手动改。\n")
    cfg_path = root / "config.yaml"
    if cfg_path.exists() and not yes("已经有 config.yaml 了，要重新配置吗", False):
        print("好，没改。")
        return
    text = (root / "config.example.yaml").read_text(encoding="utf-8")
    env: dict[str, str] = {}

    print("① 在哪里和她聊？")
    print("  1. QQ（推荐，国内不用代理；会自动下载、配置 NapCat，你只要用她的 QQ 小号扫一次码）")
    print("  2. Telegram（要代理）")
    print("  3. 两个都要")
    where = ask("选哪个", "1" if sys.platform == "win32" else "2")
    use_qq = where in ("1", "3")
    use_tg = where in ("2", "3")
    if use_tg:
        print("\n  Telegram：在 Telegram 里找 @BotFather，发 /newbot 创建一个 bot，把它给你的 token 粘贴过来。")
        while True:
            tok = ask("bot token", secret=False)
            if re.fullmatch(r"\d{6,12}:[\w-]{30,}", tok):
                break
            print("  看起来不对（格式像 123456789:AAH...），再试一次")
        env["TELEGRAM_BOT_TOKEN"] = tok
        text = _set_line(text, r'^(\s*bot_token:\s*).*$', r'\1"${TELEGRAM_BOT_TOKEN}"')
    else:
        text = _set_line(text, r'^(\s*bot_token:\s*).*$', r'\1""   # 只用 QQ；想加 Telegram 就重新运行 setup')

    print("\n② 代理：国内访问 Telegram 需要代理（Clash 一般是 http://127.0.0.1:7890）。只用 QQ 可以不要。")
    found = detect_proxy()
    if found:
        print(f"  检测到本机有代理：{found}")
    proxy = ask("代理地址（不需要就输入 无）", found or ("http://127.0.0.1:7890" if use_tg else "无"))
    proxy = "" if proxy in ("无", "no", "none", "-") else proxy
    text = _set_line(text, r'^proxy:.*$', f'proxy: "{proxy}"')

    print("\n③ 大模型（都有免费额度）：")
    print("  智谱 open.bigmodel.cn → API Keys（聊天主力，免费）")
    z = ask("智谱 API Key（没有就回车跳过）", secret=True)
    print("  硅基流动 cloud.siliconflow.cn → API 密钥（备用聊天 + 记忆检索，免费）")
    sf = ask("硅基流动 API Key（没有就回车跳过）", secret=True)
    print("  也可以用别的接口（DeepSeek、通义、Kimi、OpenRouter、本机 Ollama……任何 OpenAI 兼容的）")
    own_llm = yes("  要加一个自己的大模型接口吗（填地址和 Key，自动测连通、列出模型让你选）", False)
    if not z and not sf and not own_llm:
        print("  ⚠️ 一个都没填：她没法说话。之后在 .env 里补上 ZHIPU_KEY 或 SILICONFLOW_KEY，或者双击 llm.bat 加接口。")
    if z:
        env["ZHIPU_KEY"] = z
        text = _set_line(text, r'^(\s*zhipu:\s*)"".*$', r'\1"${ZHIPU_KEY}"')
    if sf:
        env["SILICONFLOW_KEY"] = sf
        text = _set_line(text, r'^(\s*siliconflow:\s*)"".*$', r'\1"${SILICONFLOW_KEY}"')

    print("\n④ 你在哪个城市（她会关心你那边的天气，可以跳过）")
    city = ask("城市", "")
    if city:
        text = _set_line(text, r'^(\s*city:\s*)"".*$', rf'\1"{city}"')

    print("\n⑤ 选一个人设（以后随时能换：python run.py persona use 文件）")
    from .personas import list_personas
    from .config import Cfg
    tmp_cfg = Cfg({"root": root, "persona_file": "persona/linxi.md"})
    ps = [p for p in list_personas(tmp_cfg) if not p.get("template")]
    for i, p in enumerate(ps, 1):
        print(f"  {i}. {p['name']}（{p.get('identity') or '—'}）  {p['file']}")
    print(f"  {len(ps) + 1}. 自己创建一个新的（回答几个问题）")
    print(f"  {len(ps) + 2}. 导入 SillyTavern 角色卡")
    pick = ask("选哪个", "1")
    persona_file = ps[0]["file"] if ps else "persona/linxi.md"
    secrets.write_env(root, env)
    cfg_path.write_text(text, encoding="utf-8")
    if pick.isdigit() and 1 <= int(pick) <= len(ps):
        persona_file = ps[int(pick) - 1]["file"]
    elif pick == str(len(ps) + 1):
        persona_file = persona_new(root) or persona_file
    elif pick == str(len(ps) + 2):
        persona_file = persona_import(root, ask("角色卡文件路径（.png 或 .json）")) or persona_file
    from .personas import set_persona_file
    set_persona_file(root, persona_file)
    print(f"\n配置好了：config.yaml + .env，人设 {persona_file}")
    if own_llm:
        from .config import load_config
        from .llm_custom import add_interactive
        try:
            run_coro(add_interactive(load_config(), ("helper", "memory", "night"), root))
        except Exception as e:  # noqa: BLE001
            print(f"  自定义接口没加上：{e}（之后双击 llm.bat 再加）")
    if use_qq:
        print("\n⑥ QQ：自动准备 NapCat（QQ 的协议端）……")
        from .napcat_install import main as napcat_main
        if napcat_main([]) != 0:
            print("QQ 这一步没做完：弄好后双击 qq_setup.bat 再来一次（已经下好的不会重新下载）。")
    print("\n下一步：双击 start.bat（Linux / macOS：bash start.sh）启动"
          + ("，在 Telegram 里给你的 bot 发 /start" if use_tg else "，用你的 QQ 给她的小号发句话") + "。")


# ================= 人设 =================
def _cfg():
    from .config import load_config
    return load_config()


def persona_list():
    cfg = _cfg()
    from .personas import list_personas
    for p in list_personas(cfg):
        mark = "▶" if p.get("current") else " "
        mem = f"记忆：{p['memory']}" if p.get("memory") else "（还没用过）"
        warn = f"  ⚠️ {len(p.get('warnings') or [])} 处建议" if p.get("warnings") else ""
        print(f"{mark} {p['file']:<28} {p['name']}（{p.get('identity') or '—'}）  {mem}{warn}")
    print("\n换人设：python run.py persona use 文件    检查：python run.py persona check 文件")


def persona_check(path: str | None = None) -> bool:
    cfg = _cfg()
    from .persona import load_persona
    f = Path(path) if path else Path(cfg.root) / cfg.persona_file
    if not f.exists():
        f = Path(cfg.root) / "persona" / f.name
    p = load_persona(f)
    print(f"{f}：{p.name}（{p.identity or '没写身份'}）· {p.ta} · life={p.life} · 对话示例 {len(p.examples)} 段 · 正文 {len(p.body)} 字")
    if p.warnings:
        for w in p.warnings:
            print("  ⚠️", w)
    else:
        print("  ✅ 没发现问题")
    return not any("没写 name" in w or "格式有错" in w for w in p.warnings)


def persona_use(path: str, share: bool = False):
    cfg = _cfg()
    root = Path(cfg.root)
    f = Path(path)
    if not f.is_absolute() and not (root / f).exists() and (root / "persona" / f.name).exists():
        f = Path("persona") / f.name
    if f.is_absolute():
        try:
            f = f.resolve().relative_to(root.resolve())
        except ValueError:
            print("人设文件要放在程序的 persona/ 文件夹里，先把它复制进去")
            return False
    rel = f.as_posix()
    if not (root / rel).exists():
        print(f"找不到 {rel}")
        return
    if not persona_check(str(root / rel)):
        print("人设有错，先改好再换。")
        return
    from .personas import assign, set_persona_file
    db = assign(cfg, rel, share_with_current=share)
    set_persona_file(root, rel)
    print(f"已换成 {rel}（记忆：{db}{'，和之前的角色共用' if share else ''}）。重启后生效（start.bat 窗口关掉再开）。")


def persona_new(root: Path = ROOT) -> str | None:
    from .personas import QUESTIONS, build_persona, expand_with_llm, safe_filename
    print("\n回答几个问题（直接回车用括号里的例子）：")
    ans = {k: ask(q, d) for k, q, d in QUESTIONS}
    draft = build_persona(ans)
    try:
        cfg = _cfg()
        has_key = any(v for v in (cfg.get("keys") or {}).values())
    except Exception:  # noqa: BLE001
        cfg, has_key = None, False
    if has_key and yes("让大模型帮你扩写成完整的人设（加细节和 10 段对话示例，约 1 分钟）"):
        from .llm import LLM
        print("  正在写……")
        draft = run_coro(expand_with_llm(LLM(cfg), draft))
    name = safe_filename(ans.get("name") or "persona")
    out = root / "persona" / f"{name}.md"
    i = 2
    while out.exists():
        out = root / "persona" / f"{name}{i}.md"
        i += 1
    out.write_text(draft, encoding="utf-8")
    rel = out.relative_to(root).as_posix()
    print(f"\n写好了：{rel}（可以用记事本打开继续改，尤其是“# 对话示例”）")
    from .persona import load_persona
    for w in load_persona(out).warnings:
        print("  ⚠️", w)
    return rel


def persona_import(root: Path, path: str) -> str | None:
    from .personas import card_to_persona, read_card, safe_filename
    try:
        card = read_card(path)
    except Exception as e:  # noqa: BLE001
        print(f"读不了这张卡：{e}")
        return None
    print(f"角色卡：{card.get('name')}")
    call = ask("TA 怎么叫你", "你")
    g = ask("角色性别（female / male / other）", "female")
    ug = ask("你的性别（male / female / other）", "male")
    text = card_to_persona(card, call, g, ug)
    out = root / "persona" / f"{safe_filename(card.get('name') or 'card')}.md"
    i = 2
    while out.exists():
        out = root / "persona" / f"{safe_filename(card.get('name') or 'card')}{i}.md"
        i += 1
    out.write_text(text, encoding="utf-8")
    rel = out.relative_to(root).as_posix()
    print(f"导入好了：{rel}")
    from .persona import load_persona
    for w in load_persona(out).warnings:
        print("  ⚠️", w)
    return rel


def persona_main(argv: list[str]):
    sub = argv[0] if argv else "list"
    rest = [a for a in argv[1:] if not a.startswith("--")]
    if sub == "list":
        persona_list()
    elif sub == "check":
        ok = persona_check(rest[0] if rest else None)
        sys.exit(0 if ok else 1)
    elif sub == "use" and rest:
        persona_use(rest[0], share="--share" in argv)
    elif sub == "new":
        rel = persona_new()
        if rel and yes("现在就换成这个人设吗"):
            persona_use(rel)
    elif sub == "import" and rest:
        rel = persona_import(ROOT, rest[0])
        if rel and yes("现在就换成这个人设吗"):
            persona_use(rel)
    else:
        print(__doc__)
        print("用法：python run.py persona list | check [文件] | use 文件 [--share] | new | import 角色卡.png")
