"""人设管理：每个角色一份自己的记忆、换角色、检查人设、导入 SillyTavern 角色卡。

    python run.py persona list                 看有哪些人设、现在用的是哪个
    python run.py persona check [文件]          检查人设写得对不对（缺什么、哪里格式错了）
    python run.py persona use 文件 [--share]    换成这个角色（默认用这个角色自己的记忆；--share 共用现在的记忆）
    python run.py persona new                  问几个问题，帮你生成一个新人设（可以让大模型帮忙扩写）
    python run.py persona import 角色卡.png|.json   导入 SillyTavern / TavernAI 角色卡

每个角色的记忆（聊天记录、日记、她身边的人……）存在各自的数据库里：
    第一个用的角色 → data/ailife.db（老用户的数据不动）
    之后新加的角色 → data/chars/<文件名>.db
对应关系记在 data/personas.json。
"""
from __future__ import annotations

import base64
import json
import os
import re
import struct
import zlib
from pathlib import Path

from .persona import load_persona, parse_persona

REGISTRY = "data/personas.json"


# ---------------- 每个角色的数据库 ----------------
def _rel(cfg, path) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(Path(cfg.root).resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def _load_reg(root: Path) -> dict:
    f = Path(root) / REGISTRY
    if f.exists():
        try:
            reg = json.loads(f.read_text(encoding="utf-8")) or {}
            if isinstance(reg, dict):
                return reg
        except (ValueError, OSError):
            pass
        # 文件坏了：留一份，再按 data/chars/ 里现有的数据库重建（免得把别的角色的记忆分给当前角色）
        import logging
        import time as _t
        bad = f.with_name(f"personas.bad-{_t.strftime('%Y%m%d-%H%M%S')}.json")
        try:
            os.replace(f, bad)
        except OSError:
            pass
        logging.getLogger("ailife.personas").warning("data/personas.json 读不出来，已改名为 %s 并重建", bad.name)
        reg = {f"persona/{d.stem}.md": f"data/chars/{d.name}"
               for d in sorted((Path(root) / "data" / "chars").glob("*.db"))
               if (Path(root) / "persona" / f"{d.stem}.md").exists()}
        _save_reg(root, reg)
        return reg
    return {}


def _save_reg(root: Path, reg: dict):
    f = Path(root) / REGISTRY
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def db_path(cfg) -> Path:
    """当前人设用哪个数据库。第一次运行时把现有的 data/ailife.db 记给当前人设。"""
    root = Path(cfg.root)
    reg = _load_reg(root)
    key = _rel(cfg, Path(cfg.root) / cfg.persona_file)
    if key not in reg:
        used = set(reg.values())
        if "data/ailife.db" not in used:
            reg[key] = "data/ailife.db"
        else:
            reg[key] = f"data/chars/{Path(key).stem}.db"
        _save_reg(root, reg)
    return root / reg[key]


def assign(cfg, persona_file: str, share_with_current: bool = False) -> str:
    """换人设前先定好它用哪个数据库。"""
    root = Path(cfg.root)
    cur_db = db_path(cfg)                      # 先把现在的角色登记好（data/ailife.db 是它的），再给新角色分
    reg = _load_reg(root)
    key = _rel(cfg, Path(cfg.root) / persona_file)
    if share_with_current:
        reg[key] = _rel(cfg, cur_db)
    elif key not in reg:
        reg[key] = "data/ailife.db" if "data/ailife.db" not in set(reg.values()) else f"data/chars/{Path(key).stem}.db"
    _save_reg(root, reg)
    return reg[key]


def all_dbs(cfg) -> list[Path]:
    root = Path(cfg.root)
    out = {root / "data" / "ailife.db"}
    out |= {root / v for v in _load_reg(root).values()}
    return sorted(p for p in out if p.exists())


# ---------------- 人设文件 ----------------
def list_personas(cfg) -> list[dict]:
    root = Path(cfg.root)
    cur = _rel(cfg, root / cfg.persona_file)
    reg = _load_reg(root)
    out = []
    for f in sorted((root / "persona").glob("*.md")):
        key = _rel(cfg, f)
        try:
            p = load_persona(f)
            out.append({"file": key, "name": p.name, "identity": p.identity, "gender": p.gender,
                        "current": key == cur, "memory": reg.get(key), "warnings": p.warnings,
                        "template": f.stem == "template"})
        except Exception as e:  # noqa: BLE001
            out.append({"file": key, "name": "（读不出来）", "error": str(e), "current": key == cur})
    return out


def set_persona_file(root: Path, rel: str):
    """改 config.yaml 里的 persona_file（只改这一行）。"""
    p = Path(root) / "config.yaml"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    line = f"persona_file: {rel}"
    if re.search(r"^persona_file\s*:.*$", text, re.M):
        text = re.sub(r"^persona_file\s*:.*$", line, text, count=1, flags=re.M)
    else:
        text = line + "\n" + text
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def check_text(text: str) -> list[str]:
    return parse_persona(text).warnings


# ---------------- SillyTavern / TavernAI 角色卡 ----------------
def read_card(path: str | Path) -> dict:
    """读角色卡：PNG（tEXt 里的 chara / ccv3，base64 JSON）或 JSON。返回卡片里的 data。"""
    raw = Path(path).read_bytes()
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        i, found = 8, {}
        while i < len(raw):
            n = struct.unpack(">I", raw[i:i + 4])[0]
            typ = raw[i + 4:i + 8]
            data = raw[i + 8:i + 8 + n]
            if typ == b"tEXt":
                k, _, v = data.partition(b"\x00")
                found[k.decode("latin-1").lower()] = v
            elif typ == b"zTXt":
                k, _, rest = data.partition(b"\x00")
                found[k.decode("latin-1").lower()] = zlib.decompress(rest[1:])
            elif typ == b"iTXt":
                k, _, rest = data.partition(b"\x00")
                comp = rest[0]
                rest = rest[2:].split(b"\x00", 2)[-1]
                found[k.decode("latin-1").lower()] = zlib.decompress(rest) if comp else rest
            i += 12 + n
            if typ == b"IEND":
                break
        blob = found.get("ccv3") or found.get("chara")
        if not blob:
            raise ValueError("这张图片里没有角色卡信息")
        card = json.loads(base64.b64decode(blob).decode("utf-8"))
    else:
        card = json.loads(raw.decode("utf-8-sig"))
    return card.get("data") if isinstance(card.get("data"), dict) else card


def _subst(text: str, char: str, user: str) -> str:
    text = re.sub(r"\{\{\s*char\s*\}\}|<BOT>", char, text or "", flags=re.I)
    return re.sub(r"\{\{\s*user\s*\}\}|<USER>", user, text, flags=re.I).strip()


def card_to_persona(card: dict, call_name: str = "你", gender: str = "female", user_gender: str = "male") -> str:
    """把角色卡转成我们的人设文件（文字原样保留；英文卡建议之后翻译成中文）。"""
    name = str(card.get("name") or "她").strip()
    user = call_name or "你"
    desc = _subst(card.get("description", ""), name, user)
    pers = _subst(card.get("personality", ""), name, user)
    scen = _subst(card.get("scenario", ""), name, user)
    first = _subst(card.get("first_mes", ""), name, user)
    sysp = _subst(card.get("system_prompt", ""), name, user)
    notes = _subst(card.get("creator_notes", ""), name, user)
    # 对话示例：<START> 分段，{{user}}: / {{char}}: 开头的行
    ex_md = []
    raw_ex = card.get("mes_example", "") or ""
    for n, block in enumerate(re.split(r"<START>", raw_ex, flags=re.I)):
        turns = []
        for line in block.splitlines():
            m = re.match(r"^\s*(\{\{\s*user\s*\}\}|<USER>|\{\{\s*char\s*\}\}|<BOT>|[^:：]{1,20})\s*[:：]\s*(.+)$", line, re.I)
            if not m:
                if turns and line.strip():
                    turns[-1] = (turns[-1][0], turns[-1][1] + " " + line.strip())
                continue
            who = m.group(1).strip()
            is_user = bool(re.match(r"\{\{\s*user|<USER>", who, re.I)) or who == user
            turns.append(("他" if is_user else name, _subst(m.group(2), name, user)))
        if turns:
            ex_md.append(f"## 示例 {len(ex_md) + 1} |\n" + "\n".join(f"{w}：{t}" for w, t in turns))
    fm = {"name": name, "call_name": user, "gender": gender, "user_gender": user_gender}
    if first:
        fm["greeting"] = first.split("\n")[0][:200]
    import yaml
    head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    parts = [f"---\n{head}\n# 从角色卡导入。英文卡建议把下面翻译成中文；补上 age / identity / life 效果更好（见 persona/template.md）\n---", ""]
    parts.append("# 她是谁\n\n" + (desc or "（角色卡里没有描述）"))
    if pers:
        parts.append("# 性格\n\n" + pers)
    if scen:
        parts.append("# 你们现在的情况\n\n" + scen)
    if sysp:
        parts.append("# 额外要求\n\n" + sysp)
    parts.append("# 说话方式\n\n- 像真人用手机聊天：短句，分几条发，不写长篇大论，不用 markdown，不用括号写动作。")
    parts.append("# 底线\n\n- 不写露骨或带性暗示的内容；他情绪很差、说起伤害自己的念头时，认真接住，鼓励他找身边的人或专业帮助。")
    if notes:
        parts.append(f"<!-- 角色卡作者的说明：{notes[:500]} -->")
    if ex_md:
        parts.append("# 对话示例\n\n" + "\n\n".join(ex_md))
    return "\n\n".join(parts).strip() + "\n"


def safe_filename(name: str) -> str:
    s = re.sub(r"[^\w一-龥-]+", "", name or "")[:24]
    return s or "persona"


# ---------------- 问答生成新人设 ----------------
QUESTIONS = [
    ("name", "她 / 他叫什么名字", "小满"),
    ("gender", "角色的性别（female 女 / male 男 / other）", "female"),
    ("user_gender", "你的性别（male 男 / female 女 / other）", "male"),
    ("call_name", "TA 平时怎么叫你（你的名字、昵称，或者“你”）", "你"),
    ("age", "TA 多大（成年人，至少 18）", "22"),
    ("identity", "一句话身份（大学生 / 上班族的职业 / 自由职业……）", "大学四年级学生"),
    ("relation", "你们是什么关系、怎么认识的", "交往一年的恋人，大学社团认识的"),
    ("traits", "性格关键词（用逗号隔开，3~6 个）", "温柔，有点小迷糊，嘴硬心软"),
    ("style", "说话风格（口头禅、语气、爱用的表情）", "爱用“欸”“嘛”，开心时会连发好几条"),
    ("likes", "TA 的爱好和日常", "画画、猫、奶茶、周末逛书店"),
]


def life_of(identity: str) -> str:
    if re.search(r"学生|大学|大一|大二|大三|大四|研究生|本科", identity or ""):
        return "student"
    if re.search(r"自由|画师|作家|博主|主播|自媒体", identity or ""):
        return "free"
    return "worker"


def build_persona(ans: dict) -> str:
    """不依赖大模型：按问答结果拼一个能直接用的人设。"""
    from .persona import GENDER_ALIASES, PRONOUN
    g = GENDER_ALIASES.get(str(ans.get("gender", "female")).lower(), "female")
    ug = GENDER_ALIASES.get(str(ans.get("user_gender", "male")).lower(), "male")
    ta, uta = PRONOUN[g], PRONOUN[ug]
    name, call = ans.get("name") or "小满", ans.get("call_name") or "你"
    try:
        age = max(18, int(re.sub(r"\D", "", str(ans.get("age") or "22")) or 22))
    except ValueError:
        age = 22
    ident = ans.get("identity") or ""
    traits = [t.strip() for t in re.split(r"[,，、;；\s]+", ans.get("traits") or "") if t.strip()]
    import yaml
    fm = yaml.safe_dump({"name": name, "call_name": call, "gender": g, "user_gender": ug, "age": age,
                         "identity": ident, "life": life_of(ident)}, allow_unicode=True, sort_keys=False).strip()
    lines = [f"---\n{fm}\n---", "", f"# {ta}是谁", "",
             f"你叫{name}，{age} 岁，{ident}。你和{uta}（对话里的“{call}”）的关系：{ans.get('relation') or '很亲近的恋人'}。",
             f"平时的爱好和日常：{ans.get('likes') or '（自己补充）'}。聊天时会自然提起自己今天在干什么。", "",
             "# 性格", ""]
    lines += [f"- {t}" for t in traits] or ["- （自己补充）"]
    lines += ["", "# 说话方式", "",
              "- 像真人用手机聊天：短句，分几条发，不写长篇大论，不列清单，不用 markdown，不用括号写动作。",
              f"- {ans.get('style') or '语气自然，偶尔用语气词'}", "",
              "# 底线", "",
              "- 感情温柔、尊重彼此，不写露骨或带性暗示的内容。",
              f"- 不用冷战、威胁、让{uta}愧疚的方式绑住{uta}。",
              f"- {uta}说起伤害自己的念头或情绪很差时，认真接住，鼓励{uta}找身边的人或专业帮助。", "",
              "# 对话示例", "",
              "<!-- 写 6~15 段" + ta + "平时怎么说话，效果会好很多。格式：“## 场景 | 关键词”，||| 表示分几条发。 -->", ""]
    return "\n".join(lines) + "\n"


async def expand_with_llm(llm, draft: str) -> str:
    """让大模型把问答拼出来的人设扩写完整（加细节和 10 段对话示例）。失败就返回原稿。"""
    prompt = f"""下面是一个 AI 伴侣角色的人设草稿（Markdown，顶部 --- 之间是基础信息）。请把它扩写成一份完整、具体、生动的人设：
- 顶部 --- 之间的字段原样保留，可以补充 look（外貌，一句话）、catchphrases（口头禅列表）；
- “是谁”写清楚身份、你们的关系和日常；“性格”写 4~6 条，每条写出具体会怎么做、怎么说；“说话方式”写 4~6 条；“底线”保留；
- 在“# 对话示例”下面写 10 段对话，覆盖：打招呼、想念、对方累了难过、被夸、问在干嘛、深夜催睡、好消息、撒娇、吃醋或小别扭、问是不是 AI（要坚定地以角色身份回答）；
  每段格式：“## 场景 | 关键词 关键词”，下面一行“他：……”（对方说的话，对方是女生就写“她：”），一行“名字：……”，名字那行用 ||| 分成 2~3 条消息；
- 全部用中文；不写露骨内容；角色是成年人；不要用括号写动作；
- 只输出完整的人设文件内容，不要解释。

草稿：
{draft}"""
    try:
        text = await llm.chat([{"role": "user", "content": prompt}], role="night", max_tokens=3000)
    except Exception:  # noqa: BLE001
        return draft
    text = re.sub(r"^```\w*\s*|\s*```\s*$", "", (text or "").strip())
    if not text.startswith("---") or "name:" not in text[:300]:
        return draft
    p = parse_persona(text)
    return text if p.name and len(p.body) > 200 else draft
