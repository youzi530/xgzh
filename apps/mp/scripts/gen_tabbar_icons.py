"""
One-shot 脚本: 从 Tabler Icons PNG CDN 下载 5 个 outline 图标, 染色 + resize
后输出 10 张 (5 tab × 2 状态) 81x81 PNG 到 apps/mp/static/tabbar/.

跑法 (在 apps/api venv): uv run python /tmp/gen_tabbar_icons.py
依赖: Pillow (临时装的, 不进 pyproject)

色彩规范 (与 apps/mp/pages.json tabBar.color / selectedColor 对齐):
- normal:  #94a3b8 (slate-400, 灰)
- active:  #4f8bff (品牌主色蓝)

资源 license: Tabler Icons MIT, 商用可。
"""

from io import BytesIO
from pathlib import Path
from urllib.request import urlopen

from PIL import Image

ICON_URL = "https://cdn.jsdelivr.net/npm/@tabler/icons-png@latest/icons/outline/{name}.png"
TARGET_DIR = Path("/Users/youzi530/lingqiao/demand-engine-team/xgzh/apps/mp/static/tabbar")
TARGET_SIZE = (81, 81)

# tab 名 → tabler icon 名
ICONS = {
    "home": "home",                 # 首页
    "community": "messages",        # 社区
    "subscriptions": "trophy",      # 中签
    "knowledge": "book-2",          # 知识
    "me": "user-circle",            # 我的
}

# normal / active 双色
COLORS = {
    "normal": (148, 163, 184, 255),  # #94a3b8
    "active": (79, 139, 255, 255),   # #4f8bff
}


def tint_and_resize(src: Image.Image, color: tuple[int, int, int, int]) -> Image.Image:
    """把黑色 outline 染成 ``color`` 并下采样到 TARGET_SIZE。

    Tabler PNG 是黑 stroke + 透明背景, 染色 = 用 alpha 通道当 mask 套色。
    """
    src = src.convert("RGBA")
    alpha = src.split()[-1]
    colored = Image.new("RGBA", src.size, color)
    colored.putalpha(alpha)
    colored.thumbnail(TARGET_SIZE, Image.LANCZOS)
    canvas = Image.new("RGBA", TARGET_SIZE, (0, 0, 0, 0))
    offset = (
        (TARGET_SIZE[0] - colored.size[0]) // 2,
        (TARGET_SIZE[1] - colored.size[1]) // 2,
    )
    canvas.paste(colored, offset, colored)
    return canvas


def download(name: str) -> Image.Image:
    url = ICON_URL.format(name=name)
    print(f"  [GET] {url}")
    with urlopen(url, timeout=20) as resp:
        return Image.open(BytesIO(resp.read())).convert("RGBA")


def main() -> None:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    print(f"目标目录: {TARGET_DIR}")
    for tab_name, icon_name in ICONS.items():
        print(f"\n[{tab_name}] tabler/{icon_name}")
        src = download(icon_name)
        for state, color in COLORS.items():
            out = tint_and_resize(src, color)
            out_path = TARGET_DIR / f"{tab_name}-{state}.png"
            out.save(out_path, "PNG")
            print(f"  → {out_path.name} ({out.size[0]}x{out.size[1]}, color={color})")
    print("\n✅ 10 张 PNG 全部生成")


if __name__ == "__main__":
    main()
