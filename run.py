#!/usr/bin/env python3
"""Botun giris noktasi.

Kullanim:
    python run.py doctor          # ayarlari ve baglantiyi kontrol et
    python run.py selftest        # borsaya baglanmadan kod testi
    python run.py backtest        # gecmis veriyle strateji testi
    python run.py scan            # tek seferlik tarama
    python run.py run             # surekli calistir
    python run.py status          # acik pozisyonlar
"""

import sys

from bot.cli import main

if __name__ == "__main__":
    sys.exit(main())
