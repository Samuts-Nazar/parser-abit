"""
GUI (CustomTkinter) для персонального аналізу конкурентів abit-poisk.

Запуск:
    python gui.py
"""

import queue
import threading
import tkinter.messagebox as messagebox
from typing import List, Optional

import customtkinter as ctk

from abit_parser import settings
from abit_parser.engine import AnalysisError, AnalysisResult, run_analysis
from abit_parser.summarize import (
    PROVIDER_GEMINI,
    PROVIDER_OFFLINE,
    SummarizeError,
    build_depersonalized_payload,
    generate_summary,
)

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

TABLE_COLUMNS = ["ПІБ", "Бал", "Пріоритет", "Статус", "Куди метить"]
TABLE_WIDTHS = [200, 70, 70, 100, 320]


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("abit-poisk — персональний аналіз конкурентів")
        self.geometry("980x720")
        self.minsize(760, 560)

        self._queue: "queue.Queue" = queue.Queue()
        self._result: Optional[AnalysisResult] = None
        self._analyzing = False

        self._build_form()
        self._build_results()

        self.after(100, self._poll_queue)

    # ------------------------------------------------------------------ форма

    def _build_form(self) -> None:
        form = ctk.CTkFrame(self)
        form.pack(fill="x", padx=12, pady=12)
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="Посилання на напрям:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.url_entry = ctk.CTkEntry(
            form, placeholder_text="https://abit-poisk.org.ua/rate2026/direction/1613482"
        )
        self.url_entry.grid(row=0, column=1, columnspan=3, sticky="we", padx=6, pady=4)

        ctk.CTkLabel(form, text="Ваш бал:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.score_entry = ctk.CTkEntry(form, width=100)
        self.score_entry.grid(row=1, column=1, sticky="w", padx=6, pady=4)

        ctk.CTkLabel(form, text="Пріоритет:").grid(row=1, column=2, sticky="w", padx=6, pady=4)
        self.priority_entry = ctk.CTkEntry(form, width=60)
        self.priority_entry.grid(row=1, column=3, sticky="w", padx=6, pady=4)

        ctk.CTkLabel(form, text="Форма навчання:").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.funding_var = ctk.StringVar(value="Б")
        ctk.CTkSegmentedButton(form, values=["Б", "К"], variable=self.funding_var).grid(
            row=2, column=1, sticky="w", padx=6, pady=4
        )

        ctk.CTkLabel(form, text="Ключ Gemini API:").grid(row=3, column=0, sticky="w", padx=6, pady=4)
        self.key_entry = ctk.CTkEntry(form, show="*")
        self.key_entry.grid(row=3, column=1, columnspan=2, sticky="we", padx=6, pady=4)

        saved_key = settings.load_gemini_key()
        if saved_key:
            self.key_entry.insert(0, saved_key)

        self.remember_var = ctk.BooleanVar(value=bool(saved_key))
        ctk.CTkCheckBox(form, text="Запам'ятати ключ", variable=self.remember_var).grid(
            row=3, column=3, sticky="w", padx=6, pady=4
        )

        ctk.CTkLabel(
            form,
            text=(
                "Ключ зберігається відкритим текстом локально в "
                "%APPDATA%\\parser-abit\\config.json — лише на цій машині."
            ),
            text_color="gray",
            font=ctk.CTkFont(size=11),
            wraplength=820,
            justify="left",
        ).grid(row=4, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 6))

        self.cross_check_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            form, text="Крос-аналіз v2 (додаткові запити на сайт по кожній людині)", variable=self.cross_check_var
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=6, pady=4)

        self.analyze_button = ctk.CTkButton(form, text="Аналізувати", command=self._start_analysis)
        self.analyze_button.grid(row=5, column=3, sticky="e", padx=6, pady=4)

        self.progress_bar = ctk.CTkProgressBar(form)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=6, column=0, columnspan=4, sticky="we", padx=6, pady=(6, 0))

        self.status_label = ctk.CTkLabel(form, text="", text_color="gray", anchor="w")
        self.status_label.grid(row=7, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 4))

    # --------------------------------------------------------------- резульат

    def _build_results(self) -> None:
        results = ctk.CTkFrame(self)
        results.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.summary_label = ctk.CTkLabel(
            results, text="", justify="left", anchor="w", font=ctk.CTkFont(size=14)
        )
        self.summary_label.pack(fill="x", padx=8, pady=8)

        header = ctk.CTkFrame(results, fg_color="transparent")
        header.pack(fill="x", padx=8)
        for i, (title, w) in enumerate(zip(TABLE_COLUMNS, TABLE_WIDTHS)):
            ctk.CTkLabel(header, text=title, font=ctk.CTkFont(weight="bold"), width=w, anchor="w").grid(
                row=0, column=i, sticky="w", padx=4
            )

        self.table_frame = ctk.CTkScrollableFrame(results)
        self.table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        explain_frame = ctk.CTkFrame(results, fg_color="transparent")
        explain_frame.pack(fill="x", padx=8, pady=(0, 4))

        self.provider_var = ctk.StringVar(value=PROVIDER_GEMINI)
        ctk.CTkSegmentedButton(
            explain_frame, values=[PROVIDER_GEMINI, PROVIDER_OFFLINE], variable=self.provider_var
        ).pack(side="left", padx=6, pady=6)

        self.explain_button = ctk.CTkButton(
            explain_frame, text="Пояснити", command=self._start_explain, state="disabled"
        )
        self.explain_button.pack(side="left", padx=6, pady=6)

        self.explain_text = ctk.CTkTextbox(results, height=110, wrap="word")
        self.explain_text.pack(fill="x", padx=8, pady=(0, 8))
        self.explain_text.configure(state="disabled")

    # ---------------------------------------------------------------- аналіз

    def _start_analysis(self) -> None:
        if self._analyzing:
            return

        url = self.url_entry.get().strip()
        if not url:
            messagebox.showerror("Помилка", "Вкажіть посилання на напрям.")
            return
        try:
            score = float(self.score_entry.get().strip().replace(",", "."))
            priority = int(self.priority_entry.get().strip())
        except ValueError:
            messagebox.showerror("Помилка", "Бал і пріоритет мають бути числами.")
            return

        funding = self.funding_var.get()
        cross_check = self.cross_check_var.get()

        if self.remember_var.get():
            key = self.key_entry.get().strip()
            if key:
                settings.save_gemini_key(key)
        else:
            settings.clear_gemini_key()

        self._analyzing = True
        self.analyze_button.configure(state="disabled", text="Аналізую...")
        self.explain_button.configure(state="disabled")
        self.progress_bar.set(0)
        self.status_label.configure(text="Завантажую сторінку...")
        self._clear_table()
        self.summary_label.configure(text="")

        def on_progress(done: int, total: int, name: str) -> None:
            self._queue.put(("progress", done, total, name))

        def worker() -> None:
            try:
                result = run_analysis(
                    url,
                    score,
                    priority,
                    funding,
                    cross_check=cross_check,
                    on_progress=on_progress if cross_check else None,
                )
                self._queue.put(("done", result))
            except AnalysisError as e:
                self._queue.put(("error", str(e)))
            except Exception as e:  # деградація: GUI ніколи не падає через живий сайт/мережу
                self._queue.put(("error", f"Неочікувана помилка: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    _, done, total, name = item
                    self.progress_bar.set(done / total if total else 0)
                    self.status_label.configure(text=f"Крос-аналіз: {done}/{total} — {name}")
                elif kind == "done":
                    self._on_analysis_done(item[1])
                elif kind == "error":
                    self._on_analysis_error(item[1])
                elif kind == "explain_done":
                    self._on_explain_done(item[1])
                elif kind == "explain_error":
                    self._on_explain_error(item[1])
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _on_analysis_done(self, result: AnalysisResult) -> None:
        self._result = result
        self._analyzing = False
        self.analyze_button.configure(state="normal", text="Аналізувати")
        self.explain_button.configure(state="normal")
        self.progress_bar.set(1)
        self.status_label.configure(text="Готово.")

        v = result.verdict
        lines = [
            f"Напрям: {result.stats.title}",
            f"M (бюджетних місць) = {v.m}    Вердикт v1: {v.verdict.upper()}",
            f"Межі v1: оптимістична {v.optimistic_bound} — песимістична {v.pessimistic_bound}",
        ]
        cc = result.cross_check
        if cc is not None:
            lines.append(
                f"Крос-аналіз v2: очікувана {cc.expected_count:.1f}, межі "
                f"{cc.optimistic_bound}—{cc.pessimistic_bound}, шанс {cc.chance * 100:.0f}% (евристика)"
            )
        for w in result.warnings:
            lines.append(f"⚠ {w}")
        self.summary_label.configure(text="\n".join(lines))

        self._fill_table(result)

    def _fill_table(self, result: AnalysisResult) -> None:
        self._clear_table()
        cc = result.cross_check
        rows: List[List[str]]
        if cc is not None:
            rows = []
            for a in cc.assessments:
                applicant = a.competitor.applicant
                target = (
                    f"{a.best_choice.university} / {a.best_choice.specialty}" if a.best_choice else "—"
                )
                rows.append([applicant.name, f"{applicant.score:g}", str(applicant.priority), a.status, target])
        else:
            rows = [
                [c.applicant.name, f"{c.applicant.score:g}", str(c.applicant.priority), c.category, "—"]
                for c in result.verdict.competitors
            ]

        for row_i, row in enumerate(rows):
            for col_i, (value, width) in enumerate(zip(row, TABLE_WIDTHS)):
                ctk.CTkLabel(self.table_frame, text=value, width=width, anchor="w").grid(
                    row=row_i, column=col_i, sticky="w", padx=4, pady=2
                )

    def _clear_table(self) -> None:
        for widget in self.table_frame.winfo_children():
            widget.destroy()

    def _on_analysis_error(self, message: str) -> None:
        self._analyzing = False
        self.analyze_button.configure(state="normal", text="Аналізувати")
        self.progress_bar.set(0)
        self.status_label.configure(text="Помилка.")
        messagebox.showerror("Помилка аналізу", message)

    # -------------------------------------------------------------- пояснення

    def _start_explain(self) -> None:
        if self._result is None:
            return

        provider = self.provider_var.get()
        api_key = self.key_entry.get().strip()
        if provider == PROVIDER_GEMINI and not api_key:
            messagebox.showerror("Помилка", "Вкажіть ключ Gemini API або оберіть офлайн-режим.")
            return

        self.explain_button.configure(state="disabled", text="Пояснюю...")
        self.explain_text.configure(state="normal")
        self.explain_text.delete("1.0", "end")
        self.explain_text.configure(state="disabled")

        payload = build_depersonalized_payload(self._result)

        def worker() -> None:
            try:
                text = generate_summary(payload, provider=provider, api_key=api_key)
                self._queue.put(("explain_done", text))
            except SummarizeError as e:
                self._queue.put(("explain_error", str(e)))
            except Exception as e:
                self._queue.put(("explain_error", f"Неочікувана помилка: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_explain_done(self, text: str) -> None:
        self.explain_button.configure(state="normal", text="Пояснити")
        self.explain_text.configure(state="normal")
        self.explain_text.insert("1.0", text)
        self.explain_text.configure(state="disabled")

    def _on_explain_error(self, message: str) -> None:
        self.explain_button.configure(state="normal", text="Пояснити")
        messagebox.showerror("Помилка пояснення", message)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
