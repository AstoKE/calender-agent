"""i18n çekirdeği için deterministik testler (bkz. plan: Faz 1).

DB'ye dokunmuyor, gerçek model/servis bağımlılığı yok."""

from __future__ import annotations

from datetime import date, datetime

from src.localization import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    missing_keys,
    normalize_language,
    translate,
    translator_for,
)
from src.localization.catalog import MESSAGES
from src.localization.formatting import (
    format_date,
    format_datetime,
    format_day_header,
    format_relative_day,
    format_time,
    format_time_range,
    month_name,
    to_display_timezone,
)
from src.services.timeutil import format_date_tr


def test_no_missing_translations():
    for lang in SUPPORTED_LANGUAGES:
        assert missing_keys(lang) == [], f"{lang} için eksik çeviri anahtarları var"


def test_every_message_has_default_language():
    for key, entry in MESSAGES.items():
        assert DEFAULT_LANGUAGE in entry, f"{key} varsayılan dili ({DEFAULT_LANGUAGE}) içermiyor"


def test_normalize_language_variants():
    assert normalize_language("tr") == "tr"
    assert normalize_language("EN") == "en"
    assert normalize_language("en-GB") == "en"
    assert normalize_language("en_US") == "en"
    assert normalize_language(None) == DEFAULT_LANGUAGE
    assert normalize_language("") == DEFAULT_LANGUAGE
    assert normalize_language("fr") == DEFAULT_LANGUAGE  # desteklenmiyor -> varsayılan


def test_translate_known_key():
    assert translate("common.approve", "tr") == "Onayla"
    assert translate("common.approve", "en") == "Approve"


def test_translate_unknown_key_returns_key_not_error():
    assert translate("bu.anahtar.yok", "tr") == "bu.anahtar.yok"


def test_translate_missing_language_falls_back_to_default():
    # Sahte, yalnızca tr içeren bir anahtar simüle edelim
    MESSAGES["_test.only_tr"] = {"tr": "sadece türkçe"}
    try:
        assert translate("_test.only_tr", "en") == "sadece türkçe"
    finally:
        del MESSAGES["_test.only_tr"]


def test_translate_with_params():
    MESSAGES["_test.with_param"] = {"tr": "{n} öğe", "en": "{n} items"}
    try:
        assert translate("_test.with_param", "tr", n=3) == "3 öğe"
    finally:
        del MESSAGES["_test.with_param"]


def test_translate_bad_params_does_not_raise():
    MESSAGES["_test.with_param2"] = {"tr": "{eksik} alan", "en": "{eksik} field"}
    try:
        # 'eksik' parametresi verilmiyor -> format() KeyError atar, yutulmalı
        assert translate("_test.with_param2", "tr") == "{eksik} alan"
    finally:
        del MESSAGES["_test.with_param2"]


def test_translator_for_closure():
    t = translator_for("en")
    assert t("common.cancel") == "Cancel"
    t_tr = translator_for("bilinmeyen-dil")
    assert t_tr("common.cancel") == "Vazgeç"  # normalize_language ile tr'ye düşer


def test_month_name_tr_and_en():
    assert month_name(8, "tr") == "Ağustos"
    assert month_name(8, "en") == "August"


def test_format_date_tr_matches_legacy_helper():
    dt = datetime(2026, 8, 19, 14, 0)
    assert format_date_tr(dt) == "19 Ağustos"
    assert format_date(dt, "tr") == "19 Ağustos 2026"
    assert format_date(dt, "en") == "August 19, 2026"


def test_format_time_is_24h_in_both_languages():
    dt = datetime(2026, 8, 19, 14, 5)
    assert format_time(dt, "tr") == "14:05"
    assert format_time(dt, "en") == "14:05"


def test_format_datetime():
    dt = datetime(2026, 8, 19, 14, 0)
    assert format_datetime(dt, "tr") == "19 Ağustos 2026, 14:00"


def test_format_time_range():
    start = datetime(2026, 8, 19, 14, 0)
    end = datetime(2026, 8, 19, 15, 0)
    assert format_time_range(start, end, "tr") == "14:00–15:00"


def test_format_day_header():
    dt = date(2026, 8, 19)  # Çarşamba
    assert format_day_header(dt, "tr") == "Çarşamba, 19 Ağustos"
    assert format_day_header(dt, "en") == "Wednesday, August 19"


def test_format_relative_day():
    today = date(2026, 8, 19)
    assert format_relative_day(today, today, "tr") == "Bugün"
    assert format_relative_day(date(2026, 8, 20), today, "en") == "Tomorrow"
    assert format_relative_day(date(2026, 8, 25), today, "tr") is None


def test_to_display_timezone_naive_passthrough():
    naive = datetime(2026, 8, 19, 14, 0)
    assert to_display_timezone(naive, "Europe/Istanbul") is naive
