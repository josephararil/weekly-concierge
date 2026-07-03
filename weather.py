"""Weekend weather data for the Weekend Concierge email — open-meteo (no API key). Returns raw
forecast numbers for Sat/Sun so the LLM stages can reason about the actual data themselves,
rather than parsing a pre-classified label. Always returns a dict for Sat/Sun; never raises
(network failures degrade to empty per-day dicts)."""

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


def weekend_weather(latlon, today):
    """Fetch the daily forecast for the upcoming Sat/Sun. Returns {"Sat": {...}, "Sun": {...}},
    each a dict of raw values (or {} on fetch failure / forecast-horizon miss):
      date, condition, max_temp_c, min_temp_c, feels_like_max_c, feels_like_min_c,
      humidity_pct, cloud_cover_pct, rain_chance_pct."""
    lat, lon = latlon
    days_until_sat = (5 - today.weekday()) % 7
    saturday = today.date() if isinstance(today, dt.datetime) else today
    saturday = saturday + dt.timedelta(days=days_until_sat)
    sunday = saturday + dt.timedelta(days=1)

    try:
        resp = requests.get(
            _FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": _DAILY_FIELDS,
                "timezone": "auto",
                "start_date": saturday.isoformat(),
                "end_date": sunday.isoformat(),
            },
            timeout=_FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        daily = resp.json()["daily"]

        result = {}
        for label, date_iso in (("Sat", saturday.isoformat()), ("Sun", sunday.isoformat())):
            idx = daily["time"].index(date_iso)
            code = daily["weather_code"][idx]
            result[label] = {
                "date": date_iso,
                "condition": _WMO_DESCRIPTIONS.get(code, f"WMO code {code}"),
                "max_temp_c": daily["temperature_2m_max"][idx],
                "min_temp_c": daily["temperature_2m_min"][idx],
                "feels_like_max_c": daily["apparent_temperature_max"][idx],
                "feels_like_min_c": daily["apparent_temperature_min"][idx],
                "humidity_pct": daily["relative_humidity_2m_mean"][idx],
                "cloud_cover_pct": daily["cloud_cover_mean"][idx],
                "rain_chance_pct": daily["precipitation_probability_max"][idx],
            }
        return result
    except Exception:
        return {"Sat": {}, "Sun": {}}


if __name__ == "__main__":
    PLOVDIV_LATLON = (42.1354, 24.7453)
    print(weekend_weather(PLOVDIV_LATLON, dt.datetime.now()))
