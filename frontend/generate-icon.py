#!/usr/bin/env python3
"""截取图标：去掉右下角水印，保留最大清晰度"""
import subprocess, os, shutil
from PIL import Image, ImageDraw

SRC = "/Users/zhanghan/Desktop/未命名.png"
OUT = "src-tauri/icons/icon.png"

img = Image.open(SRC).convert("RGBA")
w, h = img.size
print(f"原始: {w}x{h}")

# 只裁掉右下角水印区域（大约 8% 高度）
# 其余部分完整保留
left = 0
top = 0
right = w
bottom = int(h * 0.92)

cropped = img.crop((left, top, right, bottom))
cw, ch = cropped.size
print(f"裁剪: ({left},{top},{right},{bottom}) → {cw}x{ch}")

def add_rounded_corners(img, radius):
    """添加圆角"""
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], radius=radius, fill=255)
    result = Image.new("RGBA", img.size, (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    return result

# 放大回 1024x1024（8% 拉伸，几乎不可感知）
resized = cropped.resize((1024, 1024), Image.LANCZOS)
# macOS 图标圆角半径约为边长的 22%
resized = add_rounded_corners(resized, radius=225)
resized.save(OUT)
print(f"输出: 1024x1024")

# 生成所有尺寸
base = resized.copy()

sizes = {
    "icon.png": 512,
    "128x128@2x.png": 256,
    "128x128.png": 128,
    "32x32.png": 32,
    "Square310x310Logo.png": 310,
    "Square284x284Logo.png": 284,
    "Square150x150Logo.png": 150,
    "Square142x142Logo.png": 142,
    "Square107x107Logo.png": 107,
    "Square89x89Logo.png": 89,
    "Square71x71Logo.png": 71,
    "Square44x44Logo.png": 44,
    "Square30x30Logo.png": 30,
    "StoreLogo.png": 50,
}

for name, size in sizes.items():
    s = base.resize((size, size), Image.LANCZOS)
    s.save(f"src-tauri/icons/{name}")
    print(f"  {name} ({size}x{size})")

# 生成 icns
iconset_dir = "icon.iconset"
os.makedirs(iconset_dir, exist_ok=True)
for s in [16, 32, 64, 128, 256, 512]:
    img_s = base.resize((s, s), Image.LANCZOS)
    img_s.save(f"{iconset_dir}/icon_{s}x{s}.png")
    if s <= 256:
        img_s2 = base.resize((s * 2, s * 2), Image.LANCZOS)
        img_s2.save(f"{iconset_dir}/icon_{s}x{s}@2x.png")

subprocess.run(["iconutil", "-c", "icns", "-o", "src-tauri/icons/icon.icns", iconset_dir], check=True)
shutil.rmtree(iconset_dir)
print("  icon.icns")
print("完成！")
