#!/usr/bin/env python3
"""图标生成工具：从 icon.png 生成多尺寸 PNG + icns/ico

用法: python3 generate-icon.py [icon.png路径]

- macOS: 生成 icns (需要 iconutil)
- Windows: 生成 ico
- 通用: 生成所有尺寸的 PNG
"""
import os, sys, platform
from PIL import Image

# 默认源图标
DEFAULT_SRC = "src-tauri/icons/icon.png"

def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC
    if not os.path.exists(src):
        print(f"错误: 找不到源图标 {src}")
        sys.exit(1)

    base = Image.open(src).convert("RGBA")
    icons_dir = "src-tauri/icons"
    os.makedirs(icons_dir, exist_ok=True)

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
        s.save(f"{icons_dir}/{name}")
        print(f"  {name} ({size}x{size})")

    # 生成 icns (仅 macOS)
    if platform.system() == "Darwin":
        import subprocess, shutil
        iconset_dir = "icon.iconset"
        os.makedirs(iconset_dir, exist_ok=True)
        for s in [16, 32, 64, 128, 256, 512]:
            img_s = base.resize((s, s), Image.LANCZOS)
            img_s.save(f"{iconset_dir}/icon_{s}x{s}.png")
            if s <= 256:
                img_s2 = base.resize((s * 2, s * 2), Image.LANCZOS)
                img_s2.save(f"{iconset_dir}/icon_{s}x{s}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", "-o", f"{icons_dir}/icon.icns", iconset_dir], check=True)
        shutil.rmtree(iconset_dir)
        print("  icon.icns")

    # 生成 Windows .ico
    ico_sizes = [16, 32, 48, 64, 128, 256]
    ico_frames = [base.resize((s, s), Image.LANCZOS) for s in ico_sizes]
    ico_frames[0].save(
        f"{icons_dir}/icon.ico",
        format="ICO",
        sizes=[(f.size[0], f.size[1]) for f in ico_frames],
        append_images=ico_frames[1:],
    )
    print("  icon.ico")

    print("完成！")

if __name__ == "__main__":
    main()
