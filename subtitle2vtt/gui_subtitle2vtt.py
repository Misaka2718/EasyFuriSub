import pykakasi
import re
import os
import glob
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import sys

# 初始化 pykakasi
kks = pykakasi.kakasi()

# ---------- 文本注音 ----------

def add_ruby_to_text(text):
    converted = kks.convert(text)
    result_text = ""
    for item in converted:
        orig = item['orig']
        hira = item['hira']
        if re.search(r'[一-龯]', orig):
            result_text += f"<ruby>{orig}<rt>{hira}</rt></ruby>"
        else:
            result_text += orig
    return result_text

# ---------- 字幕解析（SRT / ASS 共用 cue 结构） ----------

def _read_text_auto(path):
    """按 utf-8 -> utf-16 顺序尝试读取字幕文件"""
    for enc in ('utf-8-sig', 'utf-16'):
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        return f.read()

def parse_srt(srt_path):
    """解析 SRT 文件，返回 cue 列表 [{'time': 时间行, 'text': 文本}]"""
    lines = [line.strip() for line in _read_text_auto(srt_path).splitlines()]

    cues = []
    current_time = None
    current_text = []

    for i, line in enumerate(lines):
        if '-->' in line:
            if current_time is not None:
                cues.append({'time': current_time, 'text': '\n'.join(current_text)})
            current_time = line
            current_text = []
        elif line == "":
            continue
        else:
            if line.isdigit():
                is_index = False
                for j in range(i + 1, len(lines)):
                    next_line = lines[j]
                    if next_line == "": continue
                    if '-->' in next_line: is_index = True
                    break
                if is_index: continue
            if current_time is not None:
                current_text.append(line)

    if current_time is not None:
        cues.append({'time': current_time, 'text': '\n'.join(current_text)})
    return cues

def _ass_time_to_srt(t):
    """ASS 时间 0:01:23.45 -> SRT 风格 00:01:23,450"""
    t = t.strip()
    h, rest = t.split(':', 1)
    m, rest = rest.split(':', 1)
    s, cs = rest.split('.')
    h, m, s = int(h), int(m), int(s)
    cs = int(cs.ljust(2, '0')[:2])
    return f"{h:02d}:{m:02d}:{s:02d},{cs * 10:03d}"

def parse_ass(ass_path):
    """解析 ASS 文件 [Events] 段，返回与 parse_srt 相同结构的 cue 列表"""
    cues = []
    in_events = False
    for raw in _read_text_auto(ass_path).splitlines():
        line = raw.strip()
        if line.startswith('['):
            in_events = line.lower() == '[events]'
            continue
        if not in_events or not line.startswith('Dialogue:'):
            continue
        fields = line[len('Dialogue:'):].split(',', 9)
        if len(fields) < 10:
            continue
        start, end, text = fields[1], fields[2], fields[9]
        # 去除特效标签 {...}，恢复换行与硬空格
        text = re.sub(r'\{[^}]*\}', '', text)
        text = text.replace('\\N', '\n').replace('\\n', '\n').replace('\\h', ' ')
        text = text.strip()
        if not text:
            continue
        cues.append({'time': f"{_ass_time_to_srt(start)} --> {_ass_time_to_srt(end)}", 'text': text})
    return cues

def parse_subtitle(path):
    """按扩展名分发解析"""
    if os.path.splitext(path)[1].lower() == '.ass':
        return parse_ass(path)
    return parse_srt(path)

def dedupe_cues(cues):
    """按 时间|||文本 签名去重"""
    result = []
    seen = set()
    for cue in cues:
        sig = f"{cue['time']}|||{cue['text']}"
        if sig in seen: continue
        seen.add(sig)
        result.append(cue)
    return result

# ---------- VTT 输出 ----------

def convert_to_vtt(sub_path, vtt_path):
    cues = dedupe_cues(parse_subtitle(sub_path))
    with open(vtt_path, 'w', encoding='utf-8') as f:
        f.write("WEBVTT\n\n")
        for cue in cues:
            f.write(cue['time'].replace(',', '.') + "\n")
            f.write('\n'.join(add_ruby_to_text(l) for l in cue['text'].split('\n')) + "\n\n")

# --- GUI 界面部分 ---
class SubtitleApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SRT/ASS 转 VTT（带注音）工具")
        self.root.geometry("600x500")
        self.root.resizable(False, False)

        # 处理模式
        self.mode_var = tk.StringVar(value='batch')
        tk.Label(root, text="处理模式:").place(x=20, y=20)
        ttk.Radiobutton(root, text="批量处理（文件夹）", variable=self.mode_var, value='batch').place(x=100, y=18)
        ttk.Radiobutton(root, text="单个文件", variable=self.mode_var, value='single').place(x=250, y=18)

        # 输入路径（批量=文件夹 / 单个=文件）
        tk.Label(root, text="输入路径:").place(x=20, y=60)
        self.entry_input = tk.Entry(root, width=50)
        self.entry_input.place(x=100, y=60)
        tk.Button(root, text="浏览...", command=self.select_input).place(x=470, y=55)

        # 输出文件夹
        tk.Label(root, text="输出文件夹:").place(x=20, y=100)
        self.entry_output = tk.Entry(root, width=50)
        self.entry_output.place(x=100, y=100)
        tk.Button(root, text="浏览...", command=self.select_output).place(x=470, y=95)

        # 文件名前缀
        tk.Label(root, text="文件名前缀:").place(x=20, y=140)
        self.entry_prefix = tk.Entry(root, width=20)
        self.entry_prefix.insert(0, "ruby_")
        self.entry_prefix.place(x=100, y=140)

        # 运行按钮
        self.btn_run = tk.Button(root, text="开始转换", font=("Arial", 12, "bold"), bg="#4CAF50", fg="white", command=self.start_processing)
        self.btn_run.place(x=250, y=185, width=100, height=35)

        # 日志输出框
        tk.Label(root, text="处理日志:").place(x=20, y=230)
        self.txt_log = scrolledtext.ScrolledText(root, width=75, height=12, state='disabled')
        self.txt_log.place(x=20, y=255)

        # 重定向 print 到文本框
        sys.stdout = self.PrintLogger(self.txt_log)

    class PrintLogger:
        def __init__(self, text_widget):
            self.text_widget = text_widget

        def write(self, msg):
            self.text_widget.config(state='normal')
            self.text_widget.insert(tk.END, msg)
            self.text_widget.see(tk.END)
            self.text_widget.config(state='disabled')
            # 强制刷新UI，防止卡死
            self.text_widget.update()

        def flush(self):
            pass

    def select_input(self):
        if self.mode_var.get() == 'batch':
            folder = filedialog.askdirectory()
            if folder:
                self.entry_input.delete(0, tk.END)
                self.entry_input.insert(0, folder)
        else:
            path = filedialog.askopenfilename(filetypes=[("字幕文件", "*.srt *.ass"), ("所有文件", "*.*")])
            if path:
                self.entry_input.delete(0, tk.END)
                self.entry_input.insert(0, path)

    def select_output(self):
        folder = filedialog.askdirectory()
        if folder:
            self.entry_output.delete(0, tk.END)
            self.entry_output.insert(0, folder)

    def start_processing(self):
        input_path = self.entry_input.get().strip()
        output_dir = self.entry_output.get().strip()
        prefix = self.entry_prefix.get().strip()
        mode = self.mode_var.get()

        if not input_path or not output_dir:
            messagebox.showwarning("提示", "请选择输入路径和输出文件夹！")
            return

        # 禁用按钮，防止重复点击
        self.btn_run.config(state=tk.DISABLED)
        self.txt_log.config(state='normal')
        self.txt_log.delete(1.0, tk.END)
        self.txt_log.config(state='disabled')

        # 使用子线程运行转换逻辑，防止界面卡死
        thread = threading.Thread(target=self.batch_process, args=(input_path, output_dir, prefix, mode))
        thread.daemon = True
        thread.start()

    def batch_process(self, input_path, output_dir, prefix, mode):
        try:
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
                print(f"📁 已自动创建输出文件夹: '{output_dir}'")

            if mode == 'single':
                if not os.path.isfile(input_path):
                    print(f"⚠️ 找不到文件: '{input_path}'")
                    return
                if not input_path.lower().endswith(('.srt', '.ass')):
                    print(f"⚠️ 不支持的文件类型（仅支持 .srt / .ass）: '{input_path}'")
                    return
                sub_files = [input_path]
                print("🔎 单个文件处理模式\n")
            else:
                if not os.path.isdir(input_path):
                    print(f"⚠️ 找不到文件夹: '{input_path}'")
                    return
                sub_files = sorted(glob.glob(os.path.join(input_path, '*.srt')) +
                                   glob.glob(os.path.join(input_path, '*.ass')))
                if not sub_files:
                    print(f"⚠️ 在 '{input_path}' 下没有找到任何 .srt / .ass 文件。")
                    return
                print(f"🔎 共找到 {len(sub_files)} 个字幕文件，开始批量转换...\n")

            for sub_file in sub_files:
                base_name = os.path.splitext(os.path.basename(sub_file))[0]
                vtt_file_name = f"{prefix}{base_name}.vtt"
                vtt_file_path = os.path.join(output_dir, vtt_file_name)

                print(f"⏳ 正在处理: {os.path.basename(sub_file)} -> {vtt_file_name}")
                convert_to_vtt(sub_file, vtt_file_path)

            print(f"\n🎉 转换完成！\n已保存至: {os.path.abspath(output_dir)}")
            messagebox.showinfo("完成", "所有文件转换完成！")
        except Exception as e:
            print(f"\n❌ 发生错误: {str(e)}")
            messagebox.showerror("错误", f"处理过程中发生错误:\n{str(e)}")
        finally:
            # 恢复按钮状态
            self.btn_run.config(state=tk.NORMAL)

if __name__ == "__main__":
    root = tk.Tk()
    app = SubtitleApp(root)
    root.mainloop()
