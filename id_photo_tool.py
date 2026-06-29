#!/usr/bin/env python3
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageEnhance, ImageOps, ImageTk


DPI = 300
MM_PER_INCH = 25.4
SHEET_SIZE = (1200, 1800)  # 4x6 inch at 300 DPI
OUTPUT_FOLDER = "证件照输出"
CONFIG_PATH = Path.home() / ".id_photo_print_tool_config.json"


@dataclass(frozen=True)
class PhotoSpec:
    name: str
    width_mm: float
    height_mm: float


PHOTO_SPECS = [
    PhotoSpec("一寸 25x35mm", 25, 35),
    PhotoSpec("小一寸 22x32mm", 22, 32),
    PhotoSpec("大一寸 33x48mm", 33, 48),
    PhotoSpec("二寸 35x49mm", 35, 49),
    PhotoSpec("小二寸 35x45mm", 35, 45),
    PhotoSpec("小两寸 35x45mm", 35, 45),
    PhotoSpec("驾照 22x32mm", 22, 32),
    PhotoSpec("结婚照 53x35mm", 53, 35),
    PhotoSpec("自定义", 35, 49),
]


def desktop_path() -> Path:
    return Path.home() / "Desktop"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def configured_output_dir() -> Path:
    config = load_config()
    raw_path = config.get("output_dir")
    if raw_path:
        return Path(raw_path).expanduser()
    return desktop_path() / OUTPUT_FOLDER


def set_configured_output_dir(path: Path) -> None:
    config = load_config()
    config["output_dir"] = str(path)
    save_config(config)


def output_dir() -> Path:
    path = configured_output_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def mm_to_px(mm: float) -> int:
    return round(mm / MM_PER_INCH * DPI)


def safe_stem(path: Path) -> str:
    return "".join(ch for ch in path.stem if ch not in '/\\:*?"<>|').strip() or "id-photo"


def detect_subject_bounds(img: Image.Image) -> tuple[int, int, int, int] | None:
    small = ImageOps.exif_transpose(img.convert("RGB"))
    small.thumbnail((420, 560), Image.Resampling.LANCZOS)
    w, h = small.size
    pixels = small.load()
    corners = [
        pixels[0, 0],
        pixels[w - 1, 0],
        pixels[0, h - 1],
        pixels[w - 1, h - 1],
    ]
    bg = tuple(sum(pixel[i] for pixel in corners) // len(corners) for i in range(3))
    xs: list[int] = []
    ys: list[int] = []
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            diff = abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2])
            dark = r + g + b < 650
            if diff > 70 and dark:
                xs.append(x)
                ys.append(y)

    if len(xs) < 200:
        return None

    scale_x = img.width / w
    scale_y = img.height / h
    return (
        round(min(xs) * scale_x),
        round(min(ys) * scale_y),
        round(max(xs) * scale_x),
        round(max(ys) * scale_y),
    )


def fit_crop_box(
    img_size: tuple[int, int],
    target_ratio: float,
    zoom: float,
    offset_x: float,
    offset_y: float,
    subject_bounds: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, int, int]:
    w, h = img_size
    crop_w = w
    crop_h = round(crop_w / target_ratio)
    if crop_h > h:
        crop_h = h
        crop_w = round(crop_h * target_ratio)

    zoom = max(1.0, min(2.4, zoom))
    crop_w = max(1, round(crop_w / zoom))
    crop_h = max(1, round(crop_h / zoom))

    max_left = max(0, w - crop_w)
    max_top = max(0, h - crop_h)
    if subject_bounds:
        sx1, sy1, sx2, _sy2 = subject_bounds
        subject_center_x = (sx1 + sx2) / 2
        top_margin = 0.10
        base_left = subject_center_x - crop_w / 2
        base_top = sy1 - crop_h * top_margin
    else:
        base_left = max_left / 2
        base_top = max_top * 0.42

    left = round(base_left + offset_x / 100 * max_left / 2)
    top = round(base_top + offset_y / 100 * max_top / 2)
    left = max(0, min(max_left, left))
    top = max(0, min(max_top, top))
    return left, top, left + crop_w, top + crop_h


def build_photo(
    source: Path,
    spec: PhotoSpec,
    zoom: float = 1.0,
    offset_x: float = 0,
    offset_y: float = -8,
    enhance: bool = True,
) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(source).convert("RGB"))
    target_ratio = spec.width_mm / spec.height_mm
    box = fit_crop_box(img.size, target_ratio, zoom, offset_x, offset_y, detect_subject_bounds(img))
    size = (mm_to_px(spec.width_mm), mm_to_px(spec.height_mm))
    photo = img.crop(box).resize(size, Image.Resampling.LANCZOS)

    if enhance:
        photo = ImageEnhance.Color(photo).enhance(1.03)
        photo = ImageEnhance.Contrast(photo).enhance(1.04)
        photo = ImageEnhance.Sharpness(photo).enhance(1.08)
    return photo


def auto_grid(photo_size: tuple[int, int], count: int, gap_px: int) -> tuple[int, int]:
    photo_w, photo_h = photo_size
    best = None
    for rows in range(1, count + 1):
        cols = (count + rows - 1) // rows
        width = cols * photo_w + (cols - 1) * gap_px
        height = rows * photo_h + (rows - 1) * gap_px
        if width <= SHEET_SIZE[0] and height <= SHEET_SIZE[1]:
            area = width * height
            balance = abs((width / height) - (SHEET_SIZE[0] / SHEET_SIZE[1]))
            score = (count - rows * cols, -area, -balance)
            if best is None or score > best[0]:
                best = (score, rows, cols)
    if best:
        return best[1], best[2]

    cols = max(1, SHEET_SIZE[0] // max(1, photo_w + gap_px))
    rows = max(1, SHEET_SIZE[1] // max(1, photo_h + gap_px))
    return rows, cols


def make_sheet(
    photo: Image.Image,
    count: int,
    rows: int | None,
    cols: int | None,
    gap_px: int,
    draw_cut_lines: bool,
    extend_cut_lines: bool = False,
) -> Image.Image:
    count = max(1, count)
    if rows is None or cols is None:
        rows, cols = auto_grid(photo.size, count, gap_px)

    capacity = rows * cols
    count = min(count, capacity)
    photo_w, photo_h = photo.size
    layout_w = cols * photo_w + (cols - 1) * gap_px
    layout_h = rows * photo_h + (rows - 1) * gap_px
    if layout_w > SHEET_SIZE[0] or layout_h > SHEET_SIZE[1]:
        raise ValueError("当前尺寸和数量放不进 4x6 相纸，请减少数量或间距。")

    canvas = Image.new("RGB", SHEET_SIZE, "white")
    draw = ImageDraw.Draw(canvas)
    start_x = (SHEET_SIZE[0] - layout_w) // 2
    start_y = (SHEET_SIZE[1] - layout_h) // 2

    for index in range(count):
        row = index // cols
        col = index % cols
        x = start_x + col * (photo_w + gap_px)
        y = start_y + row * (photo_h + gap_px)
        canvas.paste(photo, (x, y))
        if draw_cut_lines:
            color = (190, 190, 190)
            draw.rectangle((x, y, x + photo_w - 1, y + photo_h - 1), outline=color, width=1)
            if extend_cut_lines:
                draw.line((x, 0, x, SHEET_SIZE[1]), fill=color, width=1)
                draw.line((x + photo_w - 1, 0, x + photo_w - 1, SHEET_SIZE[1]), fill=color, width=1)
                draw.line((0, y, SHEET_SIZE[0], y), fill=color, width=1)
                draw.line((0, y + photo_h - 1, SHEET_SIZE[0], y + photo_h - 1), fill=color, width=1)
    return canvas


def process_image(
    source: Path,
    spec: PhotoSpec,
    count: int,
    rows: int | None,
    cols: int | None,
    gap_px: int,
    zoom: float,
    offset_x: float,
    offset_y: float,
    enhance: bool,
    draw_cut_lines: bool,
    extend_cut_lines: bool = False,
) -> tuple[Path, Path]:
    out = output_dir()
    photo = build_photo(source, spec, zoom, offset_x, offset_y, enhance)
    sheet = make_sheet(photo, count, rows, cols, gap_px, draw_cut_lines, extend_cut_lines)
    stem = safe_stem(source)
    size_label = f"{spec.width_mm:g}x{spec.height_mm:g}mm"
    single_path = out / f"{stem}-{spec.name}-单张-{size_label}-300dpi.jpg"
    sheet_path = out / f"{stem}-{spec.name}-4x6排版{count}张-300dpi.jpg"
    photo.save(single_path, quality=96, dpi=(DPI, DPI), subsampling=0)
    sheet.save(sheet_path, quality=96, dpi=(DPI, DPI), subsampling=0)
    return single_path, sheet_path


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("证件照打印排版工具")
        self.geometry("1140x1000")
        self.minsize(1000, 880)
        self.resizable(True, True)
        self.configure(padx=22, pady=18)

        self.source_path: Path | None = None
        self.last_single_path: Path | None = None
        self.last_sheet_path: Path | None = None
        self.single_preview_image: ImageTk.PhotoImage | None = None
        self.sheet_preview_image: ImageTk.PhotoImage | None = None
        self.crop_preview_image: ImageTk.PhotoImage | None = None
        self.preview_job: str | None = None
        self.spec_var = tk.StringVar(value=PHOTO_SPECS[3].name)
        self.width_var = tk.StringVar(value="35")
        self.height_var = tk.StringVar(value="49")
        self.count_var = tk.StringVar(value="6")
        self.rows_var = tk.StringVar(value="自动")
        self.cols_var = tk.StringVar(value="自动")
        self.gap_var = tk.StringVar(value="12")
        self.zoom_var = tk.DoubleVar(value=1.0)
        self.offset_x_var = tk.DoubleVar(value=0)
        self.offset_y_var = tk.DoubleVar(value=-8)
        self.enhance_var = tk.BooleanVar(value=True)
        self.lines_var = tk.BooleanVar(value=True)
        self.extend_lines_var = tk.BooleanVar(value=True)

        tk.Label(self, text="证件照打印排版工具", font=("Arial", 24, "bold")).grid(
            row=0, column=0, columnspan=5, sticky="w", pady=(0, 16)
        )
        self.columnconfigure(0, minsize=720, weight=3)
        self.columnconfigure(1, minsize=320, weight=2)
        self.rowconfigure(5, weight=1)

        self.build_file_row()
        self.build_size_panel()
        self.build_layout_panel()
        self.build_actions()
        self.build_crop_panel()
        self.build_preview_panel()
        self.bind_auto_preview()
        self.bind("<Configure>", self.on_window_resize)

        self.status = tk.Label(self, text=f"选择照片后即可生成。输出：{output_dir()}", fg="#3568a9", anchor="w")
        self.status.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.on_spec_change()

    def build_file_row(self) -> None:
        frame = tk.Frame(self)
        frame.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)

        tk.Button(frame, text="选择照片", command=self.choose_file, width=14, height=2).grid(
            row=0, column=0, sticky="w"
        )
        self.file_label = tk.Label(
            frame,
            text="未选择照片",
            anchor="w",
            fg="#e8e8e8",
            bg="#4a4a4a",
            padx=12,
            pady=8,
        )
        self.file_label.grid(row=0, column=1, sticky="ew", padx=(12, 0))

    def compact_name(self, name: str, limit: int = 34) -> str:
        if len(name) <= limit:
            return name
        keep = max(8, (limit - 3) // 2)
        return f"{name[:keep]}...{name[-keep:]}"

    def build_size_panel(self) -> None:
        frame = ttk.LabelFrame(self, text="照片尺寸")
        frame.grid(row=2, column=0, sticky="ew", pady=(18, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="预设").grid(row=0, column=0, padx=10, pady=12, sticky="w")
        combo = ttk.Combobox(
            frame,
            textvariable=self.spec_var,
            values=[spec.name for spec in PHOTO_SPECS],
            state="readonly",
            width=22,
        )
        combo.grid(row=0, column=1, padx=8, pady=12, sticky="w")
        combo.bind("<<ComboboxSelected>>", lambda _event: self.on_spec_change())

        ttk.Label(frame, text="宽 mm").grid(row=0, column=2, padx=(20, 6), sticky="e")
        ttk.Entry(frame, textvariable=self.width_var, width=8).grid(row=0, column=3, padx=4)
        ttk.Label(frame, text="高 mm").grid(row=0, column=4, padx=(16, 6), sticky="e")
        ttk.Entry(frame, textvariable=self.height_var, width=8).grid(row=0, column=5, padx=(4, 10))

    def build_layout_panel(self) -> None:
        frame = ttk.LabelFrame(self, text="4x6 排版")
        frame.grid(row=3, column=0, sticky="ew", pady=8)

        ttk.Label(frame, text="张数").grid(row=0, column=0, padx=10, pady=12)
        ttk.Spinbox(frame, from_=1, to=30, textvariable=self.count_var, width=8).grid(row=0, column=1)
        ttk.Label(frame, text="行").grid(row=0, column=2, padx=(18, 6))
        ttk.Combobox(frame, textvariable=self.rows_var, values=["自动", "1", "2", "3", "4", "5", "6"], width=8).grid(
            row=0, column=3
        )
        ttk.Label(frame, text="列").grid(row=0, column=4, padx=(18, 6))
        ttk.Combobox(frame, textvariable=self.cols_var, values=["自动", "1", "2", "3", "4", "5", "6"], width=8).grid(
            row=0, column=5
        )
        ttk.Label(frame, text="间距 px").grid(row=0, column=6, padx=(18, 6))
        ttk.Spinbox(frame, from_=0, to=80, textvariable=self.gap_var, width=8).grid(row=0, column=7, padx=(0, 10))
        ttk.Checkbutton(frame, text="框线", variable=self.lines_var).grid(row=0, column=8, padx=8)
        ttk.Checkbutton(frame, text="延长框线", variable=self.extend_lines_var).grid(row=0, column=9, padx=(0, 10))

    def build_crop_panel(self) -> None:
        frame = ttk.LabelFrame(self, text="裁切微调")
        frame.grid(row=5, column=0, sticky="ew", pady=8)
        frame.columnconfigure(2, weight=1)

        self.build_adjust_row(frame, "缩放", self.zoom_var, 1.0, 1.8, 0.02, 0)
        self.build_adjust_row(frame, "左右", self.offset_x_var, -100, 100, 2, 1)
        self.build_adjust_row(frame, "上下", self.offset_y_var, -100, 100, 2, 2)
        ttk.Checkbutton(frame, text="轻微提亮锐化", variable=self.enhance_var).grid(
            row=0, column=6, rowspan=3, padx=18
        )

    def build_adjust_row(
        self,
        frame: ttk.LabelFrame,
        label: str,
        variable: tk.DoubleVar,
        minimum: float,
        maximum: float,
        step: float,
        row: int,
    ) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, padx=10, pady=8, sticky="w")
        ttk.Button(frame, text="-", width=3, command=lambda: self.adjust_value(variable, -step, minimum, maximum)).grid(
            row=row, column=1, padx=(0, 4)
        )
        ttk.Scale(frame, from_=minimum, to=maximum, variable=variable).grid(row=row, column=2, sticky="ew", padx=6)
        ttk.Button(frame, text="+", width=3, command=lambda: self.adjust_value(variable, step, minimum, maximum)).grid(
            row=row, column=3, padx=(4, 8)
        )
        ttk.Spinbox(
            frame,
            from_=minimum,
            to=maximum,
            increment=step,
            textvariable=variable,
            width=8,
        ).grid(row=row, column=4, padx=(0, 10))

    def adjust_value(self, variable: tk.DoubleVar, delta: float, minimum: float, maximum: float) -> None:
        value = max(minimum, min(maximum, float(variable.get()) + delta))
        variable.set(round(value, 2))

    def build_actions(self) -> None:
        frame = tk.Frame(self)
        frame.grid(row=4, column=0, sticky="w", pady=(8, 4))

        tk.Button(frame, text="保存单张和排版", command=self.generate, width=18, height=2).grid(
            row=0, column=0, sticky="w"
        )
        tk.Button(frame, text="打开输出文件夹", command=self.open_output, width=16, height=2).grid(
            row=0, column=1, sticky="w", padx=8
        )
        tk.Button(frame, text="选择输出文件夹", command=self.choose_output_dir, width=16, height=2).grid(
            row=0, column=2, sticky="w", padx=8
        )
        tk.Button(frame, text="打印排版", command=self.print_sheet, width=12, height=2).grid(
            row=0, column=3, sticky="w", padx=8
        )

    def build_preview_panel(self) -> None:
        frame = ttk.LabelFrame(self, text="自动预览")
        frame.grid(row=1, column=1, rowspan=6, sticky="nsew", padx=(18, 0))

        self.single_preview_label = tk.Label(frame, text="单张预览", bg="#f7f7f7", fg="#666")
        self.single_preview_label.pack(fill="both", expand=True, padx=10, pady=(10, 5))
        self.single_caption = tk.Label(frame, text="未选择照片", anchor="center", fg="#555")
        self.single_caption.pack(fill="x", padx=10, pady=(0, 8))

        self.sheet_preview_label = tk.Label(frame, text="4x6 排版预览", bg="#f7f7f7", fg="#666")
        self.sheet_preview_label.pack(fill="both", expand=True, padx=10, pady=(0, 5))
        self.sheet_caption = tk.Label(frame, text="框线默认开启", anchor="center", fg="#555")
        self.sheet_caption.pack(fill="x", padx=10, pady=(0, 8))

        self.data_label = tk.Label(frame, text="尺寸数据会自动显示", justify="left", anchor="w", fg="#333")
        self.data_label.pack(fill="x", padx=10, pady=(0, 10))

    def on_spec_change(self) -> None:
        selected = self.spec_var.get()
        spec = next((item for item in PHOTO_SPECS if item.name == selected), PHOTO_SPECS[3])
        self.width_var.set(f"{spec.width_mm:g}")
        self.height_var.set(f"{spec.height_mm:g}")
        if "结婚照" in spec.name:
            self.count_var.set("3")
            self.rows_var.set("自动")
            self.cols_var.set("自动")
        elif "一寸" in spec.name or "驾照" in spec.name:
            self.count_var.set("9")
            self.rows_var.set("自动")
            self.cols_var.set("自动")
        else:
            self.count_var.set("6")
            self.rows_var.set("自动")
            self.cols_var.set("自动")
        self.schedule_auto_preview()

    def bind_auto_preview(self) -> None:
        for var in (
            self.width_var,
            self.height_var,
            self.count_var,
            self.rows_var,
            self.cols_var,
            self.gap_var,
            self.zoom_var,
            self.offset_x_var,
            self.offset_y_var,
            self.enhance_var,
            self.lines_var,
            self.extend_lines_var,
        ):
            var.trace_add("write", lambda *_args: self.schedule_auto_preview())

    def schedule_auto_preview(self) -> None:
        if self.preview_job:
            self.after_cancel(self.preview_job)
        self.preview_job = self.after(180, self.refresh_auto_preview)

    def on_window_resize(self, event: tk.Event) -> None:
        if event.widget is self and self.source_path:
            self.schedule_auto_preview()

    def current_spec(self) -> PhotoSpec:
        name = self.spec_var.get()
        width = float(self.width_var.get())
        height = float(self.height_var.get())
        if width <= 0 or height <= 0:
            raise ValueError("照片宽高必须大于 0。")
        if name == "自定义":
            return PhotoSpec("自定义", width, height)
        base_name = name.split(" ")[0]
        return PhotoSpec(base_name, width, height)

    def parse_layout_value(self, value: str) -> int | None:
        value = value.strip()
        if not value or value == "自动":
            return None
        return int(value)

    def choose_file(self) -> None:
        file_name = filedialog.askopenfilename(
            title="选择证件照",
            filetypes=[
                ("图片文件", "*.png *.jpg *.jpeg *.webp *.tif *.tiff"),
                ("所有文件", "*.*"),
            ],
        )
        if not file_name:
            return
        self.source_path = Path(file_name)
        self.file_label.config(text=f"已选择：{self.compact_name(self.source_path.name)}")
        self.last_single_path = None
        self.last_sheet_path = None
        self.refresh_auto_preview()

    def get_render_options(self) -> tuple[PhotoSpec, int, int | None, int | None, int, float, float, float, bool, bool, bool]:
        return (
            self.current_spec(),
            int(self.count_var.get()),
            self.parse_layout_value(self.rows_var.get()),
            self.parse_layout_value(self.cols_var.get()),
            int(self.gap_var.get()),
            float(self.zoom_var.get()),
            float(self.offset_x_var.get()),
            float(self.offset_y_var.get()),
            self.enhance_var.get(),
            self.lines_var.get(),
            self.extend_lines_var.get(),
        )

    def build_current_images(self) -> tuple[Image.Image, Image.Image]:
        if not self.source_path:
            raise ValueError("请先选择一张照片。")
        spec, count, rows, cols, gap, zoom, offset_x, offset_y, enhance, lines, extend_lines = self.get_render_options()
        photo = build_photo(self.source_path, spec, zoom, offset_x, offset_y, enhance)
        sheet = make_sheet(photo, count, rows, cols, gap, lines, extend_lines)
        return photo, sheet

    def generate(self, show_message: bool = True) -> tuple[Path, Path] | None:
        if not self.source_path:
            messagebox.showwarning("请选择照片", "请先选择一张照片。")
            return None
        try:
            spec, count, rows, cols, gap, zoom, offset_x, offset_y, enhance, lines, extend_lines = self.get_render_options()
            single, sheet = process_image(
                self.source_path,
                spec,
                count,
                rows,
                cols,
                gap,
                zoom,
                offset_x,
                offset_y,
                enhance,
                lines,
                extend_lines,
            )
        except Exception as exc:
            messagebox.showerror("生成失败", str(exc))
            return None

        self.last_single_path = single
        self.last_sheet_path = sheet
        self.status.config(text=f"已生成：{sheet.name}")
        self.refresh_auto_preview()
        if show_message:
            messagebox.showinfo(
                "完成",
                f"单张：{single}\n\n排版：{sheet}\n\n打印时选择 4x6 英寸，缩放选择 100% / 实际大小。",
            )
        return single, sheet

    def show_preview(self, image: Image.Image, label: tk.Label, image_attr: str, max_size: tuple[int, int]) -> None:
        preview = ImageOps.exif_transpose(image.convert("RGB"))
        preview.thumbnail(max_size, Image.Resampling.LANCZOS)
        photo_image = ImageTk.PhotoImage(preview)
        setattr(self, image_attr, photo_image)
        label.config(image=photo_image, text="", bg="#f7f7f7")

    def single_reference_preview(self, photo: Image.Image, width_px: int, height_px: int) -> Image.Image:
        width_px = max(180, width_px)
        height_px = max(180, height_px)
        canvas = Image.new("RGB", (width_px, height_px), "white")
        draw = ImageDraw.Draw(canvas)
        margin = 12
        available_w = max(1, width_px - margin * 2)
        available_h = max(1, height_px - margin * 2)
        scale = min(available_w / photo.width, available_h / photo.height)
        photo_w = max(1, round(photo.width * scale))
        photo_h = max(1, round(photo.height * scale))
        preview_photo = photo.resize((photo_w, photo_h), Image.Resampling.LANCZOS)
        x = (width_px - photo_w) // 2
        y = (height_px - photo_h) // 2
        canvas.paste(preview_photo, (x, y))
        draw.rectangle((x, y, x + photo_w - 1, y + photo_h - 1), outline=(60, 120, 200), width=2)
        return canvas

    def layout_data_text(self, photo: Image.Image) -> str:
        spec, count, rows, cols, gap, _zoom, _offset_x, _offset_y, _enhance, lines, extend_lines = self.get_render_options()
        auto_rows, auto_cols = auto_grid(photo.size, count, gap)
        rows = rows or auto_rows
        cols = cols or auto_cols
        top_px = round(photo.height * 0.10)
        top_mm = spec.height_mm * 0.10
        return (
            f"单张：{spec.name}  {spec.width_mm:g} x {spec.height_mm:g} mm\n"
            f"像素：{photo.width} x {photo.height} px / {DPI}DPI\n"
            f"自动裁剪：头顶留白约 {top_mm:.1f}mm / {top_px}px\n"
            f"排版：4x6 英寸  {count} 张  {rows} 行 x {cols} 列  间距 {gap}px\n"
            f"框线：{'开启' if lines else '关闭'}  延长框线：{'开启' if extend_lines else '关闭'}"
        )

    def refresh_auto_preview(self) -> None:
        self.preview_job = None
        if not self.source_path:
            return
        try:
            photo, sheet = self.build_current_images()
        except Exception as exc:
            self.status.config(text=f"自动预览失败：{exc}")
            return
        single_w = max(180, self.single_preview_label.winfo_width() - 20)
        single_h = max(180, self.single_preview_label.winfo_height() - 12)
        sheet_w = max(260, self.sheet_preview_label.winfo_width() - 24)
        sheet_h = max(260, self.sheet_preview_label.winfo_height() - 12)
        self.show_preview(
            self.single_reference_preview(photo, single_w, single_h),
            self.single_preview_label,
            "single_preview_image",
            max_size=(single_w, single_h),
        )
        self.show_preview(sheet, self.sheet_preview_label, "sheet_preview_image", max_size=(sheet_w, sheet_h))
        self.single_caption.config(text="单张自动预览")
        self.sheet_caption.config(text="4x6 排版自动预览")
        self.data_label.config(text=self.layout_data_text(photo))
        self.status.config(text="自动预览已刷新。")

    def print_sheet(self) -> None:
        if not self.last_sheet_path or not self.last_sheet_path.exists():
            result = self.generate(show_message=False)
            if not result:
                return

        assert self.last_sheet_path is not None
        try:
            subprocess.run(["open", "-a", "Preview", str(self.last_sheet_path)], check=False)
            subprocess.run(["osascript", "-e", 'tell application "Preview" to activate'], check=False)
            subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to keystroke "p" using command down'],
                check=False,
            )
        except Exception as exc:
            messagebox.showerror("打印失败", str(exc))
            return

        self.status.config(text=f"已打开打印窗口：{self.last_sheet_path.name}")

    def open_output(self) -> None:
        subprocess.run(["open", str(output_dir())], check=False)

    def choose_output_dir(self) -> None:
        folder = filedialog.askdirectory(
            title="选择输出文件夹",
            initialdir=str(output_dir()),
        )
        if not folder:
            return
        path = Path(folder)
        set_configured_output_dir(path)
        output_dir()
        self.status.config(text=f"输出文件夹已设置：{path}")


def cli_process(paths: list[str]) -> None:
    spec = PHOTO_SPECS[3]
    for item in paths:
        single, sheet = process_image(Path(item), spec, 6, None, None, 12, 1.0, 0, -8, True, True)
        print(f"生成：{single}")
        print(f"生成：{sheet}")


def main() -> None:
    if len(sys.argv) > 1:
        cli_process(sys.argv[1:])
        return
    App().mainloop()


if __name__ == "__main__":
    main()
