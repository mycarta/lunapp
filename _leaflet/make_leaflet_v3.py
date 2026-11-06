import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
import qrcode
from qrcode.constants import ERROR_CORRECT_M

DPI = 300
MM = DPI / 25.4
PT = DPI / 72
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


def f(path, px):
    return ImageFont.truetype(path, px)


def th(text, fnt):
    bb = draw.textbbox((0, 0), text, font=fnt)
    return bb[3] - bb[1]


def ctext(cx, y, text, fnt, fill):
    """Draw horizontally centered; y is the top of the glyphs. Returns bottom y."""
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


def load_icon(path, ink=INK):
    """Normalize a line-art icon to a tight ink-on-transparent RGBA glyph."""
    rgba = Image.open(path).convert("RGBA")
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    comp = np.asarray(Image.alpha_composite(white, rgba).convert("L")).astype(float)
    alpha = np.clip((225 - comp) / (225 - 60), 0, 1)          # dark strokes -> opaque
    out = np.zeros((*comp.shape, 4), np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = ink
    out[..., 3] = (alpha * 255).astype(np.uint8)
    ys, xs = np.where(out[..., 3] > 20)
    out = out[ys.min():ys.max() + 1, xs.min():xs.max() + 1]   # crop tight
    return Image.fromarray(out, "RGBA")


# ---- geometry ----
m = round(11 * MM)
top_m, bot_m = round(7 * MM), round(8 * MM)
content_w = W - 2 * m
cx = W // 2

# ---- title: "fingerpost", large Georgia, centered ----
title_f = f(GEO, round(13 * MM))
t_asc, t_desc = title_f.getmetrics()
ctext(cx, top_m, "fingerpost", title_f, INK)
title_bottom = top_m + th("fingerpost", title_f)

# ---- fonts ----
cap1_f = f(GEO_B, round(6.5 * MM))
cap2_f = f(GEO,   round(4.4 * MM))
cap3_f = f(GEO_I, round(4.4 * MM))
url_f  = f(GEO_I, round(3.4 * MM))
step_f = f(GEO,   round(3.9 * MM))                 # numbered steps, one size
mb_f   = f(GEO,   round(3.2 * MM))
made_f = f(GEO_I, round(3.3 * MM))
heart_f = f(SYM,  round(3.3 * MM))
feat_f = f(GEO_I, round(3.3 * MM))
FEAT = "free · no ads · no app download · works on any phone"
MADE = [("Made with ", made_f), ("❤", heart_f), (" in Lunenburg", made_f)]

cap1_h = th("Lunenburg & area events", cap1_f)
cap2_h = th("for the next two weeks", cap2_f)
cap3_h = th("updated daily", cap3_f)
url_h = th("lunenburg.fingerpost.ca", url_f)
mb_txt_h = th("Mahone Bay — coming soon", mb_f)
feat_h = th(FEAT, feat_f)
made_h = th("Made with in Lunenburg", made_f)

# ---- inline step icons sized to the step font ----
s_asc, s_desc = step_f.getmetrics()
icon_h = round(s_asc * 0.92)
share_ic = load_icon("share.PNG")
home_ic = load_icon("home.PNG")
share_ic = share_ic.resize((round(icon_h * share_ic.width / share_ic.height), icon_h), Image.LANCZOS)
home_ic = home_ic.resize((round(icon_h * home_ic.width / home_ic.height), icon_h), Image.LANCZOS)

STEPS = [("1)  Scan code", None),
         ("2)  Tap Share", share_ic),
         ("3)  Add Icon to Home Screen", home_ic)]
g_icon = round(1.6 * MM)
step_w = max(draw.textlength(t, font=step_f) + (g_icon + ic.width if ic else 0) for t, ic in STEPS)
step_line_h = s_asc + s_desc
step_gap = round(2.4 * MM)
steps_block_h = len(STEPS) * step_line_h + (len(STEPS) - 1) * step_gap

# ---- spacing (mm) ----
g_ti, g_img_cap, g1, g2 = 5, 6, 4, 3
g_cap_sec, g_url_qr, g_sec_mb, g_mb_made, g_made_feat = 9, 2, 9, 6, 3
qr_size = round(32 * MM)
gap_cols = round(11 * MM)
mb_thumb_h = round(22 * 2 / 3 * MM)                 # 1/3 smaller

section_h = url_h + round(g_url_qr * MM) + qr_size          # left column is the taller
content_top = title_bottom + round(g_ti * MM)
fixed = round(g_img_cap * MM) + cap1_h + round(g1 * MM) + cap2_h + round(g2 * MM) + cap3_h \
    + round(g_cap_sec * MM) + section_h \
    + round(g_sec_mb * MM) + mb_thumb_h \
    + round(g_mb_made * MM) + made_h + round(g_made_feat * MM) + feat_h
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
y = ctext(cx, y, "updated daily", cap3_f, INK) + round(g_cap_sec * MM)

# ---- two-column: url+QR (left) | numbered steps (right) ----
section_top = y
total_w = qr_size + gap_cols + step_w
group_x = round((W - total_w) / 2)
qr_left = group_x
qr_cx = qr_left + qr_size // 2
qr_top = section_top + url_h + round(g_url_qr * MM)
steps_x = qr_left + qr_size + gap_cols

qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=2)
qr.add_data("https://lunenburg.fingerpost.ca"); qr.make(fit=True)
qr_img = qr.make_image(fill_color=(40, 40, 40), back_color=BG).convert("RGB").resize((qr_size, qr_size), Image.NEAREST)
ctext(qr_cx, section_top, "lunenburg.fingerpost.ca", url_f, MUTED)
canvas.paste(qr_img, (qr_left, qr_top))

# numbered steps, vertically centered against the QR
qr_mid = qr_top + qr_size // 2
steps_top = qr_mid - steps_block_h // 2
for i, (text, ic) in enumerate(STEPS):
    ly = steps_top + i * (step_line_h + step_gap)
    baseline = ly + s_asc
    draw.text((steps_x, baseline), text, font=step_f, fill=INK, anchor="ls")
    if ic is not None:
        tw = draw.textlength(text, font=step_f)
        cap_mid = baseline - round(0.34 * s_asc)
        canvas.paste(ic, (round(steps_x + tw + g_icon), round(cap_mid - ic.height / 2)), ic)
y = qr_top + qr_size + round(g_sec_mb * MM)

# ---- small desaturated Mahone thumbnail (bottom-left) + two-line caption beside ----
mb = Image.open("banner_mahone_bay.jpg").convert("RGB")
mb_scale = mb_thumb_h / mb.height
mbw = round(mb.width * mb_scale)
mb_img = muted(mb.resize((mbw, mb_thumb_h), Image.LANCZOS))
mb_x = qr_left                                      # align under the QR (well right of before)
canvas.paste(mb_img, (mb_x, y))

mb_lines = ["Mahone Bay", "coming soon"]
mb_asc, mb_desc = mb_f.getmetrics()
mb_line_adv = mb_asc + mb_desc
mb_line_gap = round(0.6 * MM)
mb_block_h = len(mb_lines) * mb_line_adv + (len(mb_lines) - 1) * mb_line_gap
tx = mb_x + mbw + round(4 * MM)
ty0 = y + mb_thumb_h // 2 - mb_block_h // 2
for i, ln in enumerate(mb_lines):
    baseline = ty0 + i * (mb_line_adv + mb_line_gap) + mb_asc
    draw.text((tx, baseline), ln, font=mb_f, fill=MUTED, anchor="ls")
y += max(mb_thumb_h, mb_block_h) + round(g_mb_made * MM)

# ---- bottom lines, centered ----
mixed_centered(cx, y, MADE, MUTED, made_f)
y += made_h + round(g_made_feat * MM)
ctext(cx, y, FEAT, feat_f, MUTED)

print("A5 %dx%d | title %.0fpt | Lunenburg %.0fx%.0f mm | QR %.0f mm | "
      "steps_w %.0f mm | bottom ends %.1fmm/210, slack %.1fmm"
      % (W, H, round(13 * MM) / PT, aw / MM, ah / MM, qr_size / MM, step_w / MM,
         (y + feat_h) / MM, (H - bot_m - (y + feat_h)) / MM))

canvas.save("leaflet_lunenburg_v3.pdf", "PDF", resolution=DPI)
canvas.resize((W // 3, H // 3), Image.LANCZOS).save("_leaflet_v3_preview.png")
print("saved leaflet_lunenburg_v3.pdf")
