"""Şablon-katmanı sunum yardımcıları — hesaplama/formatlama mantığı burada,
Jinja şablonlarında değil (bkz. plan)."""

from __future__ import annotations

import hashlib
from typing import Callable

# Sabit, erişilebilir (beyaz metinle yeterli kontrast) 6 renklik palet —
# account_id'den DETERMİNİSTİK seçilir (aynı hesap her zaman aynı renk).
_AVATAR_PALETTE = ["#1a73e8", "#188038", "#e37400", "#d93025", "#8430ce", "#12805c"]


def initials(email: str) -> str:
    """'ada.yilmaz@example.com' -> 'AY'; ayraç yoksa ilk iki harf."""
    local = (email or "").split("@")[0]
    parts = [p for p in local.replace("_", ".").replace("-", ".").split(".") if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    if local:
        return local[:2].upper()
    return "?"


def avatar_color(account_id: str) -> str:
    """Resim/gravatar YOK (ağ çağrısı = offline-first ihlali + gizlilik) —
    account_id'nin hash'inden sabit palet içinden deterministik bir renk."""
    digest = hashlib.sha256((account_id or "").encode("utf-8")).hexdigest()
    return _AVATAR_PALETTE[int(digest[:8], 16) % len(_AVATAR_PALETTE)]


# structured_action'daki her anahtar, insan cümlesine çeviren bir katalog
# anahtarı + değeri nasıl biçimlendireceğini bilen bir fonksiyona eşlenir.
# Yeni bir action tipi (derivation.py'de) eklenince buraya bir satır eklemek
# yeterli — describe_structured_action'ın kendisi değişmez.
_ACTION_DESCRIBERS: dict[str, Callable[[object, Callable], str]] = {
    "default_duration_minutes": lambda v, t: f"{t('kurallarim.action.duration')}: {v} {t('common.minutes')}",
    "reminder_minutes_before": lambda v, t: f"{t('kurallarim.action.reminder')}: {v} {t('common.minutes')}",
    "importance": lambda v, t: f"{t('kurallarim.action.importance')}: {t('enum.importance.' + str(v))}",
}


def describe_structured_action(action: dict, t: Callable[..., str]) -> list[str]:
    """{"default_duration_minutes": 45} -> ["Varsayılan süre: 45 dakika"].
    Kurallarım'ın ham JSON yerine kuralı insan cümlesi olarak göstermesi
    için (bkz. plan §16 "structured_action asla ham JSON değil"). Bilinmeyen
    bir anahtar (ileride eklenen bir action tipi) sessizce atlanmaz — ham
    "anahtar: değer" olarak gösterilir, veri kaybolmuş gibi görünmesin."""
    lines = []
    for key, value in action.items():
        describer = _ACTION_DESCRIBERS.get(key)
        lines.append(describer(value, t) if describer else f"{key}: {value}")
    return lines


# candidate_snapshot() (src/memory/correction_memory.py) hangi alanları
# dahil ediyorsa burası da onları biliyor — yeni bir alan eklenirse ikisi
# birlikte güncellenmeli.
_DIFF_FIELD_LABELS = {
    "title": "duzenle.field.title",
    "start_datetime": "duzenle.field.datetime",
    "duration_minutes": "duzenle.field.duration",
    "importance": "duzenle.field.importance",
    "location": "duzenle.field.location",
    "event_type": "oneriler.field.type",
    "reminders": "duzeltmelerim.field.reminders",
}


def translate_field_names(fields: list[str], t: Callable[..., str]) -> list[str]:
    """Ham Pydantic alan adlarını (bkz. duzenle.html'in eksik/belirsiz alan
    uyarısı — CandidateEvent.missing_fields/ambiguous_fields her zaman
    "title"/"start_datetime"/"duration_minutes" gibi İNGİLİZCE kod
    tanımlayıcıları taşır, dile göre değişmez) kullanıcıya gösterilecek
    çevrilmiş etiketlere çevirir — diff etiketleriyle AYNI eşlemeyi
    (_DIFF_FIELD_LABELS) yeniden kullanır. Eşlenmemiş bir alan (bu üç
    alanın dışında hiçbiri şu an oluşmuyor, bkz. extraction.py) ham haliyle
    gösterilir, veri kaybolmuş gibi görünmesin."""
    return [t(_DIFF_FIELD_LABELS[f]) if f in _DIFF_FIELD_LABELS else f for f in fields]


def _format_diff_value(key: str, value: object, t: Callable[..., str]) -> str:
    if value in (None, "", []):
        return t("common.unspecified")
    if key == "importance":
        return t("enum.importance." + str(value))
    if key == "event_type":
        return t("enum.event_type." + str(value))
    if key == "reminders" and isinstance(value, list):
        return ", ".join(str(r) for r in value)
    return str(value)


def diff_snapshots(original: dict, corrected: dict, t: Callable[..., str]) -> list[tuple[str, str, str]]:
    """(alan etiketi, önce, sonra) üçlüleri — YALNIZCA gerçekten farklı olan
    alanlar için. Web'in reddetme akışı original_output/corrected_output'u
    AYNI candidate anlık görüntüsüyle kaydediyor (save_user_correction,
    reddetme sırasında candidate henüz düzenlenmemiş) — bu durumda liste
    BOŞ döner; çağıran boş listeyi "alan değişikliği yok" olarak göstermeli,
    sahte/boş bir diff tablosu değil."""
    diffs = []
    for key, label_key in _DIFF_FIELD_LABELS.items():
        before, after = original.get(key), corrected.get(key)
        if before == after:
            continue
        diffs.append((t(label_key), _format_diff_value(key, before, t), _format_diff_value(key, after, t)))
    return diffs
