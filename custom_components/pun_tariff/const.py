"""Constants of the PUN Tariff integration."""
from datetime import timedelta

from .gme import SOURCE_API, SOURCE_WEB  # noqa: F401  (re-exported)

DOMAIN = "pun_tariff"

CONF_SOURCE = "source"
CONF_ENERGY_SENSOR = "energy_sensor"
CONF_CONTRACT = "contract"

UPDATE_INTERVAL = timedelta(minutes=30)
DAY_AHEAD_HOUR = 14          # GME publishes the next day's prices around 13:00
STORAGE_VERSION = 1
SERVICE_CALCULATE_BILL = "calculate_bill"
