DOMAIN = "solar_eclipse"
# Primary sources (decade pages) - try in order
NASA_DECADE_URLS = [
	"https://eclipse.gsfc.nasa.gov/SEdecade/SEdecade2021.html",
	"https://eclipse.gsfc.nasa.gov/SEdecade/SEdecade2031.html",
	"https://eclipse.gsfc.nasa.gov/SEdecade/SEdecade2041.html",
	"https://eclipse.gsfc.nasa.gov/SEdecade/SEdecade2051.html",
	"https://eclipse.gsfc.nasa.gov/SEdecade/SEdecade2061.html",
]
JSEX_INDEX_URL = "https://eclipse.gsfc.nasa.gov/JSEX/JSEX-index.html"
ATTRIBUTION = "Eclipse predictions by NASA/GSFC"
SUPPORTED_REGIONS = ["Global", "Africa", "Asia", "Europe", "North America", "South America", "Oceania", "Antarctica"]
# Labels used on the JSEX index to identify region pages
JSEX_REGION_LABELS = {
	"Europe": "Europe",
	"Africa": "Africa",
	"Asia": "Asia and Asia Minor",
	"North America": "North America",
	"South America": "South America",
	"Oceania": "Southeast Asia, Australia & Oceana",
}
# Minimal fallback (used only if NASA pages are unavailable)
ECLIPSE_FALLBACK = [
	{"identifier": "2026-02-17", "type": "Annular", "time_utc": "17:00"},
	{"identifier": "2026-08-12", "type": "Total", "time_utc": "17:00"},
	{"identifier": "2027-08-02", "type": "Total", "time_utc": "10:08"},
]
VERSION = "1.0.0"
DEFAULT_NUM_EVENTS = 3
DEFAULT_UPDATE_HOUR = 1
DEFAULT_MIN_COVERAGE = 10
