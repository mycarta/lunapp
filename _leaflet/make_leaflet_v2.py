from PIL import Image, ImageDraw, ImageFont, ImageOps
import qrcode
from qrcode.constants import ERROR_CORRECT_M

DPI = 300
MM = DPI / 25.4
PT = DPI / 72                                       # px per typographic point
W, H = round(148 * MM), round(210 * MM)            # A5 portrait: 1748 x 2480
BG = (250, 248, 245)                               # #FAF8F5
INK = (58, 54, 49)                                 # #3a3631
MUTED = (138, 131, 120)                            # #8a8378

GEO   = "/c/Windows/Fonts/georgia.ttf"
GEO_B = "/c/Windows/Fonts/georgiab.ttf"
GEO_I = "/c/Windows/Fonts/georgiai.ttf"
SYM   = "/c/Windows/Fonts/seguisym.ttf"            # has U+2764 heart; Georgia does not

canvas = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(canvas)

SAVE_PDF = False


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


def trim_border(img, border=8):
    """Lightly trim screenshot edges for a tighter thumbnail look."""
    if img.width <= 2 * border or img.height <= 2 * border:
        return img
    return img.crop((border, border, img.width - border, img.height - border))


def force_white_background(img, dark_threshold=118):
    """Keep dark UI glyph/text, force everything else to white."""
    g = ImageOps.grayscale(img)
    return g.point(lambda p: p if p < dark_threshold else 255, mode="L").convert("RGB")


# ---- geometry ----
m = round(11 * MM)
top_m, bot_m = round(7 * MM), round(8 * MM)
content_w = W - 2 * m
cx = W // 2

# ---- title: lowercase Georgia "f" (3pt larger) + "ingerpost", baseline-aligned ----
ING_PT = 48.5                                      # whole word one size
F_DELTA_PT = 0                                     # "f" same size as the rest
title_px = round(ING_PT * PT)
f_px = round((ING_PT + F_DELTA_PT) * PT)
title_f = f(GEO, title_px)                          # "ingerpost"
big_f = f(GEO, f_px)                                # "f", larger
asc, desc = title_f.getmetrics()
asc_b, desc_b = big_f.getmetrics()
fw = draw.textlength("f", font=big_f)
iw = draw.textlength("ingerpost", font=title_f)
start_x = round((W - (fw + iw)) / 2)
baseline = top_m + asc_b
f_dx = 0                                            # uniform word, no nudge
draw.text((start_x + f_dx, baseline), "f", font=big_f, fill=INK, anchor="ls")
draw.text((start_x + fw, baseline), "ingerpost", font=title_f, fill=INK, anchor="ls")
title_bottom = baseline + max(desc, desc_b)

# ---- caption fonts ----
cap1_f = f(GEO_B, round(6.5 * MM))
cap2_f = f(GEO,   round(4.4 * MM))
cap3_f = f(GEO_I, round(4.4 * MM))
url_f  = f(GEO_I, round(3.4 * MM))
mb_f   = f(GEO,   round(3.2 * MM))
feat_f = f(GEO_I, round(3.3 * MM))
made_f = f(GEO_I, round(3.3 * MM))
heart_f = f(SYM, round(3.3 * MM))
s2hs_f = f(GEO_I, round(3.0 * MM))
FEAT = "free · no ads · no app download · works on any phone"
MADE = [("Made with ", made_f), ("❤", heart_f), (" in Lunenburg", made_f)]

cap1_h = th("Lunenburg & area events", cap1_f)
cap2_h = th("for the next two weeks", cap2_f)
cap3_h = th("updated daily", cap3_f)
url_h, mb_txt_h, feat_h = th("lunenburg.fingerpost.ca", url_f), th("Mahone Bay — coming soon", mb_f), th(FEAT, feat_f)
s2hs_h = th("then Add to Home Screen", s2hs_f)

# ---- spacing (mm) ----
g_ti, g_img_cap, g1, g2 = 5, 6, 4, 3
g_cap_row, g_item_txt, g_row_s2hs, g_s2hs_txt, g_s2hs_made, g_made_feat = 9, 5, 5, 2, 5, 3
qr_size = round(32 * MM)                            # >= 30mm
step_h = round(12 * MM)
row_text_h = max(url_h, mb_txt_h)
row_h = qr_size + round(g_item_txt * MM) + row_text_h
s2hs_block_h = step_h + round(g_s2hs_txt * MM) + s2hs_h
made_h = th("Made with in Lunenburg", made_f)

content_top = title_bottom + round(g_ti * MM)
fixed = round(g_img_cap * MM) + cap1_h + round(g1 * MM) + cap2_h + round(g2 * MM) + cap3_h \
    + round(g_cap_row * MM) + row_h + round(g_row_s2hs * MM) + s2hs_block_h \
    + round(g_s2hs_made * MM) + made_h \
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

# ---- save-to-home-screen helper row ----
s2hs_y = row_y + row_h + round(g_row_s2hs * MM)
share = trim_border(Image.open("share_icon.jpg").convert("RGB"), border=8)
add_home = trim_border(Image.open("add_home.jpg").convert("RGB"), border=8)

share_scale = step_h / share.height
sh_w = round(share.width * share_scale)
share = force_white_background(share.resize((sh_w, step_h), Image.LANCZOS))
canvas.paste(share, (left_cx - sh_w // 2, s2hs_y))
ctext(left_cx, s2hs_y + step_h + round(g_s2hs_txt * MM), "Tap Share", s2hs_f, MUTED)

add_scale = step_h / add_home.height
ah_w = round(add_home.width * add_scale)
add_home = muted(add_home.resize((ah_w, step_h), Image.LANCZOS), gray=0.55, opacity=0.80)
canvas.paste(add_home, (right_cx - ah_w // 2, s2hs_y))
ctext(right_cx, s2hs_y + step_h + round(g_s2hs_txt * MM), "then Add to Home Screen", s2hs_f, MUTED)

# ---- "Made with ❤ in Lunenburg" + bottom full-width line ----
made_y = s2hs_y + s2hs_block_h + round(g_s2hs_made * MM)
mixed_centered(cx, made_y, MADE, MUTED, made_f)
feat_y = made_y + made_h + round(g_made_feat * MM)
ctext(cx, feat_y, FEAT, feat_f, MUTED)

print("A5 %dx%d (%.1fx%.1f mm) | title 'ingerpost' %.1fpt, 'f' %.1fpt | "
      "Lunenburg %.0fx%.0f mm | QR %.1f mm | feat ends %.1fmm/210, slack %.1fmm"
      % (W, H, W / MM, H / MM, title_px / PT, f_px / PT,
         aw / MM, ah / MM, qr_size / MM, (feat_y + feat_h) / MM,
         (H - bot_m - (feat_y + feat_h)) / MM))

if SAVE_PDF:
    canvas.save("leaflet_lunenburg_v2.pdf", "PDF", resolution=DPI)
    print("saved leaflet_lunenburg_v2.pdf")

canvas.resize((W // 3, H // 3), Image.LANCZOS).save("_leaflet_v2_preview_s2hs.png")
print("saved _leaflet_v2_preview_s2hs.png")
