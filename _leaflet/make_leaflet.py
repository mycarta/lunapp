import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from scipy import ndimage
import qrcode
from qrcode.constants import ERROR_CORRECT_M

DPI = 300
MM = DPI / 25.4
W, H = round(148 * MM), round(210 * MM)            # A5 portrait: 1748 x 2480
BG = (250, 248, 245)                               # #FAF8F5
INK = (58, 54, 49)                                 # #3a3631
MUTED = (138, 131, 120)                            # #8a8378
PEN = (40, 37, 33)                                 # ink-drawing tone

GEO   = "/c/Windows/Fonts/georgia.ttf"
GEO_B = "/c/Windows/Fonts/georgiab.ttf"
GEO_I = "/c/Windows/Fonts/georgiai.ttf"
SYM   = "/c/Windows/Fonts/seguisym.ttf"            # has U+2764 heart; Georgia does not

canvas = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(canvas)


def f(path, px):
    return ImageFont.truetype(path, px)


def th(text, fnt):
    bb = draw.textbbox((0, 0), text, font=fnt)
    return bb[3] - bb[1]


def ctext(cx, y, text, fnt, fill):
    bb = draw.textbbox((0, 0), text, font=fnt)
    w = bb[2] - bb[0]
    draw.text((cx - w // 2 - bb[0], y - bb[1]), text, font=fnt, fill=fill)
    return y + (bb[3] - bb[1])


def mixed_centered(cx, top_y, segments, fill, base_fnt):
    """Draw [(text, font), ...] as one baseline-aligned, horizontally centered line."""
    widths = [draw.textlength(t, font=fn) for t, fn in segments]
    baseline = top_y + base_fnt.getmetrics()[0]
    x = cx - sum(widths) / 2
    for (t, fn), w in zip(segments, widths):
        draw.text((x, baseline), t, font=fn, fill=fill, anchor="ls")
        x += w
    bb = draw.textbbox((0, 0), "Made with in Lunenburg", font=base_fnt)
    return top_y + (bb[3] - bb[1])


def muted(img, gray=0.70, opacity=0.60, bg=BG):
    g = ImageOps.grayscale(img).convert("RGB")
    desat = Image.blend(img, g, gray)
    return Image.blend(Image.new("RGB", img.size, bg), desat, opacity)


# ---- process F.png: despeckle, tight crop, strip white -> alpha logo ----
fL = np.asarray(Image.open("F.PNG").convert("L"))
ink = fL < 170
lbl, n = ndimage.label(ink)
sizes = ndimage.sum(np.ones_like(lbl), lbl, range(1, n + 1))
keep = np.isin(lbl, [i + 1 for i, s in enumerate(sizes) if s >= 30])
ys, xs = np.where(keep)
y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
pad = 6
gray = fL[max(0, y0 - pad):y1 + pad + 1, max(0, x0 - pad):x1 + pad + 1].astype(float)
alpha = np.clip((240 - gray) / (240 - 100), 0, 1)            # white->0, ink->1
logo = np.zeros((*gray.shape, 4), np.uint8)
logo[..., 0], logo[..., 1], logo[..., 2] = PEN
logo[..., 3] = (alpha * 255).astype(np.uint8)
logo = Image.fromarray(logo, "RGBA")
logo_aspect = logo.width / logo.height

# ---- geometry ----
m = round(11 * MM)
top_m, bot_m = round(7 * MM), round(8 * MM)
content_w = W - 2 * m
cx = W // 2

# ---- title row: F-logo + "ingerpost", baseline-aligned, large ----
title_f = f(GEO, round(15 * MM))
logo_h = round(26 * MM)
logo_w = round(logo_h * logo_aspect)
logo_r = logo.resize((logo_w, logo_h), Image.LANCZOS)
asc, desc = title_f.getmetrics()
gap_lt = round(1 * MM)
text = "ingerpost"
text_w = round(draw.textlength(text, font=title_f))
total_w = logo_w + gap_lt + text_w
start_x = (W - total_w) // 2
baseline = top_m + logo_h
logo_dx, logo_dy = round(5 * MM), round(5.5 * MM)   # nudge toward "ingerpost"
canvas.paste(logo_r, (start_x + logo_dx, baseline - logo_h + logo_dy), logo_r)
draw.text((start_x + logo_w + gap_lt, baseline), text, font=title_f, fill=INK, anchor="ls")
title_bottom = baseline + desc

# ---- caption fonts ----
cap1_f = f(GEO_B, round(6.5 * MM))
cap2_f = f(GEO,   round(4.4 * MM))
cap3_f = f(GEO_I, round(4.4 * MM))
url_f  = f(GEO_I, round(3.4 * MM))
mb_f   = f(GEO,   round(3.2 * MM))
feat_f = f(GEO_I, round(3.3 * MM))
made_f = f(GEO_I, round(3.3 * MM))
heart_f = f(SYM, round(3.3 * MM))
FEAT = "free · no ads · no app download · works on any phone"
MADE = [("Made with ", made_f), ("❤", heart_f), (" in Lunenburg", made_f)]

cap1_h = th("Lunenburg & area events", cap1_f)
cap2_h = th("for the next two weeks", cap2_f)
cap3_h = th("updated daily", cap3_f)
url_h, mb_txt_h, feat_h = th("lunenburg.fingerpost.ca", url_f), th("Mahone Bay — coming soon", mb_f), th(FEAT, feat_f)

# ---- spacing (mm) ----
g_ti, g_img_cap, g1, g2 = 5, 6, 4, 3
g_cap_row, g_item_txt, g_row_made, g_made_feat = 9, 5, 7, 3
qr_size = round(32 * MM)                            # >= 30mm
row_text_h = max(url_h, mb_txt_h)
row_h = qr_size + round(g_item_txt * MM) + row_text_h
made_h = th("Made with in Lunenburg", made_f)

content_top = title_bottom + round(g_ti * MM)
fixed = round(g_img_cap * MM) + cap1_h + round(g1 * MM) + cap2_h + round(g2 * MM) + cap3_h \
        + round(g_cap_row * MM) + row_h + round(g_row_made * MM) + made_h \
        + round(g_made_feat * MM) + feat_h
img_h = (H - bot_m - content_top) - fixed

# ---- Lunenburg watercolor ----
art = Image.open("banner_lunenburg.png").convert("RGB")
scale = min(content_w / art.width, img_h / art.height)
aw, ah = round(art.width * scale), round(art.height * scale)
art = art.resize((aw, ah), Image.LANCZOS)

y = content_top
canvas.paste(art, (cx - aw // 2, y)); y += ah + round(g_img_cap * MM)
y = ctext(cx, y, "Lunenburg & area events", cap1_f, INK) + round(g1 * MM)
y = ctext(cx, y, "for the next two weeks", cap2_f, INK) + round(g2 * MM)
y = ctext(cx, y, "updated daily", cap3_f, INK) + round(g_cap_row * MM)

# ---- bottom row: QR (left) + muted Mahone thumb (right), tops aligned ----
row_y = y
left_cx = round(W * 0.34)
right_cx = round(W * 0.68)

qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=2)
qr.add_data("https://lunenburg.fingerpost.ca"); qr.make(fit=True)
qr_img = qr.make_image(fill_color=(40, 40, 40), back_color=BG).convert("RGB").resize((qr_size, qr_size), Image.NEAREST)
canvas.paste(qr_img, (left_cx - qr_size // 2, row_y))
ctext(left_cx, row_y + qr_size + round(g_item_txt * MM), "lunenburg.fingerpost.ca", url_f, MUTED)

mb = Image.open("banner_mahone_bay.jpg").convert("RGB")
mb_scale = qr_size / mb.height
mw, mh = round(mb.width * mb_scale), qr_size
mb = muted(mb.resize((mw, mh), Image.LANCZOS))
canvas.paste(mb, (right_cx - mw // 2, row_y))
ctext(right_cx, row_y + qr_size + round(g_item_txt * MM), "Mahone Bay — coming soon", mb_f, MUTED)

# ---- "Made with ❤ in Lunenburg" + bottom full-width line ----
made_y = row_y + row_h + round(g_row_made * MM)
mixed_centered(cx, made_y, MADE, MUTED, made_f)
feat_y = made_y + made_h + round(g_made_feat * MM)
ctext(cx, feat_y, FEAT, feat_f, MUTED)

print("A5 %dx%d (%.1fx%.1f mm) | logo %.0fx%.0f mm | title font %.1f mm | "
      "Lunenburg %.0fx%.0f mm | QR %.1f mm | feat ends %.1fmm/210, slack %.1fmm"
      % (W, H, W / MM, H / MM, logo_w / MM, logo_h / MM, 15,
         aw / MM, ah / MM, qr_size / MM, (feat_y + feat_h) / MM,
         (H - bot_m - (feat_y + feat_h)) / MM))

canvas.save("leaflet_lunenburg.pdf", "PDF", resolution=DPI)
canvas.resize((W // 3, H // 3), Image.LANCZOS).save("_leaflet_preview.png")
print("saved leaflet_lunenburg.pdf")
