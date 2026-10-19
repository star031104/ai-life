"""桌宠的另一种样子：codex-pets.net 上的像素宠物（Codex pet 格式的精灵图）。

和 Live2D 比：一张 3 MB 左右的 webp 图就是全部（不用 WebGL、不用下运行库），空闲时几乎不占 CPU；
每只宠物有 9 组动作（待机、跑、挥手、跳、难过、等你、干活、看东西……）和 16 个注视方向，
桌宠按她的状态播对应的动作（思路参考了 Wakuwaku：https://github.com/zhoupengjie/Wakuwaku ，MIT）。

宠物的版权归各自作者（很多是同人二创），这里不打包任何一只：你在小屋里挑一只，程序才从 codex-pets.net
下载到你自己电脑的 data/pet/sprites/<id>/。只认 codex-pets.net 上的地址，也只从那里下载。

精灵图格式（Codex pet）：8 列 × 192×208 的格子；v2 是 11 行（1536×2288，最后两行是 16 个注视方向），
v1 是 9 行（1536×1872，没有注视）。
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

SITE = "https://codex-pets.net"
ATLASES = {"1536x2288": 2, "1536x1872": 1}
MAX_SHEET = 64 * 1024 * 1024
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,80}$")


class PetError(Exception):
    pass


def is_id(text: str) -> bool:
    return bool(ID_RE.match(text or ""))


def is_site(host: str | None) -> bool:
    host = (host or "").lower()
    return host == "codex-pets.net" or host.endswith(".codex-pets.net")


def parse_ref(text: str) -> str:
    """从你粘贴的东西里拿出宠物的 id：直接写 id，或者 codex-pets.net 上它的页面 / 接口 / 图片地址。"""
    text = (text or "").strip()
    if is_id(text):
        return text
    u = urlparse(text if "://" in text else "https://" + text)
    if not is_site(u.hostname):
        raise PetError(f"只支持 codex-pets.net 上的宠物：{text}")

    def after(part: str, prefix: str) -> str | None:
        if not part.startswith(prefix):
            return None
        pid = unquote(re.split(r"[/?#]", part[len(prefix):])[0])
        return pid if is_id(pid) else None

    m = re.match(r"^/assets/pets/v/\d+/([^/]+)/", u.path)
    for cand in (after(u.fragment, "/pets/"), after(u.path, "/api/pets/"),
                 unquote(m.group(1)) if m and is_id(unquote(m.group(1))) else None, after(u.path, "/pets/")):
        if cand:
            return cand
    raise PetError(f"没认出是哪只宠物：{text}")


def sprites_dir(root: Path) -> Path:
    return Path(root) / "data" / "pet" / "sprites"


def local_pets(root: Path) -> list[dict]:
    """已经下载到电脑上的宠物。"""
    out = []
    d = sprites_dir(root)
    if not d.is_dir():
        return out
    for p in sorted(d.iterdir()):
        if p.name.startswith(".") or not (p / "spritesheet.webp").exists():
            continue
        try:
            meta = json.loads((p / "pet.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        out.append({"id": p.name, "name": meta.get("displayName") or p.name, "author": meta.get("author") or "",
                    "description": meta.get("description") or "", "source": meta.get("source") or "",
                    "version": 1 if str(meta.get("spriteVersionNumber")) == "1" else 2})
    return out


def find_local(root: Path, pid: str) -> dict | None:
    return next((p for p in local_pets(root) if p["id"] == pid), None) if is_id(pid) else None


def remove_local(root: Path, pid: str) -> bool:
    if not is_id(pid):
        return False
    p = sprites_dir(root) / pid
    if not p.is_dir():
        return False
    shutil.rmtree(p, ignore_errors=True)
    return True


def _write(root: Path, pid: str, sheet: bytes, meta: dict):
    """先写进 .<id>.new/ 再一次改名换上去：中途断了也不会留下半只宠物。"""
    d = sprites_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    out, stage, old = d / pid, d / f".{pid}.new", d / f".{pid}.old"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    (stage / "pet.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (stage / "spritesheet.webp").write_bytes(sheet)
    if out.exists():
        shutil.rmtree(old, ignore_errors=True)
        out.rename(old)
    stage.rename(out)
    shutil.rmtree(old, ignore_errors=True)


async def _get(url: str, proxy: str | None, *, as_json: bool, limit: int = MAX_SHEET):
    """先直连，不行再走代理（codex-pets.net 在国内有时很慢）。"""
    import httpx
    last: Exception | None = None
    for px in ((None, proxy) if proxy else (None,)):
        try:
            async with httpx.AsyncClient(proxy=px, timeout=40, follow_redirects=False,
                                         headers={"user-agent": "ai-life"}) as c:
                r = await c.get(url)
                if r.status_code == 404:
                    raise PetError("codex-pets.net 上没有这只宠物")
                r.raise_for_status()
                if len(r.content) > limit:
                    raise PetError("文件太大了")
                return r.json() if as_json else r.content
        except PetError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
    raise PetError(f"连不上 codex-pets.net：{last}")


async def download(root: Path, ref: str, proxy: str | None = None) -> dict:
    """把一只宠物下载到 data/pet/sprites/<id>/（pet.json + spritesheet.webp），返回它的信息。"""
    pid = parse_ref(ref)
    body = await _get(f"{SITE}/api/pets/{quote(pid)}", proxy, as_json=True, limit=2_000_000)
    pet = (body or {}).get("pet") or {}
    sheet_url = urljoin(SITE + "/", str(pet.get("spritesheetUrl") or ""))
    u = urlparse(sheet_url)
    if u.scheme != "https" or not is_site(u.hostname):
        raise PetError(f"图集不在 codex-pets.net 上，不下载：{sheet_url}")
    atlas = str((pet.get("validationReport") or {}).get("atlasSize") or "")
    if atlas and atlas not in ATLASES:
        raise PetError(f"这只宠物的图集是 {atlas}，桌宠只认 1536×2288 或 1536×1872")
    sheet = await _get(sheet_url, proxy, as_json=False)
    if not sheet.startswith(b"RIFF") or sheet[8:12] != b"WEBP":
        raise PetError("下载到的不是 webp 图片")
    version = ATLASES.get(atlas) or (1 if str(pet.get("spriteVersionNumber")) == "1" else 2)
    meta = {"id": pid, "displayName": pet.get("displayName") or pid, "description": pet.get("description") or "",
            "author": pet.get("ownerName") or "", "source": f"{SITE}/#/pets/{pid}",
            "spritesheetPath": "spritesheet.webp", "spriteVersionNumber": version, "kind": pet.get("kind") or ""}
    _write(root, pid, sheet, meta)
    return find_local(root, pid) or {"id": pid}


async def gallery(query: str = "", page: int = 1, sort: str = "popular", proxy: str | None = None) -> dict:
    """codex-pets.net 的图库（一页 12 只）。只把图库显示的东西和站内的图片地址交给网页。"""
    sort = "new" if sort in ("new", "newest") else "popular"
    url = f"{SITE}/api/pets?page={max(1, int(page))}&pageSize=12&sort={sort}"
    q = (query or "").strip()[:100]
    if q:
        url += "&q=" + quote(q)
    body = await _get(url, proxy, as_json=True, limit=4_000_000)
    items = []
    for p in (body or {}).get("pets") or []:
        pid = str(p.get("id") or "")
        if not is_id(pid):
            continue
        prev = next((str(p[k]) for k in ("posterUrl", "previewUrl")
                     if str(p.get(k) or "").startswith(SITE + "/")), "")
        items.append({"id": pid, "name": p.get("displayName") or pid, "author": p.get("ownerName") or "",
                      "likes": int(p.get("likeCount") or 0), "description": str(p.get("description") or "")[:200],
                      "version": 1 if str(p.get("spriteVersionNumber")) == "1" else 2, "poster": prev})
    return {"items": items, "page": body.get("page") or page, "totalPages": body.get("totalPages") or 1,
            "total": body.get("total") or len(items)}
