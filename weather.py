"""Weekend weather signal for event filtering — open-meteo (no API key), classified into
categorical archetypes so the pipeline can reason about weather instead of parsing prose.
Always returns a dict for Sat/Sun; never raises (network failures degrade to UNKNOWN)."""

import datetime as dt

import requests

_FETCH_TIMEOUT = 15
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_RAIN_CODES = {51, 52, 53, 54, 55, 56, 57, 61, 62, 63, 64, 65, 80, 81, 82}
_CLOUDY_CODES = {1, 2, 3}


def _classify(max_temp, precip_prob, weather_code):
    if precip_prob > 50 or weather_code in _RAIN_CODES:
        return "RAINY"
    if max_temp > 32:
        return "HOT"
    if max_temp < 12:
        return "COLD"
    if weather_code == 0 and 18 <= max_temp <= 28:
        return "OUTDOOR_PERFECT"
    if weather_code in _CLOUDY_CODES:
        return "CLOUDY"
    return "MILD"


def weekend_weather(latlon, today):
    """Fetch the daily forecast for the upcoming Sat/Sun and classify each into a semantic
    label (RAINY/HOT/COLD/CLOUDY/OUTDOOR_PERFECT/MILD/UNKNOWN). Returns {"Sat": ..., "Sun": ...}."""
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
                "daily": "temperature_2m_max,precipitation_probability_max,weather_code",
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
            result[label] = _classify(
                daily["temperature_2m_max"][idx],
                daily["precipitation_probability_max"][idx],
                daily["weather_code"][idx],
            )
        return result
    except Exception:
        return {"Sat": "UNKNOWN", "Sun": "UNKNOWN"}


if __name__ == "__main__":
    PLOVDIV_LATLON = (42.1354, 24.7453)
    print(weekend_weather(PLOVDIV_LATLON, dt.datetime.now()))
