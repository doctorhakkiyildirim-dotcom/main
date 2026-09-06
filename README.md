# Swing Sinyal Botu — uzun vadeli kripto sinyal ve işlem botu

Komut satırından (CMD) çalışan, **3–10 günlük trendleri** yakalamak için tasarlanmış
bir kripto botu. Telegram gerekmez; bir PC'yi açık bırakıp bota devredebilirsin.

Her işlem, **daha açılmadan önce** şunlara sahiptir:

* kesin bir **stop-loss**,
* kesin bir **kâr al** hedefi,
* 1R kâra geçince **başa baş**a çekilen, sonra **ATR ile iz süren (trailing)** stop —
  yani kâr büyüdükçe stop yukarı taşınır, "kârdan zarara dönme" engellenir,
* stop mesafesinden **hesaplanan** kaldıraç (keyfi seçilmez),
* ve o işlemin **komisyon/kâr oranı** kontrolü — masrafı çıkarmayan işlem açılmaz.

---

## 0. Önce dürüst konuşalım (bunu atlamadan oku)

Sen "beni kazandıracak, sürekli kâra geçirecek bir sistem" istedin. Böyle bir şey yok —
ne bende ne başkasında. Sürekli kazandıran bir formül olsaydı kimse satmaz, kullanırdı.
Bu botun gerçekten yaptığı şey şu: **disiplini otomatikleştirir.** Duygusal karar vermez,
stop'u kaldırmaz, zarara ortalama yapmaz, her işleme aynı risk kuralını uygular. İnsanları
batıran şeylerin çoğu bunlar. Ama piyasa yatay giderse bu bot da para kaybeder.

Üç şeyi baştan söylüyorum, çünkü sonra sürpriz olmasın:

**1) 5 dolar ile başlamak matematiksel olarak çok zor.** Sebebi risk iştahın değil,
borsanın alt limitleri. Binance vadeli işlemlerde en küçük pozisyon ~5 USDT'dir. 5 USDT
sermayen varsa, tek bir pozisyon zaten tüm hesabın demektir; %7'lik normal bir stop bile
sermayenin %7'sinden fazlasını götürür ve manevra alanın kalmaz. Bot bunu sana yalan
söylemek yerine ekrana yazar ve o işlemi açmaz.

**2) "İşlem başına 3 dolar ücret" rakamı normal değil ve sistemi tek başına öldürür.**
Binance USDT vadelide taker komisyonu **%0.05**'tir. 3 dolar komisyon ödemen için
**6.000 dolarlık** pozisyon açman gerekir. 5 dolarlık hesapta gidiş-dönüş 6 dolar masraf
demek, hesabın tamamından fazlası demek — hiçbir strateji bunu kurtaramaz.
Bu 3 dolar büyük ihtimalle şunlardan biri:

| Muhtemel sebep | Nasıl anlarsın | Çözüm |
|---|---|---|
| Kaldıraçlı büyük pozisyon açıyorsun (komisyon **hacim** üzerinden) | 3 $ / %0.05 = 6.000 $ hacim | Normal, sorun yok — oran zaten %0.05 |
| **Funding (fonlama) ücreti** ödüyorsun | 8 saatte bir kesiliyor | Uzun vadede önemli; pozisyonu funding'e karşı yönde tut |
| **Para çekme (withdraw)** ücreti | Sadece çekerken | İşlemle ilgisi yok, biriktir sonra çek |
| Aracı kurum / kopya-işlem platformu sabit ücret alıyor | Her işlemde sabit 3 $ | **Binance'e doğrudan geç**, yoksa bu iş yürümez |

Bot bu durumu ölçüyor: `config.yaml` içine `fees.flat_fee_usd: 3.0` yazarsan, sana
"bu ücret yapısıyla anlamlı en küçük pozisyon **~116 USDT**" der ve altındaki işlemleri
reddeder. Bu bir arıza değil, seni korumasıdır.

**3) Bu bot varsayılan olarak GERÇEK PARA KULLANMAZ.** `paper` (kâğıt) modunda başlar,
sanal işlem yapar. Gerçek paraya geçmek için ayarı bilerek değiştirmen ve onay yazman
gerekir. Önce en az **1 ay** kâğıt modda çalıştır, sonuçlara bak, ondan sonra karar ver.

---

## 1. Kurulum (Windows / Linux / Mac)

Python 3.10+ gerekir. [python.org](https://www.python.org/downloads/) — kurulumda
**"Add Python to PATH"** kutusunu işaretle.

```cmd
git clone https://github.com/doctorhakkiyildirim-dotcom/main.git swingbot
cd swingbot

python -m venv .venv
.venv\Scripts\activate            :: Linux/Mac:  source .venv/bin/activate

pip install -r requirements.txt
copy config.example.yaml config.yaml    :: Linux/Mac:  cp config.example.yaml config.yaml
```

Hemen doğrula — bu komut borsaya bağlanmadan tüm mantığı test eder:

```cmd
python run.py selftest
```

---

## 2. Binance API anahtarı nasıl alınır

`scan` ve `paper` modları için **anahtar gerekmez** (fiyat verisi herkese açık).
Anahtar sadece gerçek emir göndermek için lazım.

### 2a. Önce TESTNET (sahte para — burada başla)

1. https://testnet.binancefuture.com adresine gir.
2. GitHub veya Google hesabınla giriş yap.
3. Sayfanın altındaki **API Key** bölümünden `API Key` ve `Secret Key`'i kopyala.
4. Testnet hesabına otomatik sahte USDT yüklenir.
5. `config.yaml` içinde `testnet: true` kalsın.

### 2b. Sonra gerçek hesap

1. Binance → sağ üst profil → **API Management** → **Create API**.
2. **System generated** seç, bir isim ver (örn. `swingbot`).
3. Doğrulamaları geç. **Secret Key sadece bir kez gösterilir** — kaydet.
4. Anahtarı düzenle ve şu izinleri ayarla:
   * ✅ **Enable Reading**
   * ✅ **Enable Futures**
   * ❌ **Enable Withdrawals** — bunu **ASLA açma**. Bot para çekmez; açarsan anahtarı
     çalan kişi paranı çeker.
   * ✅ **Restrict access to trusted IPs only** → PC'nin IP'sini ekle (çok önemli).
5. `config.yaml` içinde `testnet: false` yap.

### 2c. Anahtarı nereye yazacaksın

**Dosyaya yazma.** Ortam değişkeni kullan — bot oradan okur:

```cmd
:: Windows (kalıcı — sonra CMD'yi kapat/aç)
setx BINANCE_API_KEY "buraya_api_key"
setx BINANCE_API_SECRET "buraya_secret_key"
```

```bash
# Linux / Mac (~/.bashrc veya ~/.zshrc içine ekle)
export BINANCE_API_KEY="buraya_api_key"
export BINANCE_API_SECRET="buraya_secret_key"
```

`config.yaml` ve `state/` klasörü `.gitignore` içinde — yanlışlıkla GitHub'a gitmez.

---

## 3. Komutlar

```cmd
python run.py doctor      :: ayarları, bağlantıyı ve ücret mantığını kontrol et
python run.py selftest    :: borsaya bağlanmadan kodu test et
python run.py backtest    :: geçmiş veriyle stratejiyi test et
python run.py scan        :: tek seferlik tarama, sinyalleri göster
python run.py run         :: sürekli çalıştır (asıl kullanım — PC'yi açık bırak)
python run.py status      :: açık pozisyonlar ve performans
python run.py close BTC/USDT   :: bir pozisyonu elle kapat
python run.py resume      :: günlük zarar durdurmasını kaldır
```

Faydalı seçenekler:

```cmd
python run.py backtest --symbols BTC/USDT ETH/USDT SOL/USDT --bars 4000 --trades 20
python run.py backtest --csv veri\BTCUSDT_4h.csv        :: kendi verinle
python run.py scan --symbols BTC/USDT --mode signal
python run.py run --mode signal                         :: sadece uyarı ver, işlem açma
```

### Önerilen sıra

```
1. python run.py doctor        → ayarlar sağlam mı?
2. python run.py backtest      → strateji geçmişte ne yapmış?
3. python run.py run --mode signal    → 1-2 hafta sadece sinyalleri izle
4. python run.py run           → paper modda 1 ay
5. testnet'te live             → 2 hafta
6. gerçek para, küçük miktarla
```

Bu 6 adımı atlama. Adım 2'de sonuç kötüyse, gerçek parada da kötü olur.

---

## 4. Strateji nasıl çalışıyor

**İki zaman dilimi.** Günlük (`1d`) grafik yönü belirler, 4 saatlik (`4h`) grafik tetiği
verir. Günlük yön yukarı değilse bot LONG'a bakmaz — trende karşı işlem açmaz.

**Giriş için hepsinin aynı anda sağlanması gerekir (LONG için):**

| Koşul | Neden |
|---|---|
| Günlük: EMA50 > EMA200 **ve** fiyat EMA200 üstünde | Ana trend gerçekten yukarı |
| 4h: EMA20 > EMA50, fiyat EMA50 üstünde | Kısa vade de aynı yönde |
| 4h: MACD sinyal çizgisini son 3 barda yukarı kesmiş | Tetik taze — geç kalmış sinyale girme |
| 4h: RSI 45–70 arası | Ne zayıf ne aşırı alım (tepeden almayalım) |
| 4h: ADX > 20 | Piyasa gerçekten trendde, yatay değil |
| Hacim ≥ 20 barlık ortalamanın %80'i | Boş hareket değil |

SHORT için hepsi tersine çalışır. Tüm koşullar geçen adaylar **0–100 arası puanlanır**
(trend gücü, üst zaman dilimi eğimi, MACD ivmesi, RSI konumu, hacim, sinyal tazeliği) ve
bot en yüksek puanlıyı seçer. Puan `min_score` altındaysa işlem yok.

**Çıkış — dört yoldan biriyle:**

1. **Stop-loss:** giriş ∓ 2.5 × ATR (dar/geniş sınırları: %2–%12 arası).
2. **Kâr al:** riskin 3 katı (3R). Yani 1 kaybedip 3 kazanırsan başa baştan iyisin.
3. **İz süren stop:** 1R kâra ulaşınca stop **başa baş**a (komisyon dahil) çekilir —
   o andan sonra o işlem sana para kaybettiremez. Sonra fiyatın gördüğü en iyi
   seviyeden 3 × ATR geride takip eder. **Stop asla geri gitmez.**
4. **Süre stopu:** 42 bar (≈7 gün) sonunda hâlâ 0.3R'nin altındaysa çıkar — para
   ölü işlemde beklemesin. Ayrıca trend tamamen dönerse (EMA + MACD birlikte) çıkar.

**Kaldıraç seçilmez, hesaplanır.** Sıra şu: riske edeceğin tutar → stop mesafesi →
pozisyon büyüklüğü → gereken kaldıraç. Sonra üç tavandan en düşüğü uygulanır:
config'deki `max_leverage`, borsanın izni ve **likidasyon güvenlik payı**. Sonuncusu
kritik: stop, likidasyon fiyatından **çok önce** tetiklenmek zorunda. Yoksa stop'un
hiçbir anlamı kalmaz.

**Masraf kapısı.** Her plan için beklenen brüt kâr, toplam masrafın (komisyon + kayma,
gidiş-dönüş) **en az 4 katı** olmalı. Değilse işlem açılmaz. Senin "3 dolar ücret"
derdinin sistemdeki karşılığı tam olarak budur — ve bu yüzden bot kısa vadeli işlem
yapmaz.

---

## 5. Ayarlar — nereye dokunacaksın

Tamamı `config.yaml` içinde, hepsi yorumlu. En çok işine yarayacaklar:

| Ayar | Ne yapar | Öneri |
|---|---|---|
| `risk.equity_usd` | Sermaye | Gerçek rakamı yaz |
| `risk.risk_per_trade_pct` | İşlem başına risk | Küçük hesapta %20, **büyüdükçe %2–5'e indir** |
| `risk.max_open_positions` | Aynı anda kaç pozisyon | 5 $ ile 1 |
| `risk.max_leverage` | Kaldıraç tavanı | Başlangıçta 5, asla 10 üstü |
| `risk.take_profit_r` | Kâr hedefi (R cinsi) | 3.0 (masrafı çıkarabilmek için 2'nin altına inme) |
| `risk.trail_atr_mult` | İz süren stop mesafesi | Küçük = erken çıkarsın, büyük = daha çok geri verirsin |
| `strategy.min_score` | Sinyal kalitesi eşiği | Az ama iyi sinyal istiyorsan 70–75 |
| `strategy.allow_short` | SHORT açsın mı | Sadece yükselişte işlem istiyorsan `false` |
| `timeframes.signal` | Sinyal grafiği | Daha uzun vade için `1d` (o zaman `trend: 1w`) |
| `fees.flat_fee_usd` | Sabit işlem ücreti | **3 dolar gerçekse buraya 3.0 yaz** |
| `execution.mode` | `signal` / `paper` / `live` | `live`'a en son geç |

**`risk_per_trade_pct: 20` ne demek:** üst üste 3 kayıpta sermayenin ~%50'si gider.
Bu, küçük hesapta borsanın alt limitleri yüzünden mecburi bir tercih. Hesap 100–200 $
seviyesine geldiğinde bunu **%2–5'e indir**; asıl kalıcı olmanın yolu bu.

---

## 6. Senden ne lazım

Aşağıdakileri bana ilet, ayarları sana göre kalibre edip botu güncelleyeyim:

**Mutlaka lazım olanlar**

1. **Bu 3 dolar tam olarak nedir?** Bir işlem yaptığın ekranın veya işlem geçmişindeki
   ücret satırının ekran görüntüsü en iyisi. Şunu netleştirmemiz gerekiyor: komisyon mu,
   funding mi, para çekme ücreti mi, yoksa aracı kurum sabit ücreti mi? **Cevabına göre
   sistemin çalışıp çalışamayacağı belli oluyor** — en kritik bilgi bu.
2. **Hangi borsayı/platformu kullanıyorsun?** Binance mi, yoksa arada bir uygulama mı var?
   (ccxt 100+ borsa destekliyor, `exchange.id` değiştirmen yeterli.)
3. **Vadeli (futures) mi, spot mu?** Kaldıraç sadece vadelide var.
4. **Gerçek sermaye ne kadar ve ne kadarını kaybetmeyi göze alıyorsun?** 5 $ test için
   uygun, kâr için değil.

**İşe yarayacak olanlar**

5. Hangi coinlerle çalışmak istersin? (Otomatik en likit 40 coin taranıyor; sen liste
   vermek istersen `universe.mode: manual` yaparız.)
6. Aynı anda kaç pozisyon açılsın?
7. SHORT açılsın mı, yoksa sadece yükselişte mi işlem yapalım?
8. Ne sıklıkta uyarı istersin — ayda 2–3 çok seçici sinyal mi, haftada birkaç tane mi?
9. Telefonuna bildirim istersen: Telegram bot token + chat id (BotFather'dan 2 dakika).
   Kod hazır, `notify.telegram.enabled: true` yapman yeterli.

**API anahtarını bana GÖNDERME.** Ne bana, ne kimseye. Sadece kendi PC'ndeki ortam
değişkenine yaz. Ben anahtar olmadan da her şeyi yapabiliyorum.

---

## 7. Botu sürekli çalışır tutmak

```cmd
cd swingbot
.venv\Scripts\activate
python run.py run
```

Pencereyi açık bırak. Bot her 5 dakikada bir kontrol eder ama **sadece kapanmış 4h
barlarda** karar verir — yani günde ~6 gerçek karar anı vardır. Ctrl+C ile durdurursan
açık pozisyonlar `state/state.json` içinde saklanır; tekrar başlattığında iz süren
stop'lar dahil kaldığı yerden devam eder.

PC kapanırsa: **`live` modda borsadaki stop ve kâr al emirleri borsada durmaya devam
eder**, bot kapalıyken de seni korur. Sadece iz süren stop güncellenmez (stop en son
bıraktığı yerde kalır). Bu, botun kasten böyle tasarlanmış tarafı.

Windows'ta otomatik başlatmak için `baslat.bat` oluştur:

```bat
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate
python run.py run
pause
```

---

## 8. Sorun giderme

| Belirti | Sebep / çözüm |
|---|---|
| `config.yaml bulunamadi` | `copy config.example.yaml config.yaml` |
| `Baglanti hatasi` | İnternet, güvenlik duvarı veya bölge kısıtı. Binance erişimini kontrol et |
| `Masraf cok agir: ...` | Beklenen davranış. `fees.flat_fee_usd`'yi düşür ya da sermayeyi artır |
| `Sermaye yetersiz` | Borsanın min pozisyon limitinin altındasın |
| `Gereken kaldirac Nx, guvenli tavan Mx` | Stop çok geniş. `atr_stop_mult` düşür ya da `max_stop_pct` daralt |
| Hiç sinyal gelmiyor | Normal. `min_score`'u 50'ye indir veya `--mode signal` ile izle |
| `API anahtari gerekli` | `live` modu için ortam değişkenlerini ayarla |
| `Bot durduruldu: gunluk zarar` | Koruma devrede. `python run.py resume` |
| Testler | `pip install pytest && python -m pytest` |

Temiz başlangıç için `state/state.json` dosyasını sil (açık pozisyon varken **silme**).

---

## 9. Proje yapısı

```
bot/
  config.py       ayar yükleme + doğrulama
  indicators.py   EMA, RSI, MACD, ATR, ADX (saf pandas)
  strategy.py     çok zaman dilimli sinyal mantığı + puanlama
  risk.py         pozisyon boyutu, kaldıraç, stop/TP, iz süren stop, masraf kapısı
  exchange.py     ccxt sarmalayıcı (veri + emirler)
  engine.py       ana döngü
  backtest.py     geçmişe dönük test (aynı strateji kodu)
  datafeed.py     CSV ve sentetik veri
  state.py        kalıcı durum (JSON)
  notifier.py     CMD çıktısı, log, ses, opsiyonel Telegram
  cli.py          komut satırı
tests/            97 test
```

Backtest, canlı botla **aynı** strateji ve risk kodunu çağırır — bu yüzden test sonucu
ile canlı davranış birbirini tutar. Gelecek verisi kullanılmaz: sinyal kapanmış barda
üretilir, giriş bir sonraki barın açılışında yapılır, bir bar hem stop hem hedefi
gördüyse **stop** kabul edilir (kötümser varsayım).

---

## 10. Uyarı

Bu yazılım eğitim ve araştırma amaçlıdır, yatırım tavsiyesi değildir. Kaldıraçlı kripto
işlemleri paranın tamamını kaybettirebilir. Yazar hiçbir zarardan sorumlu değildir.
Gerçek para ile çalıştırmadan önce testnet ve kâğıt modda uzun süre dene, ve
**kaybetmeyi göze alamayacağın parayı kullanma.**
