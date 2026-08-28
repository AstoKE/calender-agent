"""Account Registry (bkz. docs/architecture-plan.md §6/§12).

`accounts` tablosu, diğer tüm tabloların (email_messages, sync_states,
calendar_events_cache, ...) account_id foreign key'inin bağlandığı kayıt —
bu kayıt yoksa o tablolara hiçbir şey yazılamaz (canlı testte
`FOREIGN KEY constraint failed` hatasıyla ortaya çıktı).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from src.storage.db import get_connection
from src.storage.preferences import get_preference

MASTER_CALENDAR_PREFERENCE_KEY = "calendar.master_account_id"


def _derive_account_id(email: str) -> str:
    """E-postanın @ öncesi kısmından dosya adı/SQL için güvenli bir account_id türetir."""
    local_part = email.split("@")[0].lower()
    return re.sub(r"[^a-z0-9_-]", "_", local_part)


def list_accounts(user_id: str | None = None) -> list[dict]:
    """Kayıtlı hesapları (id, provider, email, status, connected_at) bağlanma
    sırasına göre döner. `user_id` verilirse o kullanıcıya ait hesaplar VE
    henüz kimseye ait olmayan (`user_id IS NULL`) hesaplar (bkz. src/ui/auth.py
    — Web UI'nin login katmanı, her istekte bunu geçirir); `None` ise (CLI'nın
    select_account()'ı — CLI BİLEREK login sisteminin dışında, bkz. plan)
    tüm hesaplar, sahiplik fark etmez.

    Sahipsiz hesapların da görünür kalması BİLİNÇLİ: CLI'nın kendi
    ensure_account_registered çağrıları hiçbir zaman user_id vermiyor (login
    sisteminin dışında olduğu için), yani CLI'dan eklenen hesaplar KALICI
    olarak sahipsiz kalır — bu proje tek bir yerel operatör için (bkz.
    CLAUDE.md), o operatör hem CLI hem web'i kullanabiliyor, bu yüzden
    "kimseye ait değil" burada "gerçek çok-kiracılı bir sızıntı" değil,
    "bu makinenin sahibinin henüz web'den devralmadığı kendi verisi"
    anlamına geliyor (bkz. auth.py::adopt_orphaned_data ile AYNI gerekçe)."""
    with get_connection() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT id, provider, email, status, connected_at FROM accounts "
                "WHERE user_id = ? OR user_id IS NULL ORDER BY connected_at",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, provider, email, status, connected_at FROM accounts ORDER BY connected_at"
            ).fetchall()
    return [dict(row) for row in rows]


def get_account(account_id: str) -> dict | None:
    """Tek bir hesabı döner — Outlook desteğiyle birlikte eklendi: bir
    account_id verildiğinde HANGİ connector'ın (Google mı MS mi) kurulacağını
    bilmek için `provider` alanına ihtiyaç var (bkz. src/ui/calendar_access.py,
    src/ui/routes.py::_get_calendar — ikisi de artık provider'a göre dallanıyor)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, provider, email, status, connected_at, user_id FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
    return dict(row) if row else None


def select_account() -> tuple[str, str]:
    """CLI'da kayıtlı hesaplardan birini seçtirir ya da yeni bir hesap ekletir.

    Her connector (GmailConnector/GoogleCalendarConnector) account_id'ye özel
    ayrı bir OAuth token dosyası kullandığından (bkz. google_auth.py), birden
    fazla hesap DB'de yan yana kayıtlı kalabilir; kullanıcı her çalıştırmada
    hangisiyle devam edeceğini seçer. Döner: (account_id, email).
    """
    accounts = list_accounts()
    print("\nKayıtlı hesaplar:")
    for i, acc in enumerate(accounts, start=1):
        print(f"  {i}. {acc['email']}")
    add_new_index = len(accounts) + 1
    print(f"  {add_new_index}. Yeni hesap ekle")

    choice = input("Hangi hesabı kullanmak istiyorsunuz? ").strip()
    try:
        index = int(choice)
    except ValueError:
        index = -1

    if 1 <= index <= len(accounts):
        acc = accounts[index - 1]
        return acc["id"], acc["email"]

    email = input("Eklenecek Gmail adresi: ").strip()
    account_id = _derive_account_id(email)
    ensure_account_registered(account_id, provider="google", email=email)
    return account_id, email


def resolve_write_account_id(fallback_account_id: str, user_id: str | None = None) -> str:
    """"Ana takvim hesabı" ayarlanmışsa VE hâlâ kayıtlıysa onu döner — mail
    taraması hangi hesaptan gelirse gelsin (`fallback_account_id`), onay/
    sohbet YAZMASI hep bu TEK hesaba gider (bkz. Ayarlar ekranındaki "Ana
    takvim hesabı" seçimi, `MASTER_CALENDAR_PREFERENCE_KEY`). Ayarlanmamışsa
    (varsayılan) `fallback_account_id`'nin kendisi döner — mevcut davranış
    DEĞİŞMEZ. Silinmiş/artık kayıtlı olmayan bir hesap ayarlıysa da aynı
    şekilde fallback'e düşer (sessizce, istisna fırlatmadan). `user_id`
    verilirse (Web UI — bkz. src/ui/auth.py) hem tercih hem hesap listesi o
    kullanıcıya scoped; `None` ise (CLI, login sisteminin BİLEREK dışında)
    eski, sahiplikten bağımsız davranış."""
    master_id = get_preference(MASTER_CALENDAR_PREFERENCE_KEY, user_id=user_id)
    if master_id and any(acc["id"] == master_id for acc in list_accounts(user_id=user_id)):
        return master_id
    return fallback_account_id


def ensure_account_registered(
    account_id: str,
    provider: str,
    email: str,
    account_type: str = "personal",
    user_id: str | None = None,
) -> None:
    """`accounts` tablosunda bu account_id için kayıt yoksa oluşturur
    (idempotent). `user_id` verilirse hesap o kullanıcıya bağlanır.

    Zaten kayıtlı bir hesap BAŞKA bir kullanıcıya bağlıyken tekrar
    bağlanmaya çalışılırsa (canlı testte bulundu: `ceren.kisacik24@gmail.com`
    olarak `enestugac@gmail.com`'u eklemek GERÇEK bir OAuth onayından
    geçmesine rağmen sessizce hiçbir şey yapmıyordu, çünkü hesap zaten
    başka bir kullanıcıya aitti) — sahiplik YENİ kullanıcıya DEVREDİLİR.
    Bu bilinçli: bu ekranı geçmek gerçek bir OAuth onayı gerektiriyor, yani
    o hesabı az önce kimin kontrol ettiğinin güçlü bir kanıtı — "ilk
    bağlayan sonsuza kadar sahip olur" değil "en son gerçekten onaylayan
    sahip olur" daha doğru bir varsayılan (tek yerel operatör senaryosunda
    hesapların kişiler arasında GERÇEKTEN paylaşılabildiği anlamına gelir)."""
    with get_connection() as conn:
        existing = conn.execute("SELECT user_id FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if existing:
            if user_id is not None and existing["user_id"] != user_id:
                conn.execute("UPDATE accounts SET user_id = ? WHERE id = ?", (user_id, account_id))
            return
        conn.execute(
            """
            INSERT INTO accounts (id, provider, account_type, email, connected_at, status, user_id)
            VALUES (?, ?, ?, ?, ?, 'active', ?)
            """,
            (account_id, provider, account_type, email, datetime.now(timezone.utc).isoformat(), user_id),
        )
