"""OAuth token dosyalarının isteğe bağlı, şeffaf şifrelenmesi (PRIV-01'in
token yarısı, bkz. docs/urunlesme-ve-tasarim-yol-haritasi.md).

``TOKEN_ENCRYPTION_KEY`` ortam değişkeni (bkz. .env.example) ayarlanırsa
``data/google_token_*.json``/``data/ms_token_*.json`` diske Fernet
(simetrik, kimlik doğrulamalı şifreleme) ile yazılır/okunur; ayarlanmazsa
(bugünkü yerel geliştirme varsayılanı) davranış DEĞİŞMEZ — düz metin,
MVP'den beri olduğu gibi. Yalnızca bir sunucuya (VPS vb.) dağıtılırken
zorunlu tutulması bekleniyor — localhost'ta çalışan tek kullanıcı için
şifreleme asıl faydayı "çalınan bir data/ yedeği" senaryosunda sağlar.

Anahtar `data/` dizininin DIŞINDA (.env, git'e dahil değil) tutulmalı —
aksi halde çalınan bir `data/` yedeği anahtarı da taşır, korumanın anlamı
kalmaz. Üretmek için: ``python -m src.core.token_crypto``.

Mevcut düz-metin dosyalar OTOMATİK geçiş yapar: bir anahtar sonradan
ayarlanırsa ilk okuma denemesi şifre çözmeyi dener, format uyuşmazsa
(henüz şifrelenmemiş eski dosya) düz metin olarak ele alınır; bir sonraki
kaydetmede (zaten her token yenilemesinde olduğu gibi, elle bir adım
gerekmeden) dosya şifreli yazılır."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from src.core.logging_config import get_logger

logger = get_logger("token_crypto")

_warned_missing_key = False


def _get_fernet() -> Fernet | None:
    key = os.environ.get("TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        global _warned_missing_key
        if not _warned_missing_key:
            logger.warning(
                "TOKEN_ENCRYPTION_KEY ayarlı değil — OAuth token dosyaları düz metin "
                "saklanıyor. Yerel geliştirmede zararsız; bir sunucuya dağıtımda "
                "mutlaka ayarlayın (bkz. .env.example, `python -m src.core.token_crypto`)."
            )
            _warned_missing_key = True
        return None
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as e:
        # Yanlış biçimli bir anahtarla SESSİZCE düz metne düşmek daha kötü
        # bir hata (kullanıcı şifrelendiğini SANIR) — burada açıkça patlıyor.
        raise RuntimeError(
            f"TOKEN_ENCRYPTION_KEY geçersiz (Fernet anahtarı olmalı): {e}"
        ) from e


def encrypt_for_storage(plaintext: str) -> str:
    """Anahtar ayarlıysa şifreler, değilse veriyi OLDUĞU GİBİ döner."""
    fernet = _get_fernet()
    if fernet is None:
        return plaintext
    return fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_from_storage(stored: str) -> str:
    """Anahtar varsa şifre çözmeyi dener; format uyuşmazsa (henüz
    şifrelenmemiş eski bir dosya) ya da anahtar hiç yoksa veriyi OLDUĞU
    GİBİ döner — bkz. modül docstring'indeki otomatik geçiş notu."""
    fernet = _get_fernet()
    if fernet is None:
        return stored
    try:
        return fernet.decrypt(stored.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return stored


if __name__ == "__main__":
    print(Fernet.generate_key().decode("ascii"))
