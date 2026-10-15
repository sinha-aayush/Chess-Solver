"""Chess search engine: iterative-deepening alpha-beta (negamax) on top of python-chess.

No Tkinter in here, so it can be tested and reused on its own.
"""
import time
import chess

MATE = 100_000
PIECE_VALUE = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
               chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}

# Small piece-square bonuses (from White's view, a1=0 ... h8=63). Mirrored for Black.
_CENTER = [0, 0, 5, 10, 10, 5, 0, 0]
def _pst(piece_type, sq):
    f, r = chess.square_file(sq), chess.square_rank(sq)
    if piece_type == chess.PAWN:
        return r * 4 + (_CENTER[f] * 2 if r < 5 else 0)
    if piece_type in (chess.KNIGHT, chess.BISHOP):
        return _CENTER[f] + _CENTER[r]
    if piece_type == chess.KING:
        return 0
    return _CENTER[f] // 2


def _key(board):
    return board._transposition_key() if hasattr(board, '_transposition_key') else board.fen()


class Timeout(Exception):
    pass


class Solver:
    def __init__(self):
        self.nodes = 0
        self._deadline = 0.0
        self._stop = False

    def stop(self):
        """Call from another thread to abort a running search."""
        self._stop = True

    # ---------- evaluation (from side-to-move's perspective) ----------
    def evaluate(self, board):
        score = 0
        for sq, p in board.piece_map().items():
            v = PIECE_VALUE[p.piece_type]
            s = sq if p.color == chess.WHITE else chess.square_mirror(sq)
            v += _pst(p.piece_type, s)
            score += v if p.color == chess.WHITE else -v
        return score if board.turn == chess.WHITE else -score

    # ---------- move ordering ----------
    def _ordered(self, board, tt_move=None, captures_only=False):
        def key(m):
            if m == tt_move:
                return 10_000
            k = 0
            if board.is_capture(m):
                victim = board.piece_type_at(m.to_square) or chess.PAWN  # en passant
                k += 10 * PIECE_VALUE[victim] - PIECE_VALUE[board.piece_type_at(m.from_square)]
            if m.promotion:
                k += PIECE_VALUE[m.promotion]
            if board.gives_check(m):
                k += 50
            return k
        moves = board.generate_legal_captures() if captures_only else board.legal_moves
        return sorted(moves, key=key, reverse=True)

    def _check_time(self):
        if self._stop or time.time() > self._deadline:
            raise Timeout

    # ---------- search ----------
    def _quiesce(self, board, alpha, beta):
        self.nodes += 1
        if self.nodes & 2047 == 0:
            self._check_time()
        stand = self.evaluate(board)
        if stand >= beta:
            return beta
        alpha = max(alpha, stand)
        for m in self._ordered(board, captures_only=True):
            board.push(m)
            score = -self._quiesce(board, -beta, -alpha)
            board.pop()
            if score >= beta:
                return beta
            alpha = max(alpha, score)
        return alpha

    def _negamax(self, board, depth, alpha, beta, ply):
        self.nodes += 1
        if self.nodes & 2047 == 0:
            self._check_time()
        if board.is_checkmate():
            return -MATE + ply            # prefer faster mates
        if (board.is_stalemate() or board.is_insufficient_material()
                or board.can_claim_threefold_repetition() or board.halfmove_clock >= 100):
            return 0
        if depth <= 0:
            return self._quiesce(board, alpha, beta)
        for m in self._ordered(board, tt_move=self._pv.get(_key(board))):
            board.push(m)
            score = -self._negamax(board, depth - 1, -beta, -alpha, ply + 1)
            board.pop()
            if score >= beta:
                return beta
            if score > alpha:
                alpha = score
                self._pv[_key(board)] = m
        return alpha

    def top_moves(self, board, n=2, max_depth=6, time_limit=5.0, on_progress=None):
        """Return the n best moves as [(move, score_cp), ...], best first.
        Scores are from the side-to-move's view; mate scores are +/-(MATE - plies).

        Root search is windowed correctly for multi-PV: alpha is the n-th best score
        found so far, so every move that can make the top n gets an exact score, and
        everything else is pruned."""
        board = board.copy()
        self.nodes, self._stop = 0, False
        self._deadline = time.time() + time_limit
        self._pv = {}
        legal = list(board.legal_moves)
        if not legal:
            return []
        n = min(n, len(legal))
        result = [(m, 0) for m in legal[:n]]           # fallback if we time out at depth 1
        prev_order = [m for m, _ in result]
        for depth in range(1, max_depth + 1):
            try:
                scored = []                              # exact scores of the current top n
                for m in sorted(self._ordered(board), key=lambda m: m not in prev_order):
                    alpha = scored[n - 1][1] if len(scored) >= n else -MATE - 1
                    board.push(m)
                    score = -self._negamax(board, depth - 1, -(MATE + 1), -alpha, 1)
                    board.pop()
                    if score > alpha:                    # makes (or improves) the top n
                        scored.append((m, score))
                        scored.sort(key=lambda x: -x[1])
                        scored = scored[:n]
                result, prev_order = scored, [m for m, _ in scored]
                if on_progress:
                    on_progress(depth, result, self.nodes)
                if result and result[0][1] > MATE - 100:  # forced mate found
                    break
            except Timeout:
                break
        return result


def describe_score(score):
    if abs(score) > MATE - 100:
        n = (MATE - abs(score) + 1) // 2
        return f"{'+' if score > 0 else '-'}M{n}"
    return f"{score / 100:+.2f}"
