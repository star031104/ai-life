"""NapCat 一键安装：解压、WebSocket 模板、找已有的。（不联网、不真的启动 NapCat）"""
import json
import zipfile

import ailife.napcat_install as N


def _zip(tmp_path, nested: bool):
    z = tmp_path / "nc.zip"
    pre = "NapCat.Shell.Windows.Node/" if nested else ""
    with zipfile.ZipFile(z, "w") as f:
        f.writestr(pre + "napcat.bat", "@echo off\r\n\"%~dp0node.exe\" \"%~dp0index.js\" %*\r\n")
        f.writestr(pre + "index.js", "//")
        f.writestr(pre + "napcat/napcat.mjs", "//")
        f.writestr(pre + "napcat/config/webui.json", json.dumps({"autoLoginAccount": ""}))
        f.writestr("../evil.txt" if not nested else pre + "ok.txt", "x")
    return z


def test_extract_flat_and_nested(tmp_path):
    for nested in (False, True):
        dest = tmp_path / f"out{nested}"
        N.extract(_zip(tmp_path, nested), dest)
        assert (dest / "napcat.bat").is_file() and (dest / "napcat" / "napcat.mjs").is_file()
        assert N.is_napcat_root(dest)
    assert not (tmp_path / "evil.txt").exists()           # zip 里的 ../ 不会写到外面


def test_ensure_ws_template_then_reuse(tmp_path):
    dest = tmp_path / "nc"
    N.extract(_zip(tmp_path, False), dest)
    port, token = N.ensure_ws(dest)
    tpl = json.loads((dest / "napcat" / "config" / "onebot11.json").read_text(encoding="utf-8"))
    ws = tpl["network"]["websocketServers"][0]
    assert port == 3001 and token and ws["token"] == token and ws["messagePostFormat"] == "array"
    assert N.ensure_ws(dest) == (port, token)             # 再跑一次不换 token
    # 登录后 NapCat 用模板生成了 onebot11_<号>.json：读它的
    (dest / "napcat" / "config" / "onebot11_123456.json").write_text(json.dumps(tpl), encoding="utf-8")
    assert N.ensure_ws(dest) == (port, token)
    from ailife.qq_setup import read_onebot
    ob = read_onebot(dest / "napcat" / "config")
    assert ob["uin"] == 123456 and ob["token"] == token and ob["url"] == "ws://127.0.0.1:3001"


def test_ensure_ws_adds_to_logged_in_account(tmp_path):
    dest = tmp_path / "nc"
    N.extract(_zip(tmp_path, False), dest)
    f = dest / "napcat" / "config" / "onebot11_99999.json"
    f.write_text(json.dumps({"network": {"websocketServers": []}}), encoding="utf-8")
    port, token = N.ensure_ws(dest)
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["network"]["websocketServers"][0]["token"] == token


def test_find_existing_prefers_project_folder(tmp_path):
    N.extract(_zip(tmp_path, False), tmp_path / "napcat")
    assert N.find_existing(tmp_path) == tmp_path / "napcat"
