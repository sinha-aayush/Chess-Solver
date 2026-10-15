"""Next-best-move suggester: play or set up any position and see the top 2 moves.

Rules/move generation: python-chess.  Search: solver.py.  Piece art: fruffers/chessGame (mats/).

    pip install chess pillow
    python app.py path/to/chessGame/mats        (or put the 'mats' folder next to this file)
"""
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog

import chess
from PIL import Image, ImageTk

from solver import Solver, describe_score

SQ = 80
LIGHT, DARK = "#f0d9b5", "#b58863"
SELECT, TARGET, LAST = "#7fc97f", "#8e7cc3", "#cdd26a"
BEST, SECOND = "#e6b800", "#4a90d9"           # outline colours for suggestion #1 and #2
NAMES = {chess.PAWN: "Pawn", chess.KNIGHT: "Knight", chess.BISHOP: "Bishop",
         chess.ROOK: "Rook", chess.QUEEN: "Queen", chess.KING: "King"}


class App:
    def __init__(self, root, mats_dir):
        self.root = root
        root.title("Next Best Move")
        self.board = chess.Board()
        self.selected = None
        self.flipped = False
        self.suggestions = []                 # [(move, score), ...] for the current position
        self.solver = None                    # the running Solver, if any
        self.gen = 0                          # bumps on every position change; stale results are dropped
        self.q = queue.Queue()                # worker thread -> UI thread
        self.images = self._load_images(mats_dir)

        self.canvas = tk.Canvas(root, width=8 * SQ, height=8 * SQ, highlightthickness=0)
        self.canvas.grid(row=0, column=0, padx=8, pady=8)
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Button-3>", lambda e: self._deselect())

        side = tk.Frame(root)
        side.grid(row=0, column=1, sticky="n", padx=8, pady=8)
        self.status = tk.Label(side, font=("Helvetica", 12, "bold"), anchor="w", width=30)
        self.status.pack(anchor="w")
        self.lines = []
        for colour in (BEST, SECOND):
            lab = tk.Label(side, anchor="w", width=30, font=("Courier", 12), fg=colour)
            lab.pack(anchor="w", pady=2)
            self.lines.append(lab)
        self.info = tk.Label(side, anchor="w", width=36, fg="gray40")
        self.info.pack(anchor="w", pady=(2, 10))

        row = tk.Frame(side); row.pack(anchor="w", pady=4)
        tk.Label(row, text="Max depth").pack(side="left")
        self.depth = tk.Spinbox(row, from_=1, to=12, width=3)
        self.depth.delete(0, "end"); self.depth.insert(0, "6"); self.depth.pack(side="left", padx=(4, 12))
        tk.Label(row, text="Seconds").pack(side="left")
        self.seconds = tk.Spinbox(row, from_=1, to=120, width=4)
        self.seconds.delete(0, "end"); self.seconds.insert(0, "5"); self.seconds.pack(side="left", padx=4)
        tk.Button(side, text="Re-analyse", width=26, command=self.analyse).pack(anchor="w", pady=2)
        for text, cmd in [("Undo", self.undo), ("New game", self.new_game),
                          ("Flip board", self.flip), ("Set position (FEN)...", self.set_fen)]:
            tk.Button(side, text=text, command=cmd, width=26).pack(anchor="w", pady=2)

        self._poll()
        self.position_changed()

    # ---------- assets ----------
    def _load_images(self, mats_dir):
        imgs = {}
        for colour, c in ((chess.WHITE, "w"), (chess.BLACK, "b")):
            for pt, name in NAMES.items():
                im = Image.open(os.path.join(mats_dir, f"{c}{name}.png")).convert("RGBA")
                imgs[(pt, colour)] = ImageTk.PhotoImage(im.resize((SQ - 10, SQ - 10), Image.LANCZOS))
        return imgs                            # keep references, or Tk garbage-collects the images

    # ---------- coordinates ----------
    def _xy(self, sq):
        f, r = chess.square_file(sq), chess.square_rank(sq)
        return (7 - f if self.flipped else f) * SQ, (r if self.flipped else 7 - r) * SQ

    def _sq_at(self, x, y):
        col, row = x // SQ, y // SQ
        return chess.square(int(7 - col if self.flipped else col), int(row if self.flipped else 7 - row))

    # ---------- drawing ----------
    def redraw(self):
        c = self.canvas
        c.delete("all")
        last = self.board.peek() if self.board.move_stack else None
        targets = {m.to_square for m in self.board.legal_moves if m.from_square == self.selected}
        for sq in chess.SQUARES:
            x, y = self._xy(sq)
            colour = LIGHT if (chess.square_file(sq) + chess.square_rank(sq)) % 2 else DARK
            if last and sq in (last.from_square, last.to_square):
                colour = LAST
            if sq == self.selected:
                colour = SELECT
            elif sq in targets:
                colour = TARGET
            c.create_rectangle(x, y, x + SQ, y + SQ, fill=colour, outline="")
            p = self.board.piece_at(sq)
            if p:
                c.create_image(x + SQ // 2, y + SQ // 2, image=self.images[(p.piece_type, p.color)])
        # suggestion outlines: draw #2 first so #1 sits on top where they overlap
        for (move, _), colour in reversed(list(zip(self.suggestions, (BEST, SECOND)))):
            for sq in (move.from_square, move.to_square):
                x, y = self._xy(sq)
                c.create_rectangle(x + 3, y + 3, x + SQ - 3, y + SQ - 3, outline=colour, width=4)
        if self.board.is_check():
            x, y = self._xy(self.board.king(self.board.turn))
            c.create_oval(x + 6, y + 6, x + SQ - 6, y + SQ - 6, outline="red", width=3)

    def _status_text(self):
        b = self.board
        if b.is_checkmate():
            return f"Checkmate - {'Black' if b.turn == chess.WHITE else 'White'} wins"
        if b.is_stalemate() or b.is_insufficient_material():
            return "Draw"
        return f"{'White' if b.turn == chess.WHITE else 'Black'} to move" + (" (check)" if b.is_check() else "")

    def _show_suggestions(self, board, suggestions, text=""):
        self.suggestions = suggestions
        for i, lab in enumerate(self.lines):
            if i < len(suggestions):
                m, s = suggestions[i]
                lab.config(text=f"{i + 1}. {board.san(m):<8} {describe_score(s)}")
            else:
                lab.config(text="")
        self.info.config(text=text)
        self.redraw()

    # ---------- input ----------
    def _deselect(self):
        self.selected = None
        self.redraw()

    def on_click(self, e):
        if self.board.is_game_over():
            return
        sq = self._sq_at(e.x, e.y)
        if self.selected is not None:
            move = self._build_move(self.selected, sq)
            if move:
                self.board.push(move)
                self.selected = None
                self.position_changed()
                return
        p = self.board.piece_at(sq)
        self.selected = sq if p and p.color == self.board.turn else None
        self.redraw()

    def _build_move(self, frm, to):
        p = self.board.piece_at(frm)
        promo = chess.QUEEN if p and p.piece_type == chess.PAWN and chess.square_rank(to) in (0, 7) else None
        m = chess.Move(frm, to, promotion=promo)         # auto-queen for now
        return m if m in self.board.legal_moves else None

    # ---------- analysis (worker thread -> queue -> UI thread; Tk itself is not thread-safe) ----------
    def position_changed(self):
        self.status.config(text=self._status_text())
        self._show_suggestions(self.board, [])
        self.analyse()

    def analyse(self):
        if self.solver:
            self.solver.stop()                           # abort the stale search
        self.gen += 1
        gen, board = self.gen, self.board.copy()
        if board.is_game_over():
            self.info.config(text="Game over")
            return
        solver = self.solver = Solver()                  # fresh solver per search: no shared stop flag
        depth, secs = int(self.depth.get()), float(self.seconds.get())
        self.info.config(text="Thinking...")

        def progress(d, result, nodes):
            self.q.put((gen, board, result, f"depth {d} - {nodes:,} nodes"))

        def work():
            res = solver.top_moves(board, 2, depth, secs, progress)
            self.q.put((gen, board, res, "done"))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                gen, board, result, text = self.q.get_nowait()
                if gen == self.gen and result:
                    self._show_suggestions(board, result, text if text != "done" else self.info.cget("text") + " (final)")
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    # ---------- controls ----------
    def undo(self):
        if self.board.move_stack:
            self.board.pop()
            self.selected = None
            self.position_changed()

    def new_game(self):
        self.board = chess.Board()
        self.selected = None
        self.position_changed()

    def flip(self):
        self.flipped = not self.flipped
        self.redraw()

    def set_fen(self):
        fen = simpledialog.askstring("Set position", "Paste a FEN:")
        if fen:
            try:
                self.board = chess.Board(fen.strip())
            except ValueError as err:
                messagebox.showerror("Bad FEN", str(err))
                return
            self.selected = None
            self.position_changed()


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    mats = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "mats")
    if not os.path.isdir(mats):
        sys.exit(f"Piece images not found at {mats!r}. Copy the repo's 'mats' folder here "
                 "or run: python app.py path/to/chessGame/mats")
    root = tk.Tk()
    App(root, mats)
    root.mainloop()
