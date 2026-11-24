"""A tiny stand-in for WinGPC's Raw Data window, for end-to-end rehearsal on
any Windows PC *without* the instrument or the licence.

It draws a live, slowly growing chromatogram (Tk canvas, white paper, black
axes, grey grid, blue trace) inside a window titled "WinGPC - Raw Data
[Simulator]" and offers menus File > ASCII export..., Raw Data > Data Editor
and a ChromPilot > Sequence Manager dialog with Start/Stop/Pause buttons.

Use it to:
  * check that `wingpc-agent probe` finds the window, menus and plot pane
  * tune plot.trace_rgb / color_tolerance / poll_hz
  * rehearse recipes (the simulator's menu names match recipes/wingpc_default.yaml,
    so you can set verified: true for a dry run against the simulator only)
  * watch the LLM operate it before pointing it at the real software

    python sim/fake_wingpc.py --speed 4
"""
from __future__ import annotations

import argparse
import math
import random
import time
import tkinter as tk
from tkinter import filedialog, messagebox

PEAKS = [(9.0, 100.0, 0.35), (12.5, 60.0, 0.5), (18.0, 25.0, 0.8)]
X_MAX, Y_MIN, Y_MAX = 30.0, -10.0, 120.0


class Sim:
    def __init__(self, root: tk.Tk, speed: float):
        self.root = root
        self.speed = speed
        self.running = False
        self.t = 0.0
        self.xs: list[float] = []
        self.ys: list[float] = []
        root.title("WinGPC - Raw Data [Simulator]")
        root.geometry("1000x600")
        self._menus()
        self.canvas = tk.Canvas(root, bg="#f0f0f0", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.status = tk.Label(root, text="Idle", anchor="w", relief="sunken")
        self.status.pack(fill="x")
        root.after(100, self._tick)

    def _menus(self):
        bar = tk.Menu(self.root)
        f = tk.Menu(bar, tearoff=0)
        f.add_command(label="Open...", command=lambda: filedialog.askopenfilename())
        f.add_command(label="ASCII export...", command=self.export_ascii)
        f.add_command(label="Save ASCII Report", command=self.export_ascii)
        f.add_separator()
        f.add_command(label="Exit", command=self.root.destroy)
        bar.add_cascade(label="File", menu=f)
        rd = tk.Menu(bar, tearoff=0)
        rd.add_command(label="Data Editor", command=self.data_editor)
        bar.add_cascade(label="Raw Data", menu=rd)
        cp = tk.Menu(bar, tearoff=0)
        cp.add_command(label="Sequence Manager", command=self.sequence_manager)
        cp.add_command(label="Instrument Manager", command=lambda: messagebox.showinfo("Instrument Manager", "Pump 1.00 mL/min, RI 35 C, UV lamp on"))
        bar.add_cascade(label="ChromPilot", menu=cp)
        w = tk.Menu(bar, tearoff=0)
        w.add_command(label="MWD", command=lambda: None)
        bar.add_cascade(label="Window", menu=w)
        self.root.config(menu=bar)

    # ---- fake acquisition
    def _signal(self, t):
        y = 5 + 0.2 * t + random.gauss(0, 0.3)
        for mu, h, sig in PEAKS:
            y += h * math.exp(-0.5 * ((t - mu) / sig) ** 2)
        return y

    def _tick(self):
        if self.running and self.t < X_MAX:
            dt = 0.01 * self.speed
            n = max(1, int(self.speed))
            for _ in range(n):
                self.t += 0.01
                self.xs.append(self.t)
                self.ys.append(self._signal(self.t))
            self.status.config(text=f"Running  t = {self.t:6.2f} min   signal = {self.ys[-1]:7.2f} mV")
            if self.t >= X_MAX:
                self.running = False
                self.status.config(text="Sequence finished")
        self._draw()
        self.root.after(100, self._tick)

    def _draw(self):
        c = self.canvas
        c.delete("all")
        W, H = c.winfo_width(), c.winfo_height()
        l, t, r, b = 70, 30, W - 30, H - 50
        if r <= l or b <= t:
            return
        c.create_rectangle(l, t, r, b, fill="white", outline="")
        for i in range(1, 6):
            gx = l + (r - l) * i / 6
            gy = t + (b - t) * i / 6
            c.create_line(gx, t, gx, b, fill="#c8c8c8")
            c.create_line(l, gy, r, gy, fill="#c8c8c8")
        c.create_line(l, b, r, b, fill="black", width=2)
        c.create_line(l, t, l, b, fill="black", width=2)
        for i in range(7):
            xv = X_MAX * i / 6
            c.create_text(l + (r - l) * i / 6, b + 12, text=f"{xv:.0f}", fill="black", font=("Segoe UI", 8))
        for i in range(0, 7):
            yv = Y_MIN + (Y_MAX - Y_MIN) * i / 6
            c.create_text(l - 25, b - (b - t) * i / 6, text=f"{yv:.0f}", fill="black", font=("Segoe UI", 8))
        c.create_text((l + r) / 2, b + 32, text="Elution time [min]", fill="black")
        if len(self.xs) > 1:
            pts = []
            for x, y in zip(self.xs, self.ys):
                pts += [l + x / X_MAX * (r - l), b - (y - Y_MIN) / (Y_MAX - Y_MIN) * (b - t)]
            c.create_line(*pts, fill="#0000ff", width=1)

    # ---- dialogs
    def sequence_manager(self):
        d = tk.Toplevel(self.root)
        d.title("Sequence Manager")
        tk.Label(d, text="Sequence: demo_PS_standards.seq").pack(padx=10, pady=6)
        row = tk.Frame(d); row.pack(padx=10, pady=6)
        tk.Button(row, text="Start", command=self.start).pack(side="left", padx=4)
        tk.Button(row, text="Pause", command=self.pause).pack(side="left", padx=4)
        tk.Button(row, text="Stop", command=self.stop).pack(side="left", padx=4)

    def data_editor(self):
        d = tk.Toplevel(self.root)
        d.title("Data Editor")
        bar = tk.Menu(d)
        f = tk.Menu(bar, tearoff=0)
        f.add_command(label="ASCII export...", command=self.export_ascii)
        bar.add_cascade(label="File", menu=f)
        d.config(menu=bar)
        txt = tk.Text(d, width=40, height=20)
        txt.pack()
        txt.insert("end", "Time [min]\tRI [mV]\n" + "\n".join(f"{x:.3f}\t{y:.3f}" for x, y in zip(self.xs[-200:], self.ys[-200:])))

    def export_ascii(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("WinGPC ASCII export (simulator)\nSample: demo\n\nTime [min]\tRI [mV]\n")
            for x, y in zip(self.xs, self.ys):
                fh.write(f"{x:.4f}\t{y:.4f}\n")
        self.status.config(text=f"Exported {len(self.xs)} points to {path}")

    def start(self):
        if self.t >= X_MAX:
            self.t, self.xs, self.ys = 0.0, [], []
        self.running = True

    def pause(self):
        self.running = False
        self.status.config(text="Paused")

    def stop(self):
        self.running = False
        self.status.config(text="Stopped")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=4.0, help="simulated minutes per real second x 0.1")
    ap.add_argument("--autostart", action="store_true")
    a = ap.parse_args()
    root = tk.Tk()
    sim = Sim(root, a.speed)
    if a.autostart:
        sim.start()
    root.mainloop()


if __name__ == "__main__":
    main()
