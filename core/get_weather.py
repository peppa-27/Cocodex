from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


GEOLOCATION_URL = (
    "http://ip-api.com/json/?fields=status,message,country,regionName,city,lat,lon,query,timezone"
)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "晴朗",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴天",
    45: "雾",
    48: "霜雾",
    51: "小毛毛雨",
    53: "中等毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "中等阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴强冰雹",
}


@dataclass(frozen=True)
class Location:
    ip: str
    country: str
    region: str
    city: str
    latitude: float
    longitude: float
    timezone: str

    @property
    def display_name(self) -> str:
        parts = [self.city, self.region, self.country]
        return "，".join(part for part in parts if part)


@dataclass(frozen=True)
class Weather:
    time: str
    temperature: float
    apparent_temperature: float
    humidity: int
    precipitation: float
    wind_speed: float
    weather_code: int

    @property
    def description(self) -> str:
        return WEATHER_CODES.get(self.weather_code, f"未知天气({self.weather_code})")


def fetch_json(url: str, timeout: int = 10, *, use_system_proxy: bool = True) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "cocodex-weather-demo/0.1",
        },
    )
    opener = (
        urllib.request.build_opener()
        if use_system_proxy
        else urllib.request.build_opener(urllib.request.ProxyHandler({}))
    )

    try:
        with opener.open(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络请求失败: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("接口返回的不是有效 JSON") from exc


def get_location_by_ip() -> Location:
    # Clash 规则模式通常会被 urllib 读取为系统代理；IP 定位要禁用代理，
    # 否则拿到的是代理出口所在地。
    data = fetch_json(GEOLOCATION_URL, use_system_proxy=False)
    if data.get("status") != "success":
        message = data.get("message") or "未知原因"
        raise RuntimeError(f"粗 IP 定位失败: {message}")

    return Location(
        ip=str(data.get("query") or ""),
        country=str(data.get("country") or ""),
        region=str(data.get("regionName") or ""),
        city=str(data.get("city") or ""),
        latitude=float(data["lat"]),
        longitude=float(data["lon"]),
        timezone=str(data.get("timezone") or "auto"),
    )


def get_location_by_proxy_ip() -> Location:
    data = fetch_json(GEOLOCATION_URL, use_system_proxy=True)
    if data.get("status") != "success":
        message = data.get("message") or "未知原因"
        raise RuntimeError(f"代理 IP 定位失败: {message}")

    return Location(
        ip=str(data.get("query") or ""),
        country=str(data.get("country") or ""),
        region=str(data.get("regionName") or ""),
        city=str(data.get("city") or ""),
        latitude=float(data["lat"]),
        longitude=float(data["lon"]),
        timezone=str(data.get("timezone") or "auto"),
    )


def get_current_weather(location: Location, *, use_system_proxy: bool = True) -> Weather:
    params = urllib.parse.urlencode(
        {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "current": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "precipitation",
                    "weather_code",
                    "wind_speed_10m",
                ]
            ),
            "timezone": location.timezone or "auto",
        }
    )
    data = fetch_json(f"{OPEN_METEO_URL}?{params}", use_system_proxy=use_system_proxy)
    current = data.get("current")
    if not isinstance(current, dict):
        raise RuntimeError("天气接口没有返回 current 数据")

    return Weather(
        time=str(current["time"]),
        temperature=float(current["temperature_2m"]),
        apparent_temperature=float(current["apparent_temperature"]),
        humidity=int(current["relative_humidity_2m"]),
        precipitation=float(current["precipitation"]),
        wind_speed=float(current["wind_speed_10m"]),
        weather_code=int(current["weather_code"]),
    )


def get_current_weather_with_fallback(location: Location) -> Weather:
    errors: list[str] = []
    for use_system_proxy in (True, False):
        try:
            return get_current_weather(location, use_system_proxy=use_system_proxy)
        except RuntimeError as exc:
            source = "系统代理" if use_system_proxy else "直连"
            errors.append(f"{source}: {exc}")
    raise RuntimeError("；".join(errors))


def location_to_dict(location: Location) -> dict[str, Any]:
    return {
        "ip": location.ip,
        "country": location.country,
        "region": location.region,
        "city": location.city,
        "displayName": location.display_name,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "timezone": location.timezone,
    }


def weather_to_dict(weather: Weather) -> dict[str, Any]:
    return {
        "time": weather.time,
        "description": weather.description,
        "temperature": weather.temperature,
        "apparentTemperature": weather.apparent_temperature,
        "humidity": weather.humidity,
        "precipitation": weather.precipitation,
        "windSpeed": weather.wind_speed,
        "weatherCode": weather.weather_code,
    }


def _try_get_location(use_system_proxy: bool) -> tuple[Location | None, str | None]:
    try:
        if use_system_proxy:
            return get_location_by_proxy_ip(), None
        return get_location_by_ip(), None
    except RuntimeError as exc:
        return None, str(exc)


def get_weather_info() -> dict[str, Any]:
    """Return weather data for Qt.

    realIp is the best-effort direct network exit. virtualIp is the best-effort
    system-proxy exit, useful when Clash/VPN is enabled. Weather uses realIp
    first, then falls back to virtualIp if direct detection fails.
    """
    real_location, real_error = _try_get_location(use_system_proxy=False)
    virtual_location, virtual_error = _try_get_location(use_system_proxy=True)

    same_ip = bool(
        real_location
        and virtual_location
        and real_location.ip
        and real_location.ip == virtual_location.ip
    )
    weather_location = real_location or virtual_location
    weather_location_kind = "realIp" if real_location else "virtualIp"

    weather: Weather | None = None
    weather_error: str | None = None
    if weather_location is not None:
        try:
            weather = get_current_weather_with_fallback(weather_location)
        except RuntimeError as exc:
            weather_error = str(exc)
    else:
        weather_error = real_error or virtual_error or "没有可用的 IP 定位结果"

    return {
        "ok": weather is not None,
        "weatherLocationKind": weather_location_kind if weather else None,
        "location": location_to_dict(weather_location) if weather_location else None,
        "weather": weather_to_dict(weather) if weather else None,
        "realIp": {
            "ok": real_location is not None,
            "location": location_to_dict(real_location) if real_location else None,
            "error": real_error,
        },
        "virtualIp": {
            "ok": virtual_location is not None and not same_ip,
            "sameAsReal": same_ip,
            "location": location_to_dict(virtual_location)
            if virtual_location and not same_ip
            else None,
            "error": virtual_error,
        },
        "error": weather_error,
    }


def format_weather_report(location: Location, weather: Weather) -> str:
    return "\n".join(
        [
            "本地天气 demo",
            "-" * 24,
            f"粗 IP: {location.ip}",
            f"定位: {location.display_name or '未知位置'}",
            f"经纬度: {location.latitude:.4f}, {location.longitude:.4f}",
            f"时区: {location.timezone}",
            "",
            f"天气: {weather.description}",
            f"温度: {weather.temperature:.1f}°C，体感 {weather.apparent_temperature:.1f}°C",
            f"湿度: {weather.humidity}%",
            f"降水: {weather.precipitation:.1f} mm",
            f"风速: {weather.wind_speed:.1f} km/h",
            f"更新时间: {weather.time}",
        ]
    )


def main() -> int:
    data = get_weather_info()
    if not data["ok"]:
        print(f"获取天气失败: {data['error']}")
        return 1

    location_data = data["location"] or {}
    weather_data = data["weather"] or {}
    report_lines = [
        "本地天气 demo",
        "-" * 24,
        f"天气位置来源: {data['weatherLocationKind']}",
        f"粗 IP: {location_data.get('ip') or '--'}",
        f"定位: {location_data.get('displayName') or '未知位置'}",
        f"经纬度: {location_data.get('latitude'):.4f}, {location_data.get('longitude'):.4f}",
        f"时区: {location_data.get('timezone') or '--'}",
        "",
        f"天气: {weather_data.get('description') or '--'}",
        (
            f"温度: {weather_data.get('temperature'):.1f}°C，"
            f"体感 {weather_data.get('apparentTemperature'):.1f}°C"
        ),
        f"湿度: {weather_data.get('humidity')}%",
        f"降水: {weather_data.get('precipitation'):.1f} mm",
        f"风速: {weather_data.get('windSpeed'):.1f} km/h",
        f"更新时间: {weather_data.get('time') or '--'}",
    ]
    virtual_ip = data["virtualIp"]
    if virtual_ip["ok"]:
        virtual_location = virtual_ip["location"] or {}
        report_lines.extend(
            [
                "",
                "代理出口",
                f"粗 IP: {virtual_location.get('ip') or '--'}",
                f"定位: {virtual_location.get('displayName') or '未知位置'}",
            ]
        )
    elif virtual_ip["sameAsReal"]:
        report_lines.extend(["", "代理出口: 和真实出口相同"])

    print("\n".join(report_lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
