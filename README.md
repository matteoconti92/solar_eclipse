# Solar Eclipse Integration for Home Assistant

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

Home Assistant custom integration that provides sensors for upcoming **solar eclipses**, including:

- Next solar eclipse date
- Eclipse coverage (%) – optional Skyfield calculation
- Eclipse type (Total, Partial, Annular)
- Visibility (where the eclipse is visible)
- Start, maximum, and end times

---

## Features

- Optional Skyfield dependency for calculating eclipse coverage percentage.
- Sensors update automatically; the coverage sensor updates **once every 24 hours** to reduce load.
- Multi-sensor setup: each attribute is a separate sensor.
- Ready for installation via **HACS**.

---

## Installation

### Manual

1. Copy the `solar_eclipse` folder into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration → Solar Eclipse**.
4. Optionally enable Skyfield for coverage calculation.

### HACS

1. Add this repository to HACS (Custom repositories → Integration → URL of your repo).
2. Install the integration.
3. Restart Home Assistant.
4. Add the integration from **Settings → Devices & Services**.

---

## Sensors

| Sensor                        | Description                                      |
|-------------------------------|--------------------------------------------------|
| Next Solar Eclipse             | Date of the next solar eclipse                   |
| Solar Eclipse Coverage (%)     | Percentage of coverage (requires Skyfield)      |
| Solar Eclipse Type             | Type of eclipse (Total, Partial, Annular)       |
| Solar Eclipse Visibility       | Where the eclipse is visible                     |
| Solar Eclipse Start            | Start time of the eclipse                        |
| Solar Eclipse Maximum          | Maximum eclipse time                             |
| Solar Eclipse End              | End time of the eclipse                           |

---

## Configuration Options

- **Install Skyfield**: checkbox to enable or disable the Skyfield library. Required to calculate coverage percentage.

---

## Requirements

- Home Assistant 2024.6.0 or later
- Optional: [Skyfield](https://rhodesmill.org/skyfield/) library for coverage calculation

---

## License

This integration is licensed under the **Apache License 2.0**. See [LICENSE](LICENSE) for details.

---

## Author

Matteo Conti  
