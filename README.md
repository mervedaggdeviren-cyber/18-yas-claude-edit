# 18 yaşında olsaydım — Reels kurgusu

"18 yaşında olsaydım kendime vereceğim 5 tavsiye" videosunun Instagram Reels
kurgusunu üreten betikler.

## Dosyalar

- `edit/transcript_tr.json`: Kelime düzeyinde zaman damgalı Türkçe transkript.
  Whisper-small ile çıkarıldı, ardından elle düzeltildi.
- `tools/make_plan.py`: Kurgu kararlarını içerir: kısaltılan duraklamalar,
  kadraj değişimleri, tavsiye kartları, altyazı satırları ve vurgulanan
  kelimeler. Bunlardan `plan.json` üretir.
- `tools/render.py`: `plan.json`'ı okuyup 1080×1920 boyutunda bir MP4 üretir.
  Renk düzeltmesi, ses temizliği (-14 LUFS) ve ASS katmanları (başlık, 1/5
  kartları, altyazılar, son ekran) bu adımda uygulanır.

## Yeniden üretmek

```sh
python3 tools/make_plan.py kaynak.mp4 plan.json
python3 tools/render.py plan.json reels.mp4            # final (x264 slow, CRF 18)
python3 tools/render.py plan.json taslak.mp4 --preview # hızlı önizleme
```

Gereksinimler: ffmpeg (libass ve libx264 ile derlenmiş), Python 3, Pillow ve
Inter yazı tipi.
