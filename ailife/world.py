"""外面的世界：天气（Open-Meteo，失败时用 wttr.in，都免费不用 key）。"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

log = logging.getLogger("ailife.world")

WMO = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒", 80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "阵雪", 95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹",
}
EN_ZH = {"sunny": "晴", "clear": "晴", "partly cloudy": "多云", "cloudy": "阴", "overcast": "阴",
         "mist": "薄雾", "fog": "雾", "light rain": "小雨", "patchy rain possible": "可能有零星小雨",
         "patchy rain nearby": "附近有零星小雨", "light rain shower": "小阵雨", "moderate rain": "中雨",
         "heavy rain": "大雨", "moderate or heavy rain shower": "阵雨", "thundery outbreaks possible": "可能有雷雨",
         "light drizzle": "毛毛雨", "patchy light rain": "零星小雨", "light snow": "小雪", "haze": "霾"}
# wttr.in 的天气代码（WWO）→ 中文，比按英文描述翻译可靠
WWO = {113: "晴", 116: "局部多云", 119: "多云", 122: "阴", 143: "薄雾", 176: "局部阵雨", 179: "局部小雪",
       182: "局部雨夹雪", 185: "局部冻毛毛雨", 200: "局部雷暴", 227: "吹雪", 230: "暴风雪", 248: "雾", 260: "冻雾",
       263: "局部毛毛雨", 266: "毛毛雨", 281: "冻毛毛雨", 284: "强冻毛毛雨", 293: "局部小雨", 296: "小雨",
       299: "间歇中雨", 302: "中雨", 305: "间歇大雨", 308: "大雨", 311: "小冻雨", 314: "中到大冻雨",
       317: "小雨夹雪", 320: "中到大雨夹雪", 323: "局部小雪", 326: "小雪", 329: "局部中雪", 332: "中雪",
       335: "局部大雪", 338: "大雪", 350: "冰粒", 353: "小阵雨", 356: "中到大阵雨", 359: "暴雨",
       362: "小阵雨夹雪", 365: "中到大阵雨夹雪", 368: "小阵雪", 371: "中到大阵雪", 374: "小冰粒",
       377: "中到大冰粒", 386: "局部雷阵雨", 389: "雷阵雨", 392: "局部雷阵雪", 395: "雷阵雪"}
# 兜底：按英文关键词翻译（顺序有讲究，先匹配更具体的）
EN_WORDS = [("thunder", "雷雨"), ("sleet", "雨夹雪"), ("blizzard", "暴风雪"), ("snow", "雪"), ("hail", "冰雹"),
            ("freezing", "冻雨"), ("drizzle", "毛毛雨"), ("heavy rain", "大雨"), ("shower", "阵雨"), ("rain", "雨"),
            ("smoke", "霾"), ("haze", "霾"), ("dust", "浮尘"), ("sand", "沙尘"), ("fog", "雾"), ("mist", "薄雾"),
            ("overcast", "阴"), ("partly", "局部多云"), ("cloud", "多云"), ("sunny", "晴"), ("clear", "晴"),
            ("wind", "有风")]


def zh_weather(desc: str, code=None) -> str:
    """把 wttr.in 的天气描述变成中文：先看代码，再查表，最后按关键词。"""
    low0 = (desc or "").lower()
    for en, zh in (("smoke", "霾"), ("haze", "霾"), ("dust", "浮尘"), ("sand", "沙尘")):   # 代码表里没有这几种
        if en in low0:
            return zh
    try:
        if code is not None and int(code) in WWO:
            return WWO[int(code)]
    except (TypeError, ValueError):
        pass
    d = (desc or "").strip()
    if not d or not any("a" <= ch.lower() <= "z" for ch in d):
        return d or "天气未知"
    low = d.lower()
    if low in EN_ZH:
        return EN_ZH[low]
    for en, zh in EN_WORDS:
        if en in low:
            return zh
    return "天气未知"


RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}


class Weather:
    TTL = 3600

    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db
        self._cache: tuple[float, dict] | None = None

    @property
    def city(self) -> str:
        return (self.db.get("city") or self.cfg.get("world", {}).get("city") or "").strip()

    async def _get(self, url: str, params: dict) -> dict:
        last = None
        headers = {"User-Agent": "ai-life/6 (personal companion bot)"}
        for proxy in (None, self.cfg.proxy or None):
            try:
                async with httpx.AsyncClient(timeout=12, proxy=proxy, headers=headers) as cli:
                    r = await cli.get(url, params=params)
                    r.raise_for_status()
                    return r.json()
            except Exception as e:  # noqa: BLE001
                last = e
        raise RuntimeError(f"{url} 失败：{last}")

    async def _geocode(self, city: str) -> tuple[float, float, str]:
        cached = self.db.get("geo")
        if cached and cached.get("city") == city:
            return cached["lat"], cached["lon"], cached["name"]
        data = await self._get("https://geocoding-api.open-meteo.com/v1/search",
                               {"name": city, "count": 1, "language": "zh"})
        res = (data.get("results") or [None])[0]
        if not res:
            raise RuntimeError(f"找不到城市：{city}")
        g = {"city": city, "lat": res["latitude"], "lon": res["longitude"], "name": res.get("name", city)}
        self.db.set("geo", g)
        return g["lat"], g["lon"], g["name"]

    async def set_location(self, lat: float, lon: float) -> str:
        """他发来位置：反查城市（OpenStreetMap），之后天气就按这个位置查。返回一个可读的地名。"""
        name, city = "", ""
        try:
            d = await self._get("https://nominatim.openstreetmap.org/reverse",
                                {"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 14, "accept-language": "zh-CN"})
            a = d.get("address") or {}
            city = a.get("city") or a.get("town") or a.get("county") or a.get("state") or ""
            area = a.get("suburb") or a.get("city_district") or a.get("district") or a.get("neighbourhood") or ""
            name = (city + (area if area and area not in city else "")) or d.get("name") or ""
        except Exception as e:  # noqa: BLE001
            log.info("位置反查失败：%s", e)
        if not city:
            if self.city:                               # 没认出是哪：别把他原来设好的城市冲掉
                return ""
            city = "他发来的位置"
        self.db.set("city", city)
        self.db.set("geo", {"city": city, "lat": float(lat), "lon": float(lon), "name": city})
        self._cache = None
        return name or city

    async def fetch(self, force: bool = False) -> dict | None:
        """返回 {'city','now','today','rain_soon','text'}；没设城市返回 None。"""
        city = self.city
        if not city:
            return None
        if not force and self._cache and time.time() - self._cache[0] < self.TTL and self._cache[1]["city"] == city:
            return self._cache[1]
        try:
            w = await self._open_meteo(city)
        except Exception as e:  # noqa: BLE001
            log.info("Open-Meteo 不可用（%s），改用 wttr.in", e)
            try:
                w = await self._wttr(city)
            except Exception as e2:  # noqa: BLE001
                log.warning("天气获取失败：%s", e2)
                return self._cache[1] if self._cache else None
        self._cache = (time.time(), w)
        return w

    async def _open_meteo(self, city: str) -> dict:
        lat, lon, name = await self._geocode(city)
        d = await self._get("https://api.open-meteo.com/v1/forecast", {
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
            "current": "temperature_2m,apparent_temperature,weather_code,precipitation",
            "hourly": "precipitation_probability,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
        })
        cur, daily, hourly = d["current"], d["daily"], d["hourly"]
        now_s = f"{WMO.get(cur['weather_code'], '未知')}，{cur['temperature_2m']:.0f}°C（体感 {cur['apparent_temperature']:.0f}°C）"
        today_s = (f"{WMO.get(daily['weather_code'][0], '未知')}，{daily['temperature_2m_min'][0]:.0f}~"
                   f"{daily['temperature_2m_max'][0]:.0f}°C，降水概率 {daily['precipitation_probability_max'][0] or 0}%")
        tomorrow_s = (f"{WMO.get(daily['weather_code'][1], '未知')}，{daily['temperature_2m_min'][1]:.0f}~"
                      f"{daily['temperature_2m_max'][1]:.0f}°C")
        # 接下来 3 小时会不会下雨
        now_h = cur["time"][:13]
        idx = next((i for i, t in enumerate(hourly["time"]) if t[:13] >= now_h), 0)
        nxt = list(zip(hourly["precipitation_probability"][idx:idx + 3], hourly["weather_code"][idx:idx + 3]))
        rain_soon = any((p or 0) >= 60 and c in RAIN_CODES for p, c in nxt) and cur["weather_code"] not in RAIN_CODES
        return self._pack(name, now_s, today_s, tomorrow_s, rain_soon, cur["weather_code"] in RAIN_CODES)

    async def _wttr(self, city: str) -> dict:
        d = await self._get(f"https://wttr.in/{city}", {"format": "j1", "lang": "zh"})
        c = d["current_condition"][0]
        desc = (c.get("lang_zh") or c.get("weatherDesc") or [{"value": ""}])[0]["value"]
        desc = zh_weather(desc, c.get("weatherCode"))
        day0, day1 = d["weather"][0], d["weather"][1]
        now_s = f"{desc}，{c['temp_C']}°C（体感 {c['FeelsLikeC']}°C）"
        hours = day0.get("hourly", [])
        h = datetime.now(self.cfg.tz).hour
        upcoming = [x for x in hours if int(x["time"]) // 100 >= h][:2]
        rain_p = max([int(x.get("chanceofrain", 0)) for x in upcoming] or [0])
        today_s = f"{day0['mintempC']}~{day0['maxtempC']}°C，降水概率 {rain_p}%"
        def day_desc(day):
            hs = day.get("hourly") or []
            mid = next((x for x in hs if str(x.get("time")) in ("1200", "1500")), hs[len(hs) // 2] if hs else None)
            if not mid:
                return ""
            return zh_weather((mid.get("lang_zh") or mid.get("weatherDesc") or [{"value": ""}])[0]["value"],
                              mid.get("weatherCode")) + "，"
        today_s = day_desc(day0) + today_s
        tomorrow_s = day_desc(day1) + f"{day1['mintempC']}~{day1['maxtempC']}°C"
        raining = "雨" in desc or "雷" in desc
        return self._pack(city, now_s, today_s, tomorrow_s, rain_p >= 60 and not raining, raining)

    @staticmethod
    def _pack(name, now_s, today_s, tomorrow_s, rain_soon, raining):
        text = f"{name}：现在{now_s}；今天{today_s}；明天{tomorrow_s}。"
        if rain_soon:
            text += "接下来几个小时很可能下雨。"
        return {"city": name, "now": now_s, "today": today_s, "tomorrow": tomorrow_s,
                "rain_soon": rain_soon, "raining": raining, "text": text}
