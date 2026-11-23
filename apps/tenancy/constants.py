from decimal import Decimal
from typing import TypedDict

# Circulating currencies in ISO 4217 (list as of 2026). Fund codes (BOV, CHE, CLF, …),
# precious metals and testing codes are excluded: they are never a company's currency.
ISO_4217_CODES = frozenset(
    """
    AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN
    BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR
    FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD
    JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD
    MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP
    PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC
    SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VED VES VND VUV WST
    XAF XCD XCG XOF XPF YER ZAR ZMW ZWG
    """.split()  # noqa: SIM905  (a code table reads better as one block)
)


class ServiceTypeDefaults(TypedDict):
    code: str
    name: str
    max_weight_kg: Decimal
    max_length_cm: Decimal
    max_width_cm: Decimal
    max_height_cm: Decimal


# Seeded for every new company (memory D-API-20, D-API-22). Limits apply per parcel.
DEFAULT_SERVICE_TYPES: tuple[ServiceTypeDefaults, ...] = (
    {
        "code": "standard",
        "name": "Standard",
        "max_weight_kg": Decimal(30),
        "max_length_cm": Decimal(120),
        "max_width_cm": Decimal(80),
        "max_height_cm": Decimal(80),
    },
    {
        "code": "express",
        "name": "Express",
        "max_weight_kg": Decimal(30),
        "max_length_cm": Decimal(120),
        "max_width_cm": Decimal(80),
        "max_height_cm": Decimal(80),
    },
    {
        "code": "freight",
        "name": "Freight",
        "max_weight_kg": Decimal(1000),
        "max_length_cm": Decimal(240),
        "max_width_cm": Decimal(120),
        "max_height_cm": Decimal(220),
    },
)
