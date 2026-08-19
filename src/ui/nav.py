"""Sol navigasyon / mobil alt tab bar için TEK kaynak (bkz. docs/architecture-plan.md §16).

Masaüstü rail, mobil tab bar ve (Faz 3'te gelecek) taşma menüsü hepsi bu
tek NAV_ITEMS tuple'ından render edilir — navigasyon bir kez değişince tüm
yüzeyler otomatik tutarlı kalır, her birini ayrı ayrı güncellemek gerekmez."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    slug: str  # active_page ile karşılaştırılır
    path: str  # "/oneriler"
    label_key: str  # src/localization/catalog.py'deki çeviri anahtarı
    icon: str  # ikon sprite id (Faz 3'te _icons.html'e eklenecek)
    mobile: bool = True  # mobil alt tab bar'da görünür mü (rail 7 öğe tolere eder, tab bar 5)


NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem("anasayfa", "/anasayfa", "nav.anasayfa", "icon-home", mobile=True),
    NavItem("takvim", "/takvim", "nav.takvim", "icon-calendar", mobile=True),
    NavItem("oneriler", "/oneriler", "nav.oneriler", "icon-inbox", mobile=True),
    NavItem("hesaplar", "/hesaplar", "nav.hesaplar", "icon-mail", mobile=True),
    NavItem("kurallarim", "/kurallarim", "nav.kurallarim", "icon-flag", mobile=False),
    NavItem("duzeltmelerim", "/duzeltmelerim", "nav.duzeltmelerim", "icon-undo", mobile=False),
    NavItem("ayarlar", "/ayarlar", "nav.ayarlar", "icon-settings", mobile=False),
)
