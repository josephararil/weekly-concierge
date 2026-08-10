"""Weekly weather data for the Weekend Concierge email — open-meteo (no API key). Returns raw
forecast numbers for the upcoming week so the LLM stages can reason about the actual data
themselves, rather than parsing a pre-classified label. Never raises (network failures degrade
to an empty list)."""

import datetime as dt

import requests

_FETCH_TIMEOUT = 15
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_DAILY_FIELDS = (
    "temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,"
    "precipitation_probability_max,relative_humidity_2m_mean,cloud_cover_mean,weather_code"
)

# WMO weather-interpretation codes (open-meteo's daily weather_code) -> short description.
_WMO_DESCRIPTIONS = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow fall", 73: "moderate snow fall", 75: "heavy snow fall",
    77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def week_weather(latlon, today, days=7):
    """Fetch the daily forecast for today+1 .. today+days. Returns a list of dicts ordered by
    date, one per day, each a dict of raw values:
      label, date, condition, max_temp_c, min_temp_c, feels_like_max_c, feels_like_min_c,
      humidity_pct, cloud_cover_pct, rain_chance_pct.
    Returns [] on any failure (fetch error, forecast-horizon miss). Never raises."""
    lat, lon = latlon
    today_date = today.date() if isinstance(today, dt.datetime) else today
    start = today_date + dt.timedelta(days=1)
    end = today_date + dt.timedelta(days=days)

    try:
        resp = requests.get(
            _FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": _DAILY_FIELDS,
                "timezone": "auto",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
            timeout=_FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        daily = resp.json()["daily"]

        result = []
        for idx, date_iso in enumerate(daily["time"]):
            date = dt.date.fromisoformat(date_iso)
            code = daily["weather_code"][idx]
            result.append({
                "label": date.strftime("%a"),
                "date": date_iso,
                "condition": _WMO_DESCRIPTIONS.get(code, f"WMO code {code}"),
                "max_temp_c": daily["temperature_2m_max"][idx],
                "min_temp_c": daily["temperature_2m_min"][idx],
                "feels_like_max_c": daily["apparent_temperature_max"][idx],
                "feels_like_min_c": daily["apparent_temperature_min"][idx],
                "humidity_pct": daily["relative_humidity_2m_mean"][idx],
                "cloud_cover_pct": daily["cloud_cover_mean"][idx],
                "rain_chance_pct": daily["precipitation_probability_max"][idx],
            })
        return result
    except Exception:
        return []


if __name__ == "__main__":
    PLOVDIV_LATLON = (42.1354, 24.7453)
    print(week_weather(PLOVDIV_LATLON, dt.datetime.now()))
