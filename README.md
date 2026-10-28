# Zehnur Social

ZehnurFavors için otomatik sosyal medya paylaşımı. Kodu Claude yazdı, GitHub Actions çalıştırıyor.

## Ne yapıyor?

| İş | Ne zaman | Nasıl |
|---|---|---|
| **Pinterest pini** | 6 saatte bir | Claude sıradaki ürünü seçer, başlık ve açıklama yazar, FLUX ürünü dikey bir sahneye yerleştirir, pin Etsy linkiyle atılır. |
| **Instagram Reels** | Her gün 18:23 (İstanbul) | FLUX 9:16 sahne görseli üretir, Wan 2.2 bunu 5 saniyelik videoya çevirir, kanca yazısı videoya basılır, Claude açıklama ve hashtag yazar. |

Ürün listesi `content_bank.json` dosyasında. Mevsimi geçen ürünler (Fall, Thanksgiving) Kasım sonundan sonra kendiliğinden atlanır.

## Reels onay modu

Varsayılan mod **önizleme**: video hazırlanır, **https://zehriofficedekor-rgb.github.io/zehnur-social/** sayfasında görünür ama paylaşılmaz.

- Beğendiysen: **Actions → Instagram Reels → Run workflow → `publish`**
- Beğenmediysen: **Actions → Instagram Reels → Run workflow → `discard`** (ertesi gün yenisi üretilir)
- Onaysız otomatik paylaşım için: **Settings → Secrets and variables → Actions → Variables** altında `IG_MODE` = `auto`

## Gizli anahtarlar (Settings → Secrets and variables → Actions)

**Secrets**
- `ANTHROPIC_API_KEY`: Claude
- `REPLICATE_API_TOKEN`: görsel ve video
- `IG_ACCESS_TOKEN`: Instagram (süresiz sayfa anahtarı)
- `PINTEREST_ACCESS_TOKEN`: Pinterest onayı gelince

**Variables**
- `IG_USER_ID` = `17841423562721220`
- `PINTEREST_BOARD_ID`: Pinterest onayı gelince
- `IG_MODE`: `preview` (varsayılan) veya `auto`
- `USE_AI_PIN_IMAGE`: `1` (AI sahne) veya `0` (Etsy fotoğrafı)

Anahtarlar bu repoda **hiçbir zaman** dosya olarak durmaz.
