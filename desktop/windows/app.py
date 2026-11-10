#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页成书 · Windows 桌面版（Tkinter）。"""
import os
import queue
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, ttk, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = [os.path.join(HERE, 'core'),
              os.path.join(HERE, '..', '..', 'core'),
              os.path.join(getattr(sys, '_MEIPASS', HERE), 'core')]
CORE = next((c for c in CANDIDATES if os.path.isdir(c)), CANDIDATES[0])
if CORE not in sys.path:
    sys.path.insert(0, CORE)

import converter  # noqa: E402


class App(object):
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.running = False
        root.title('网页成书')
        root.geometry('760x600')
        root.minsize(680, 520)

        pad = {'padx': 16, 'pady': 6}
        head = ttk.Frame(root)
        head.pack(fill='x', **pad)
        ttk.Label(head, text='网页成书', font=('Microsoft YaHei UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(head, text='粘贴网址 → 抓取正文与图片 → 分章 → 生成 EPUB / PDF / DOCX',
                  foreground='#666').pack(anchor='w', pady=(2, 0))

        row = ttk.Frame(root)
        row.pack(fill='x', **pad)
        ttk.Label(row, text='网址').pack(side='left')
        self.url = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.url)
        entry.pack(side='left', fill='x', expand=True, padx=8)
        entry.focus_set()
        try:
            clip = root.clipboard_get()
            if clip.lower().startswith('http') and len(clip) < 500 and '\n' not in clip:
                self.url.set(clip)
        except Exception:
            pass

        fmt = ttk.Frame(root)
        fmt.pack(fill='x', **pad)
        ttk.Label(fmt, text='输出格式').pack(side='left')
        self.v_epub = tk.BooleanVar(value=True)
        self.v_pdf = tk.BooleanVar(value=True)
        self.v_docx = tk.BooleanVar(value=True)
        for text, var in (('EPUB 电子书', self.v_epub), ('PDF', self.v_pdf), ('DOCX', self.v_docx)):
            ttk.Checkbutton(fmt, text=text, variable=var).pack(side='left', padx=10)

        out = ttk.Frame(root)
        out.pack(fill='x', **pad)
        ttk.Label(out, text='保存到').pack(side='left')
        self.outdir = tk.StringVar(value=os.path.join(os.path.expanduser('~'), 'Downloads'))
        ttk.Entry(out, textvariable=self.outdir).pack(side='left', fill='x', expand=True, padx=8)
        ttk.Button(out, text='更改…', command=self.choose_dir).pack(side='left')

        act = ttk.Frame(root)
        act.pack(fill='x', **pad)
        self.btn = ttk.Button(act, text='开始转换', command=self.start)
        self.btn.pack(side='left')
        self.open_btn = ttk.Button(act, text='打开所在文件夹', command=self.open_dir, state='disabled')
        self.open_btn.pack(side='left', padx=8)

        self.pbar = ttk.Progressbar(root, maximum=100)
        self.pbar.pack(fill='x', padx=16, pady=(8, 2))
        self.status = ttk.Label(root, text='就绪', foreground='#555')
        self.status.pack(anchor='w', padx=16)

        self.log = tk.Text(root, height=14, wrap='word', font=('Menlo' if sys.platform == 'darwin' else 'Consolas', 9))
        self.log.pack(fill='both', expand=True, padx=16, pady=10)
        self.log.configure(state='disabled')

        foot = ttk.Frame(root)
        foot.pack(fill='x', padx=16, pady=(0, 10))
        ttk.Label(foot, text='制作：张兴文 ｜ zane@163.com ｜ v%s' % converter.VERSION,
                  foreground='#888').pack(side='left')
        ttk.Button(foot, text='关于', command=self.about).pack(side='right')

        root.after(120, self.poll)

    def about(self):
        messagebox.showinfo('关于 网页成书',
                            '网页成书 WebToBook v%s\n\n'
                            '把网页变成 EPUB / PDF / DOCX\n\n'
                            '制作：张兴文\n邮箱：zane@163.com\n'
                            '主页：github.com/shadowenzane/web-to-book' % converter.VERSION)

    def choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.outdir.get() or os.path.expanduser('~'))
        if d:
            self.outdir.set(d)

    def open_dir(self):
        d = self.outdir.get()
        if sys.platform.startswith('win'):
            os.startfile(d)  # noqa
        elif sys.platform == 'darwin':
            os.system('open "%s"' % d)
        else:
            os.system('xdg-open "%s"' % d)

    def write_log(self, s):
        self.log.configure(state='normal')
        self.log.insert('end', s + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def start(self):
        if self.running:
            return
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning('网页成书', '请先粘贴网页链接')
            return
        if not url.lower().startswith('http'):
            url = 'https://' + url
        formats = []
        if self.v_epub.get():
            formats.append('epub')
        if self.v_pdf.get():
            formats.append('pdf')
        if self.v_docx.get():
            formats.append('docx')
        if not formats:
            messagebox.showwarning('网页成书', '请至少选择一种输出格式')
            return

        self.running = True
        self.btn.configure(state='disabled')
        self.open_btn.configure(state='disabled')
        self.pbar['value'] = 0
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')
        self.status.configure(text='正在处理…')

        t = threading.Thread(target=self.worker, args=(url, self.outdir.get(), formats), daemon=True)
        t.start()

    def worker(self, url, outdir, formats):
        def cb(pct, msg):
            self.q.put(('progress', (pct, msg)))
        try:
            res = converter.convert(url, outdir, formats, progress_cb=cb, pdf_engine='reportlab')
            self.q.put(('done', res))
        except Exception as e:
            self.q.put(('error', (str(e), traceback.format_exc())))

    def poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == 'progress':
                    pct, msg = payload
                    self.pbar['value'] = pct
                    self.status.configure(text=msg)
                    self.write_log('· ' + msg)
                elif kind == 'done':
                    self.finish(payload)
                elif kind == 'error':
                    self.fail(payload)
        except queue.Empty:
            pass
        self.root.after(120, self.poll)

    def finish(self, res):
        self.running = False
        self.btn.configure(state='normal')
        self.open_btn.configure(state='normal')
        self.pbar['value'] = 100
        self.status.configure(text='完成，已保存到 ' + self.outdir.get())
        for k, v in sorted((res.get('files') or {}).items()):
            self.write_log('✓ %s：%s' % (k.upper(), v))
        for c in res.get('checks') or []:
            self.write_log('  校验 %s：%s' % (c['fmt'], '通过' if c['ok'] else '有问题'))
        self.write_log('共 %d 章、%d 张图片' % (res.get('chapters', 0), res.get('images', 0)))

    def fail(self, payload):
        msg, detail = payload
        self.running = False
        self.btn.configure(state='normal')
        self.status.configure(text='转换失败')
        self.write_log('✗ ' + msg)
        self.write_log(detail[-800:])


def main():
    root = tk.Tk()
    try:
        root.tk.call('tk', 'scaling', 1.2)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
