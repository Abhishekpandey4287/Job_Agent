"""India location checks. Unspecified locations stay in the workbook for review."""

from __future__ import annotations

import re

_INDIA = re.compile(
    r"\b("
    r"india|indian|bengaluru|bangalore|hyderabad|pune|mumbai|bombay|chennai|"
    r"gurgaon|gurugram|noida|delhi|kolkata|ahmedabad|jaipur|kochi|coimbatore|"
    r"mysuru|mysore|chandigarh|indore|thiruvananthapuram|trivandrum|"
    r"karnataka|telangana|maharashtra|tamil nadu|haryana|kerala|gujarat|"
    r"rajasthan|west bengal"
    r")\b",
    re.I,
)
_INDIA_CODE = re.compile(r"(?:^|[\s,/(])IN(?:$|[\s,)/])")
_OUTSIDE = re.compile(
    r"\b("
    r"united states|united kingdom|canada|germany|france|australia|singapore|"
    r"japan|ireland|netherlands|sweden|switzerland|israel|brazil|mexico|spain|"
    r"italy|poland|romania|philippines|vietnam|indonesia|malaysia|dubai|"
    r"china|taiwan|south korea|new zealand|california|texas|washington|"
    r"new york|seattle|austin|boston|london|berlin|paris|dublin|toronto|"
    r"sydney|tokyo|munich|amsterdam|san francisco|san jose|mountain view|"
    r"santa clara|cupertino|bellevue|redmond|los angeles|chicago|usa|u\.s\.a?\.?|"
    r"uk|nyc|czech|prague|malm[oö]|sweden|emea|europe|poland|romania|hungary|"
    r"portugal|austria|belgium|finland|norway|denmark|greece|czechia|slovakia|"
    r"warsaw|krakow|lisbon|budapest|vienna|bucharest|zurich|geneva|tel aviv|"
    r"hong kong|seoul|beijing|shanghai|bangkok|jakarta|melbourne|auckland|"
    r"toronto|vancouver|edinburgh|manchester|stockholm|copenhagen|oslo|helsinki|"
    r"brazil|mexico|argentina|chile|colombia|peru|south africa|nigeria|kenya|"
    r"egypt|qatar|saudi|riyadh"
    r")\b",
    re.I,
)
_UNSPECIFIED = re.compile(
    r"multiple locations?|various locations?|not specified|several locations?|"
    r"hybrid|remote|anywhere",
    re.I,
)


def location_check(location: str) -> str:
    """Return india, outside_india, or unspecified."""
    text = (location or "").strip()
    if not text:
        return "unspecified"
    in_india = bool(_INDIA.search(text) or _INDIA_CODE.search(text))
    outside = bool(_OUTSIDE.search(text))
    if in_india and not outside:
        return "india"
    if in_india and outside:
        return "india"
    if outside and not _UNSPECIFIED.search(text):
        return "outside_india"
    if outside and _UNSPECIFIED.search(text) and not in_india:
        return "outside_india"
    return "unspecified"
