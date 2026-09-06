"""config.yaml uzerinde YORUMLARI KORUYARAK ayar degistirme.

Dosyayi YAML olarak okuyup geri yazmak butun aciklama satirlarini siler;
kullanicinin ayar dosyasi okunmaz hale gelir. Bu yuzden degisiklik, hedef
satirin uzerinde metin duzeyinde yapilir.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class ConfigEditError(Exception):
    """Ayar satiri bulunamadi veya degistirilemedi."""


def format_value(value: Any) -> str:
    """Python degerini YAML yazimina cevir."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    # Yorum isareti veya bosluk iceren metinler tirnaklanmali
    if text == "" or re.search(r"[#:]|^\s|\s$", text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def set_scalar(text: str, section: str, key: str, value: Any) -> str:
    """`section:` altindaki `key:` satirinin degerini degistir, yorumu koru.

    Yalnizca sayi/dogruluk/kisa metin gibi tek satirlik degerler icindir;
    liste veya ic ice sozluk degistirmez.
    """
    lines = text.splitlines(keepends=True)
    formatted = format_value(value)
    in_section = False

    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        ending = line[len(body):] or "\n"

        if not in_section:
            if re.match(rf"^{re.escape(section)}\s*:", body):
                in_section = True
            continue

        # Girintisiz yeni bir satir geldiyse bolum bitmistir
        if body and not body[0].isspace() and not body.lstrip().startswith("#"):
            break

        match = re.match(rf"^(\s+){re.escape(key)}\s*:(.*)$", body)
        if match:
            indent, rest = match.group(1), match.group(2)
            comment_match = re.search(r"\s+#.*$", rest)
            comment = comment_match.group(0) if comment_match else ""
            lines[i] = f"{indent}{key}: {formatted}{comment}{ending}"
            return "".join(lines)

    raise ConfigEditError(f"'{section}.{key}' ayari dosyada bulunamadi.")


def apply_changes(path: str | Path, changes: dict[tuple[str, str], Any]) -> list[str]:
    """Bir grup ayari dosyaya yaz. Degisen satirlarin ozetini dondurur.

    Once tum degisiklikler bellekte uygulanir; biri basarisiz olursa dosyaya
    hic dokunulmaz.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    summary: list[str] = []

    for (section, key), value in changes.items():
        updated = set_scalar(text, section, key, value)
        if updated != text:
            summary.append(f"{section}.{key} = {format_value(value)}")
        text = updated

    path.write_text(text, encoding="utf-8")
    return summary


def read_scalar(text: str, section: str, key: str) -> str | None:
    """Mevcut degeri ham metin olarak oku (gostermek icin)."""
    in_section = False
    for line in text.splitlines():
        body = line.rstrip()
        if not in_section:
            if re.match(rf"^{re.escape(section)}\s*:", body):
                in_section = True
            continue
        if body and not body[0].isspace() and not body.lstrip().startswith("#"):
            break
        match = re.match(rf"^\s+{re.escape(key)}\s*:(.*)$", body)
        if match:
            return re.sub(r"\s+#.*$", "", match.group(1)).strip()
    return None
